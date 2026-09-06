from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from finance_data.datasets import nyfed_sofr
from finance_data.nyfed import FetchResult, NYFedMarketsAdapter, NYFedMarketsError
from finance_data.registry import SUPPORTED_DATASETS


def api_row(
    period: str,
    rate: float,
    *,
    volume: int = 2000,
    revision: str = "",
    footnote: str | None = None,
    percentiles_available: bool = True,
) -> dict[str, object]:
    percentiles: dict[str, object]
    if percentiles_available:
        percentiles = {
            "percentPercentile1": rate - 0.06,
            "percentPercentile25": rate - 0.02,
            "percentPercentile75": rate + 0.04,
            "percentPercentile99": rate + 0.08,
        }
    else:
        percentiles = {
            "percentPercentile1": "NA",
            "percentPercentile25": "NA",
            "percentPercentile75": "NA",
            "percentPercentile99": "NA",
        }
    return {
        "effectiveDate": period,
        "type": "SOFR",
        "percentRate": rate,
        **percentiles,
        "volumeInBillions": volume,
        "revisionIndicator": revision,
        **({"footnoteId": footnote} if footnote is not None else {}),
    }


class FakeAdapter:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[date, date | None]] = []

    def fetch_sofr(self, *, start_date: date, end_date: date | None = None) -> FetchResult:
        self.calls.append((start_date, end_date))
        selected = [
            {
                "effective_date": row["effectiveDate"],
                "type": row["type"],
                "percent_rate": row["percentRate"],
                "percentile_1": row["percentPercentile1"],
                "percentile_25": row["percentPercentile25"],
                "percentile_75": row["percentPercentile75"],
                "percentile_99": row["percentPercentile99"],
                "volume_billions": row["volumeInBillions"],
                "revision_indicator": row.get("revisionIndicator") or "",
                "footnote_id": row.get("footnoteId"),
            }
            for row in self.rows
            if date.fromisoformat(str(row["effectiveDate"])) >= start_date
            and (end_date is None or date.fromisoformat(str(row["effectiveDate"])) <= end_date)
        ]
        return FetchResult(
            records=selected,
            request={"start_date": start_date.isoformat(), "access": "fixture"},
            pages=1,
            source_total_count=len(selected),
        )


class NYFedAdapterTests(unittest.TestCase):
    def test_adapter_requests_rate_and_volume_and_preserves_revision_fields(self):
        requested: list[str] = []

        def get_json(url: str) -> dict[str, object]:
            requested.append(url)
            return {"refRates": [api_row("2026-09-03", 3.66, volume=2949, revision="R", footnote="1")]}

        result = NYFedMarketsAdapter(get_json=get_json).fetch_sofr(
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 3)
        )
        parsed = urlparse(requested[0])
        query = parse_qs(parsed.query)
        self.assertEqual(query["type"], ["rate,volume"])
        self.assertEqual(query["startDate"], ["2026-09-01"])
        self.assertEqual(query["endDate"], ["2026-09-03"])
        self.assertEqual(result.records[0]["volume_billions"], 2949)
        self.assertEqual(result.records[0]["revision_indicator"], "R")
        self.assertEqual(result.records[0]["footnote_id"], "1")

    def test_adapter_rejects_missing_required_source_field(self):
        row = api_row("2026-09-03", 3.66)
        del row["volumeInBillions"]
        adapter = NYFedMarketsAdapter(get_json=lambda _url: {"refRates": [row]})
        with self.assertRaises(NYFedMarketsError):
            adapter.fetch_sofr(start_date=date(2026, 9, 3), end_date=date(2026, 9, 3))


class NYFedSOFRDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            api_row("2026-08-28", 3.65, volume=2808),
            api_row("2026-09-02", 3.67, volume=2910),
            api_row("2026-09-03", 3.66, volume=2949),
        ]

    def test_sync_is_idempotent_and_dataset_is_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = FakeAdapter(self.rows)
            first = nyfed_sofr.sync(
                root,
                full=True,
                adapter=adapter,
                retrieved_at=datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc),
            )
            second = nyfed_sofr.sync(
                root,
                adapter=adapter,
                retrieved_at=datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(first.normalized_records, 3)
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            self.assertEqual(second.changed_partitions, 0)
            self.assertEqual(nyfed_sofr.validate(root)["status"], "PASS")
        self.assertIn(nyfed_sofr.DATASET_ID, SUPPORTED_DATASETS)

    def test_source_correction_replaces_canonical_record_and_preserves_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nyfed_sofr.sync(
                root,
                full=True,
                adapter=FakeAdapter(self.rows),
                retrieved_at=datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc),
            )
            corrected = list(self.rows)
            corrected[-1] = api_row("2026-09-03", 3.67, volume=2950, revision="R", footnote="1")
            summary = nyfed_sofr.sync(
                root,
                adapter=FakeAdapter(corrected),
                overlap_days=21,
                retrieved_at=datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(summary.raw_snapshot_created)
            rows = nyfed_sofr.rebuild(root)
            by_period = {row["period"]: row for row in rows}
            self.assertEqual(by_period["2026-09-03"]["sofr_percent"], "3.67")
            self.assertEqual(by_period["2026-09-03"]["volume_billions"], "2950")
            self.assertEqual(by_period["2026-09-03"]["revision_indicator"], "R")
            self.assertEqual(by_period["2026-09-03"]["footnote_id"], "1")

    def test_unavailable_percentiles_are_preserved_as_null_with_footnote(self):
        row = api_row(
            "2021-08-05",
            0.05,
            volume=901,
            footnote="2",
            percentiles_available=False,
        )
        source = FakeAdapter([row]).fetch_sofr(start_date=date(2018, 4, 3)).records[0]
        normalized = nyfed_sofr.normalize_source_record(source)
        self.assertEqual(normalized["sofr_percent"], "0.05")
        self.assertEqual(normalized["percentile_1_percent"], "null")
        self.assertEqual(normalized["percentile_25_percent"], "null")
        self.assertEqual(normalized["percentile_75_percent"], "null")
        self.assertEqual(normalized["percentile_99_percent"], "null")
        self.assertEqual(normalized["volume_billions"], "901")
        self.assertEqual(normalized["footnote_id"], "2")

    def test_unavailable_percentiles_without_footnote_are_rejected(self):
        row = api_row("2021-08-05", 0.05, volume=901, percentiles_available=False)
        source = FakeAdapter([row]).fetch_sofr(start_date=date(2018, 4, 3)).records[0]
        with self.assertRaises(nyfed_sofr.DatasetValidationError):
            nyfed_sofr.validate_source_record(source)

    def test_invalid_percentile_order_is_rejected(self):
        record = {
            "effective_date": "2026-09-03",
            "type": "SOFR",
            "percent_rate": 3.66,
            "percentile_1": 3.70,
            "percentile_25": 3.64,
            "percentile_75": 3.70,
            "percentile_99": 3.74,
            "volume_billions": 2949,
            "revision_indicator": "",
            "footnote_id": None,
        }
        with self.assertRaises(nyfed_sofr.DatasetValidationError):
            nyfed_sofr.validate_source_record(record)


if __name__ == "__main__":
    unittest.main()
