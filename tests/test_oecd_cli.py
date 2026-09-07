from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finance_data.datasets import oecd_cli
from finance_data.oecd import OECDDataAdapter
from finance_data.registry import SUPPORTED_DATASETS
from finance_data.sdmx_csv import FetchResult


def source_row(area: str, period: str, value: str, *, status: str = "A") -> dict[str, object]:
    return {
        "DATAFLOW": "OECD.SDD.STES:DSD_STES@DF_CLI(4.1)",
        "REF_AREA": area,
        "FREQ": "M",
        "MEASURE": "LI",
        "UNIT_MEASURE": "IX",
        "ACTIVITY": "_Z",
        "ADJUSTMENT": "AA",
        "TRANSFORMATION": "IX",
        "TIME_HORIZ": "_Z",
        "METHODOLOGY": "H",
        "TIME_PERIOD": period,
        "OBS_VALUE": value,
        "OBS_STATUS": status,
        "UNIT_MULT": "0",
        "DECIMALS": "2",
        "BASE_PER": "",
    }


class FakeOECDAdapter:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls = 0

    def fetch_composite_leading_indicator(self) -> FetchResult:
        self.calls += 1
        return FetchResult(
            records=[dict(row) for row in self.rows],
            request={"access": "fixture", "dataflow": "OECD.SDD.STES,DSD_STES@DF_CLI,4.1"},
            pages=1,
            source_total_count=len(self.rows),
        )


class OECDAdapterTests(unittest.TestCase):
    def test_wrapper_reuses_shared_sdmx_adapter_with_exact_slice(self):
        calls: list[dict[str, object]] = []

        class FakeSDMX:
            def fetch(self, **kwargs):
                calls.append(kwargs)
                return FetchResult([], {}, 1, 0)

        OECDDataAdapter(sdmx=FakeSDMX()).fetch_composite_leading_indicator()
        self.assertEqual(calls[0]["dataflow"], "OECD.SDD.STES,DSD_STES@DF_CLI,4.1")
        self.assertEqual(calls[0]["key"], ".M.LI...AA...H")
        self.assertEqual(
            calls[0]["extra_params"], {"dimensionAtObservation": "AllDimensions"}
        )


class OECDCLIDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            source_row("KOR", "2026-05", "102.66"),
            source_row("USA", "2026-05", "100.10"),
            source_row("KOR", "2026-06", "102.87"),
            source_row("USA", "2026-06", "100.20"),
        ]

    def test_sync_is_idempotent_registered_and_preserves_area_dimension(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = FakeOECDAdapter(self.rows)
            first = oecd_cli.sync(
                root,
                full=True,
                adapter=adapter,
                retrieved_at=datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc),
            )
            second = oecd_cli.sync(
                root,
                adapter=adapter,
                retrieved_at=datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(first.normalized_records, 4)
            self.assertEqual(first.reference_areas, 2)
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            rows = oecd_cli.rebuild(root)
            keys={(row["ref_area"],row["period"]) for row in rows}
            self.assertEqual(keys, {("KOR","2026-05"),("USA","2026-05"),("KOR","2026-06"),("USA","2026-06")})
        self.assertIn(oecd_cli.DATASET_ID, SUPPORTED_DATASETS)

    def test_source_correction_replaces_only_matching_area_period(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            oecd_cli.sync(
                root,
                adapter=FakeOECDAdapter(self.rows),
                retrieved_at=datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc),
            )
            corrected=list(self.rows)
            corrected[2]=source_row("KOR","2026-06","103.01",status="R")
            oecd_cli.sync(
                root,
                adapter=FakeOECDAdapter(corrected),
                retrieved_at=datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc),
            )
            rows={(row["ref_area"],row["period"]):row for row in oecd_cli.rebuild(root)}
            self.assertEqual(rows[("KOR","2026-06")]["cli_index"],"103.01")
            self.assertEqual(rows[("KOR","2026-06")]["obs_status"],"R")
            self.assertEqual(rows[("USA","2026-06")]["cli_index"],"100.20")

    def test_duplicate_area_period_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(oecd_cli.DatasetValidationError):
                oecd_cli.sync(
                    Path(temp),
                    adapter=FakeOECDAdapter([self.rows[0], dict(self.rows[0])]),
                    retrieved_at=datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc),
                )

    def test_wrong_selected_dimension_is_rejected(self):
        row=source_row("KOR","2026-06","102.87")
        row["ADJUSTMENT"]="SA"
        with self.assertRaises(oecd_cli.DatasetValidationError):
            oecd_cli.validate_source_record(row)


if __name__ == "__main__":
    unittest.main()
