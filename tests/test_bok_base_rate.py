from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from finance_data.bok_ecos import ECOSAdapter, FetchResult
from finance_data.cli import build_parser
from finance_data.datasets import bok_base_rate
from finance_data.registry import SUPPORTED_DATASETS


def ecos_api_row(period: str, value: str) -> dict[str, object]:
    return {
        "TIME": period,
        "STAT_CODE": "722Y001",
        "STAT_NAME": "1.3.1. 한국은행 기준금리 및 여수신금리",
        "ITEM_CODE1": "0101000",
        "ITEM_NAME1": "한국은행 기준금리",
        "UNIT_NAME": "연%",
        "DATA_VALUE": value,
        "ITEM_CODE2": None,
        "ITEM_CODE3": None,
        "ITEM_CODE4": None,
        "ITEM_NAME2": None,
        "ITEM_NAME3": None,
        "ITEM_NAME4": None,
        "WGT": None,
    }


def source_record(period: str, value: str) -> dict[str, object]:
    return {
        "time": period,
        "stat_code": "722Y001",
        "stat_name": "1.3.1. 한국은행 기준금리 및 여수신금리",
        "item_code": "0101000",
        "item_name": "한국은행 기준금리",
        "unit_name": "연%",
        "data_value": value,
    }


class FakeAdapter:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def fetch_base_rate_monthly(
        self, *, start_time: str = "199905", end_time: str | None = None
    ) -> FetchResult:
        self.calls.append(start_time)
        selected = [
            dict(row)
            for row in self.rows
            if str(row["time"]) >= start_time
            and (end_time is None or str(row["time"]) <= end_time)
        ]
        return FetchResult(
            records=selected,
            request={
                "access": "fixture",
                "registered": False,
                "page_size": 10,
                "stat_code": "722Y001",
                "item_code": "0101000",
                "cycle": "M",
                "start_time": start_time,
                "end_time": end_time or "fixture",
            },
            pages=max(1, (len(selected) + 9) // 10),
            source_total_count=len(selected),
        )


class ECOSAdapterTests(unittest.TestCase):
    def test_sample_access_paginates_ten_rows_per_request(self):
        rows = [ecos_api_row(f"2025{month:02d}", "3.0") for month in range(1, 13)]
        requested: list[str] = []

        def get_json(url: str) -> dict[str, object]:
            requested.append(url)
            parts = urlparse(url).path.strip("/").split("/")
            kr_index = parts.index("kr")
            start = int(parts[kr_index + 1])
            end = int(parts[kr_index + 2])
            selected = rows[start - 1 : end]
            return {"StatisticSearch": {"list_total_count": 12, "row": selected}}

        result = ECOSAdapter(get_json=get_json).fetch_base_rate_monthly(
            start_time="202501", end_time="202512"
        )
        self.assertEqual(result.source_total_count, 12)
        self.assertEqual(result.pages, 2)
        self.assertEqual(len(result.records), 12)
        self.assertEqual(result.request["page_size"], 10)
        self.assertFalse(result.request["registered"])
        self.assertIn("/sample/", requested[0])
        self.assertIn("/1/10/", requested[0])
        self.assertIn("/11/12/", requested[1])

    def test_registered_key_is_used_for_access_but_not_persisted_in_request_metadata(self):
        rows = [ecos_api_row("202601", "2.5")]
        requested: list[str] = []

        def get_json(url: str) -> dict[str, object]:
            requested.append(url)
            return {"StatisticSearch": {"list_total_count": 1, "row": rows}}

        result = ECOSAdapter(api_key="secret-key", get_json=get_json).fetch_base_rate_monthly(
            start_time="202601", end_time="202601"
        )
        self.assertIn("/secret-key/", requested[0])
        self.assertTrue(result.request["registered"])
        self.assertEqual(result.request["page_size"], 1000)
        self.assertNotIn("secret-key", str(result.request))


class BOKBaseRateDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            source_record("202601", "2.50"),
            source_record("202602", "2.50"),
            source_record("202603", "2.50"),
        ]

    def test_sync_is_idempotent_registered_and_rebuildable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = FakeAdapter(self.rows)
            first = bok_base_rate.sync(
                root,
                full=True,
                adapter=adapter,
                retrieved_at=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            )
            second = bok_base_rate.sync(
                root,
                adapter=adapter,
                retrieved_at=datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(first.normalized_records, 3)
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            self.assertEqual(second.changed_partitions, 0)
            rows = bok_base_rate.rebuild(root)
            self.assertEqual(rows[-1]["base_rate_percent"], "2.50")
            self.assertEqual(bok_base_rate.validate(root)["status"], "PASS")
        self.assertIn(bok_base_rate.DATASET_ID, SUPPORTED_DATASETS)

    def test_overlap_correction_replaces_canonical_month_and_preserves_raw_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bok_base_rate.sync(
                root,
                full=True,
                adapter=FakeAdapter(self.rows),
                retrieved_at=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            )
            corrected = list(self.rows)
            corrected[-1] = source_record("202603", "2.25")
            adapter = FakeAdapter(corrected)
            summary = bok_base_rate.sync(
                root,
                overlap_months=2,
                adapter=adapter,
                retrieved_at=datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(summary.raw_snapshot_created)
            self.assertEqual(adapter.calls, ["202601"])
            rows = bok_base_rate.rebuild(root)
            self.assertEqual(rows[-1]["period"], "2026-03")
            self.assertEqual(rows[-1]["base_rate_percent"], "2.25")

    def test_gap_in_source_published_monthly_history_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(bok_base_rate.DatasetValidationError):
                bok_base_rate.sync(
                    root,
                    full=True,
                    adapter=FakeAdapter(
                        [source_record("202601", "2.50"), source_record("202603", "2.50")]
                    ),
                    retrieved_at=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                )

    def test_cli_accepts_month_overlap_override(self):
        args = build_parser().parse_args(
            ["sync", bok_base_rate.DATASET_ID, "--overlap-months", "6"]
        )
        self.assertEqual(args.overlap_months, 6)


if __name__ == "__main__":
    unittest.main()
