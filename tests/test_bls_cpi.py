from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finance_data.bls import (
    BLS_V1_URL,
    BLS_V2_URL,
    BLSPublicDataAdapter,
    CPI_U_ALL_ITEMS_SERIES,
    FetchResult,
)
from finance_data.datasets import bls_cpi
from finance_data.registry import SUPPORTED_DATASETS


def observation(
    year: str,
    period: str,
    value: str,
    *,
    latest: bool = False,
    unavailable: bool = False,
) -> dict[str, object]:
    names = {
        "M01": "January",
        "M02": "February",
        "M03": "March",
        "M04": "April",
        "M05": "May",
        "M06": "June",
        "M07": "July",
        "M08": "August",
        "M09": "September",
        "M10": "October",
        "M11": "November",
        "M12": "December",
    }
    row: dict[str, object] = {
        "year": year,
        "period": period,
        "periodName": names[period],
        "value": value,
        "footnotes": (
            [{"code": "X", "text": "Data unavailable due to the 2025 lapse in appropriations"}]
            if unavailable
            else [{}]
        ),
    }
    if latest:
        row["latest"] = "true"
    return row


def api_response(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "REQUEST_SUCCEEDED",
        "responseTime": 1,
        "message": [],
        "Results": {
            "series": [
                {
                    "seriesID": CPI_U_ALL_ITEMS_SERIES,
                    "data": rows,
                }
            ]
        },
    }


class FakeAdapter:
    def __init__(self, rows: list[dict[str, object]], *, registered: bool = False) -> None:
        self.rows = rows
        self.registered = registered

    def fetch_series(self, series_id: str, *, start_year: int, end_year: int) -> FetchResult:
        if series_id != CPI_U_ALL_ITEMS_SERIES:
            raise AssertionError(series_id)
        selected = [
            {"series_id": series_id, **row}
            for row in self.rows
            if start_year <= int(str(row["year"])) <= end_year
        ]
        return FetchResult(
            records=selected,
            request={
                "series_id": series_id,
                "start_year": start_year,
                "end_year": end_year,
                "registered": self.registered,
            },
            pages=1,
            source_total_count=len(selected),
        )


class BLSAdapterTests(unittest.TestCase):
    def test_unregistered_adapter_preserves_missing_value_and_footnote(self):
        calls: list[tuple[str, dict[str, object]]] = []

        def post(url: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((url, payload))
            return api_response([observation("2025", "M10", "-", unavailable=True)])

        adapter = BLSPublicDataAdapter(registration_key=None, post_json=post)
        result = adapter.fetch_series(CPI_U_ALL_ITEMS_SERIES, start_year=2025, end_year=2025)
        self.assertEqual(calls[0][0], BLS_V1_URL)
        self.assertNotIn("registrationkey", calls[0][1])
        self.assertEqual(result.records[0]["value"], "-")
        self.assertEqual(result.records[0]["footnotes"][0]["code"], "X")

    def test_registered_adapter_uses_v2_without_exposing_key_in_metadata(self):
        calls: list[tuple[str, dict[str, object]]] = []

        def post(url: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((url, payload))
            return api_response([observation("2026", "M07", "333.918", latest=True)])

        adapter = BLSPublicDataAdapter(registration_key="secret-key", post_json=post)
        result = adapter.fetch_series(CPI_U_ALL_ITEMS_SERIES, start_year=2026, end_year=2026)
        self.assertEqual(calls[0][0], BLS_V2_URL)
        self.assertEqual(calls[0][1]["registrationkey"], "secret-key")
        self.assertNotIn("registrationkey", result.request)
        self.assertTrue(result.request["registered"])

    def test_adapter_rejects_windows_longer_than_ten_years(self):
        adapter = BLSPublicDataAdapter(post_json=lambda _url, _payload: api_response([]))
        with self.assertRaises(ValueError):
            adapter.fetch_series(CPI_U_ALL_ITEMS_SERIES, start_year=1913, end_year=1923)


class BLSCPIDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            observation("2025", "M09", "324.800"),
            observation("2025", "M10", "-", unavailable=True),
            observation("2025", "M11", "324.122"),
            observation("2026", "M07", "333.918", latest=True),
        ]

    def test_unavailable_month_is_preserved_not_dropped_or_zeroed(self):
        record = {"series_id": CPI_U_ALL_ITEMS_SERIES, **self.rows[1]}
        normalized = bls_cpi.normalize_source_record(record)
        self.assertEqual(normalized["period"], "2025-10")
        self.assertEqual(normalized["index_value"], "null")
        self.assertEqual(normalized["observation_status"], "unavailable")
        self.assertEqual(normalized["footnote_codes"], "X")
        self.assertIn("lapse in appropriations", normalized["footnote_text"])

    def test_sync_is_idempotent_and_dataset_is_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = bls_cpi.sync(
                root,
                full=True,
                adapter=FakeAdapter(self.rows),
                retrieved_at=datetime(2026, 9, 6, 1, 0, tzinfo=timezone.utc),
            )
            second = bls_cpi.sync(
                root,
                adapter=FakeAdapter(self.rows),
                retrieved_at=datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(first.normalized_records, 4)
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            self.assertEqual(second.changed_partitions, 0)
            report = bls_cpi.validate(root)
            self.assertEqual(report["unavailable_records"], 1)
            self.assertEqual(report["status"], "PASS")
        self.assertIn(bls_cpi.DATASET_ID, SUPPORTED_DATASETS)

    def test_overlap_correction_updates_canonical_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bls_cpi.sync(
                root,
                full=True,
                adapter=FakeAdapter(self.rows),
                retrieved_at=datetime(2026, 9, 6, 1, 0, tzinfo=timezone.utc),
            )
            corrected = [dict(row) for row in self.rows]
            corrected[2] = observation("2025", "M11", "324.123")
            summary = bls_cpi.sync(
                root,
                adapter=FakeAdapter(corrected),
                retrieved_at=datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(summary.raw_snapshot_created)
            self.assertEqual(summary.changed_partitions, 1)
            rows = bls_cpi.rebuild(root)
            by_period = {row["period"]: row for row in rows}
            self.assertEqual(by_period["2025-11"]["index_value"], "324.123")

    def test_year_windows_respect_unregistered_limit(self):
        windows = bls_cpi._year_windows(1913, 2026)
        self.assertEqual(windows[0], (1913, 1922))
        self.assertEqual(windows[-1], (2023, 2026))
        self.assertTrue(all(end - start <= 9 for start, end in windows))
        self.assertLessEqual(len(windows), 25)


if __name__ == "__main__":
    unittest.main()
