from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finance_data.datasets.treasury_debt import (
    DatasetValidationError,
    normalize_source_record,
    sync,
    validate,
)
from finance_data.storage import read_normalized_rows, store_raw_snapshot
from finance_data.treasury import FetchResult


def row(day: str, public: str, intragov: str, total: str, line: str = "1") -> dict[str, str]:
    return {
        "record_date": day,
        "debt_held_public_amt": public,
        "intragov_hold_amt": intragov,
        "tot_pub_debt_out_amt": total,
        "src_line_nbr": line,
    }


BASE_ROWS = [
    row("2024-01-02", "27000000000000.00", "7000000000000.00", "34000000000000.00"),
    row("2024-01-03", "27000000000001.11", "7000000000002.22", "34000000000003.33"),
    row("2024-01-04", "27000000000004.44", "7000000000005.55", "34000000000009.99"),
]


class FakeAdapter:
    def __init__(self, records: list[dict[str, str]]) -> None:
        self.records = records
        self.start_dates = []

    def fetch_debt_to_penny(self, *, start_date=None, page_size=5000):
        self.start_dates.append(start_date)
        records = self.records
        if start_date is not None:
            records = [r for r in records if r["record_date"] >= start_date.isoformat()]
        return FetchResult(
            records=records,
            request={"start_date": start_date.isoformat() if start_date else None},
            pages=1,
            source_total_count=len(records),
        )


class TreasuryDebtTests(unittest.TestCase):
    def test_normalization_preserves_exact_decimal_strings(self):
        normalized = normalize_source_record(BASE_ROWS[1])
        self.assertEqual(normalized["debt_held_by_public"], "27000000000001.11")
        self.assertEqual(normalized["total_public_debt_outstanding"], "34000000000003.33")
        self.assertEqual(len(normalized["source_record_sha256"]), 64)

    def test_invalid_total_fails(self):
        invalid = row("2024-01-02", "1.00", "2.00", "4.00")
        with self.assertRaises(DatasetValidationError):
            normalize_source_record(invalid)

    def test_historical_total_only_record_preserves_null_components(self):
        historical = row("1993-04-01", "null", "null", "4225873987843.44")
        normalized = normalize_source_record(historical)
        self.assertEqual(normalized["debt_held_by_public"], "null")
        self.assertEqual(normalized["intragovernmental_holdings"], "null")
        self.assertEqual(normalized["total_public_debt_outstanding"], "4225873987843.44")

    def test_missing_component_after_known_coverage_start_fails(self):
        unexpected = row("2005-03-31", "null", "null", "7776939047670.14")
        with self.assertRaises(DatasetValidationError):
            normalize_source_record(unexpected)

    def test_one_sided_component_null_fails(self):
        invalid = row("1999-01-04", "null", "100.00", "100.00")
        with self.assertRaises(DatasetValidationError):
            normalize_source_record(invalid)

    def test_sync_is_idempotent_when_source_payload_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = FakeAdapter(BASE_ROWS)
            first = sync(
                root,
                full=True,
                adapter=adapter,
                retrieved_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
            )
            second = sync(
                root,
                full=True,
                adapter=adapter,
                retrieved_at=datetime(2024, 1, 6, tzinfo=timezone.utc),
            )
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            self.assertEqual(second.changed_partitions, 0)
            self.assertEqual(validate(root)["status"], "PASS")

    def test_incremental_overlap_and_source_correction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initial = FakeAdapter(BASE_ROWS)
            sync(root, full=True, adapter=initial, retrieved_at=datetime(2024, 1, 5, tzinfo=timezone.utc))

            corrected = BASE_ROWS[:2] + [
                row("2024-01-04", "27000000000006.00", "7000000000006.00", "34000000000012.00"),
                row("2024-01-05", "27000000000007.00", "7000000000007.00", "34000000000014.00"),
            ]
            adapter = FakeAdapter(corrected)
            result = sync(
                root,
                full=False,
                overlap_days=10,
                adapter=adapter,
                retrieved_at=datetime(2024, 1, 8, tzinfo=timezone.utc),
            )
            self.assertIsNotNone(adapter.start_dates[0])
            self.assertTrue(result.raw_snapshot_created)
            rows = read_normalized_rows(root)
            self.assertEqual(rows[-2]["total_public_debt_outstanding"], "34000000000012.00")
            self.assertEqual(rows[-1]["period"], "2024-01-05")
            self.assertEqual(validate(root)["records"], 4)

    def test_raw_snapshot_checksum_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot, _ = store_raw_snapshot(
                root,
                BASE_ROWS,
                request={},
                pages=1,
                source_total_count=3,
                retrieved_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
            )
            with (snapshot / "records.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(BASE_ROWS[0]) + "\n")
            with self.assertRaises(ValueError):
                validate(root)


class FakeHTTPResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        import io
        self._buffer = io.BytesIO(json.dumps(self.payload).encode("utf-8"))
        self.read = self._buffer.read
        return self

    def __exit__(self, exc_type, exc, tb):
        self._buffer.close()
        return False


class TreasuryAdapterTests(unittest.TestCase):
    def test_adapter_requests_explicit_fields_and_paginates(self):
        from urllib.parse import parse_qs, urlparse
        from finance_data.treasury import DEBT_TO_PENNY_FIELDS, TreasuryFiscalDataAdapter

        seen_urls: list[str] = []
        pages = {
            "1": {
                "data": BASE_ROWS[:2],
                "meta": {"total-pages": 2, "total-count": 3},
            },
            "2": {
                "data": BASE_ROWS[2:],
                "meta": {"total-pages": 2, "total-count": 3},
            },
        }

        def opener(request, timeout=30):
            seen_urls.append(request.full_url)
            query = parse_qs(urlparse(request.full_url).query)
            return FakeHTTPResponse(pages[query["page[number]"][0]])

        adapter = TreasuryFiscalDataAdapter(opener=opener)
        result = adapter.fetch_debt_to_penny(page_size=2)
        self.assertEqual(result.records, BASE_ROWS)
        self.assertEqual(result.pages, 2)
        self.assertEqual(result.source_total_count, 3)
        query = parse_qs(urlparse(seen_urls[0]).query)
        self.assertEqual(query["fields"][0], ",".join(DEBT_TO_PENNY_FIELDS))
        self.assertEqual(query["sort"][0], "record_date")
        self.assertEqual(query["format"][0], "json")


if __name__ == "__main__":
    unittest.main()
