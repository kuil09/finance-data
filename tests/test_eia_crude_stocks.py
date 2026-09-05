from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from finance_data.datasets import eia_crude_stocks
from finance_data.eia import (
    COMMERCIAL_CRUDE_STOCKS_SERIES,
    EIARightsError,
    EIAOpenDataAdapter,
    FetchResult,
)
from finance_data.registry import SUPPORTED_DATASETS


def series_object(*, copyright_value: str = "None") -> dict[str, object]:
    return {
        "series_id": COMMERCIAL_CRUDE_STOCKS_SERIES,
        "name": "U.S. Ending Stocks excluding SPR of Crude Oil, Weekly",
        "units": "Thousand Barrels",
        "f": "W",
        "unitsshort": "Mbbl",
        "description": "U.S. Ending Stocks excluding SPR of Crude Oil ",
        "copyright": copyright_value,
        "source": "EIA, U.S. Energy Information Administration",
        "iso3166": "USA",
        "geography": "USA",
        "start": "19820820",
        "end": "20260828",
        "last_updated": "2026-09-02T17:59:48-04:00",
        "data": [["20260828", 424460], ["20260821", 428910]],
    }


def bulk_fetcher(value: dict[str, object]):
    def fetch(_url: str, destination: Path) -> dict[str, str]:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("PET.txt", json.dumps(value, separators=(",", ":")) + "\n")
        return {"last-modified": "Wed, 02 Sep 2026 21:59:48 GMT"}

    return fetch


class FakeAdapter:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def fetch_series(self, series_id: str, *, start_date=None) -> FetchResult:
        self.assert_series(series_id)
        selected = self.records
        if start_date is not None:
            selected = [
                row
                for row in selected
                if datetime.strptime(str(row["period"]), "%Y%m%d").date() >= start_date
            ]
        return FetchResult(
            records=list(selected),
            request={"series_id": series_id, "access": "fixture"},
            pages=1,
            source_total_count=len(self.records),
        )

    @staticmethod
    def assert_series(series_id: str) -> None:
        if series_id != COMMERCIAL_CRUDE_STOCKS_SERIES:
            raise AssertionError(series_id)


class EIAAdapterTests(unittest.TestCase):
    def test_bulk_adapter_reads_and_filters_official_series(self):
        adapter = EIAOpenDataAdapter(fetch_to_path=bulk_fetcher(series_object()))
        result = adapter.fetch_series(
            COMMERCIAL_CRUDE_STOCKS_SERIES,
            start_date=datetime(2026, 8, 25).date(),
        )
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0]["period"], "20260828")
        self.assertEqual(result.records[0]["value"], 424460)
        self.assertEqual(result.source_total_count, 2)
        metadata = result.request["series_metadata"]
        self.assertEqual(metadata["copyright"], "None")
        self.assertNotIn("data", metadata)

    def test_bulk_adapter_rejects_third_party_copyright(self):
        adapter = EIAOpenDataAdapter(
            fetch_to_path=bulk_fetcher(series_object(copyright_value="Third Party"))
        )
        with self.assertRaises(EIARightsError):
            adapter.fetch_series(COMMERCIAL_CRUDE_STOCKS_SERIES)


class EIACrudeStocksDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            {"period": "20260821", "series_id": COMMERCIAL_CRUDE_STOCKS_SERIES, "value": 428910},
            {"period": "20260828", "series_id": COMMERCIAL_CRUDE_STOCKS_SERIES, "value": 424460},
        ]

    def test_sync_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = eia_crude_stocks.sync(
                root,
                full=True,
                adapter=FakeAdapter(self.records),
                retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )
            second = eia_crude_stocks.sync(
                root,
                adapter=FakeAdapter(self.records),
                retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            )
            self.assertEqual(first.normalized_records, 2)
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            self.assertEqual(second.changed_partitions, 0)
            self.assertEqual(eia_crude_stocks.validate(root)["status"], "PASS")

    def test_overlap_correction_updates_canonical_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            eia_crude_stocks.sync(
                root,
                full=True,
                adapter=FakeAdapter(self.records),
                retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )
            corrected = [dict(row) for row in self.records]
            corrected[-1]["value"] = 424461
            summary = eia_crude_stocks.sync(
                root,
                adapter=FakeAdapter(corrected),
                retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            )
            self.assertTrue(summary.raw_snapshot_created)
            self.assertEqual(summary.changed_partitions, 1)
            rows = eia_crude_stocks.rebuild(root)
            self.assertEqual(rows[-1]["stock_thousand_barrels"], "424461")

    def test_dataset_is_registered(self):
        self.assertIn(eia_crude_stocks.DATASET_ID, SUPPORTED_DATASETS)


if __name__ == "__main__":
    unittest.main()
