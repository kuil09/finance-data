from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from finance_data.datasets import ecb_deposit_rate
from finance_data.ecb import ECBDataAdapter
from finance_data.registry import SUPPORTED_DATASETS
from finance_data.sdmx_csv import FetchResult, SDMXCSVAdapter


def source_row(period: str, value: str, *, status: str = "A") -> dict[str, object]:
    return {
        "KEY": "FM.D.U2.EUR.4F.KR.DFR.LEV",
        "FREQ": "D",
        "REF_AREA": "U2",
        "CURRENCY": "EUR",
        "PROVIDER_FM": "4F",
        "INSTRUMENT_FM": "KR",
        "PROVIDER_FM_ID": "DFR",
        "DATA_TYPE_FM": "LEV",
        "TIME_PERIOD": period,
        "OBS_VALUE": value,
        "OBS_STATUS": status,
        "OBS_CONF": "F",
        "OBS_PRE_BREAK": "",
        "TIME_FORMAT": "P1D",
        "UNIT": "PCPA",
        "UNIT_MULT": "0",
    }


class FakeECBAdapter:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls = 0

    def fetch_deposit_facility_rate(self) -> FetchResult:
        self.calls += 1
        return FetchResult(
            records=[dict(row) for row in self.rows],
            request={"access": "fixture", "dataflow": "FM", "key": "D.U2.EUR.4F.KR.DFR.LEV"},
            pages=1,
            source_total_count=len(self.rows),
        )


class SDMXCSVAdapterTests(unittest.TestCase):
    def test_generic_adapter_constructs_sdmx_query_and_preserves_columns(self):
        requested: list[tuple[str, dict[str, str]]] = []
        text = (
            "KEY,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
            "FLOW.D.X, D,2026-01-01,1.25,A\n"
        )

        def get_text(url: str, headers):
            requested.append((url, dict(headers)))
            return text

        result = SDMXCSVAdapter(
            base_url="https://example.test/service", get_text=get_text
        ).fetch(
            dataflow="FLOW",
            key="D.X",
            start_period="2026-01-01",
            end_period="2026-01-31",
            detail="full",
        )
        parsed = urlparse(requested[0][0])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/service/data/FLOW/D.X")
        self.assertEqual(query["format"], ["csvdata"])
        self.assertEqual(query["startPeriod"], ["2026-01-01"])
        self.assertEqual(query["endPeriod"], ["2026-01-31"])
        self.assertEqual(result.records[0]["TIME_PERIOD"], "2026-01-01")
        self.assertEqual(result.records[0]["OBS_VALUE"], "1.25")
        self.assertEqual(requested[0][1]["Accept"], "text/csv")

    def test_ecb_wrapper_uses_exact_dataflow_and_key(self):
        calls: list[tuple[str, str, str | None, str]] = []

        class FakeSDMX:
            def fetch(self, *, dataflow, key, start_period=None, detail="full", **_kwargs):
                calls.append((dataflow, key, start_period, detail))
                return FetchResult([], {}, 1, 0)

        ECBDataAdapter(sdmx=FakeSDMX()).fetch_deposit_facility_rate()
        self.assertEqual(calls, [("FM", "D.U2.EUR.4F.KR.DFR.LEV", "1999-01-01", "full")])


class ECBDepositRateDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            source_row("2026-09-05", "2.25"),
            source_row("2026-09-06", "2.25"),
            source_row("2026-09-07", "2.25"),
        ]

    def test_sync_is_idempotent_registered_and_rebuildable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = FakeECBAdapter(self.rows)
            first = ecb_deposit_rate.sync(
                root,
                full=True,
                adapter=adapter,
                retrieved_at=datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc),
            )
            second = ecb_deposit_rate.sync(
                root,
                adapter=adapter,
                retrieved_at=datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(first.normalized_records, 3)
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            self.assertEqual(second.changed_partitions, 0)
            report = ecb_deposit_rate.validate(root)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["source_key"], "D.U2.EUR.4F.KR.DFR.LEV")
        self.assertIn(ecb_deposit_rate.DATASET_ID, SUPPORTED_DATASETS)

    def test_source_correction_replaces_canonical_day_and_preserves_raw_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ecb_deposit_rate.sync(
                root,
                adapter=FakeECBAdapter(self.rows),
                retrieved_at=datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc),
            )
            corrected = list(self.rows)
            corrected[-1] = source_row("2026-09-07", "2.20", status="R")
            summary = ecb_deposit_rate.sync(
                root,
                adapter=FakeECBAdapter(corrected),
                retrieved_at=datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(summary.raw_snapshot_created)
            rows = ecb_deposit_rate.rebuild(root)
            self.assertEqual(rows[-1]["deposit_facility_rate_percent"], "2.20")
            self.assertEqual(rows[-1]["obs_status"], "R")

    def test_gap_in_source_published_calendar_daily_history_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ecb_deposit_rate.DatasetValidationError):
                ecb_deposit_rate.sync(
                    Path(temp),
                    adapter=FakeECBAdapter(
                        [source_row("2026-09-05", "2.25"), source_row("2026-09-07", "2.25")]
                    ),
                    retrieved_at=datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc),
                )

    def test_wrong_sdmx_dimension_is_rejected(self):
        row = source_row("2026-09-07", "2.25")
        row["REF_AREA"] = "US"
        with self.assertRaises(ecb_deposit_rate.DatasetValidationError):
            ecb_deposit_rate.validate_source_record(row)


if __name__ == "__main__":
    unittest.main()
