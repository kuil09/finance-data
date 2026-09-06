from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from finance_data.bea import (
    BEAAPIAdapter,
    BEADataError,
    BEANIPAFlatFileAdapter,
    DownloadedFile,
    GDP_SERIES_CODE,
)
from finance_data.datasets import bea_gdp


SERIES = b'''%SeriesCode,SeriesLabel,MetricName,CalculationType,DefaultScale,TableId:LineNo,SeriesCodeParents\nA191RC,"Gross domestic product","Current Dollars","Level",-6,OECD10205:1|T10105:1|T10205:1,A001RC\n'''
TABLES = b'''TableId,TableTitle\nT10105,"Table 1.1.5. Gross Domestic Product"\n'''
DATA = b'''%SeriesCode,Period,Value\nA191RC,1947Q1,"243,164"\nA191RC,1947Q2,"245,968"\nA191RC,1947Q3,"249,585"\nA191RC,1947Q4,"259,745"\nA191RC,1948Q1,"265,742"\n'''


def flat_adapter(data: bytes = DATA) -> BEANIPAFlatFileAdapter:
    mapping = {
        "SeriesRegister.txt": SERIES,
        "TablesRegister.txt": TABLES,
        "nipadataQ.txt": data,
    }

    def fetch(url: str) -> DownloadedFile:
        name = url.rsplit("/", 1)[-1]
        return DownloadedFile(
            url=url,
            content=mapping[name],
            last_modified="Sat, 05 Sep 2026 12:00:00 GMT",
            etag=f'"{name}"',
        )

    return BEANIPAFlatFileAdapter(fetch_bytes=fetch)


class BEAAdapterTests(unittest.TestCase):
    def test_flat_adapter_resolves_series_table_line_and_file_provenance(self):
        result = flat_adapter().fetch_gdp_current_dollars()
        self.assertEqual(len(result.records), 5)
        first = result.records[0]
        self.assertEqual(first["series_code"], GDP_SERIES_CODE)
        self.assertEqual(first["table_id"], "T10105")
        self.assertEqual(first["line_number"], "1")
        self.assertEqual(first["value"], "243,164")
        self.assertEqual(first["default_scale"], "-6")
        self.assertEqual(result.pages, 3)
        files = result.request["files"]
        self.assertEqual(set(files), {"SeriesRegister.txt", "TablesRegister.txt", "nipadataQ.txt"})
        self.assertEqual(len(files["nipadataQ.txt"]["content_sha256"]), 64)

    def test_flat_adapter_fails_if_series_table_identity_drifts(self):
        bad_series = SERIES.replace(b"T10105:1", b"T10105:2")
        mapping = {
            "SeriesRegister.txt": bad_series,
            "TablesRegister.txt": TABLES,
            "nipadataQ.txt": DATA,
        }
        adapter = BEANIPAFlatFileAdapter(
            fetch_bytes=lambda url: DownloadedFile(url, mapping[url.rsplit('/', 1)[-1]])
        )
        with self.assertRaises(BEADataError):
            adapter.fetch_gdp_current_dollars()

    def test_api_adapter_requires_key_and_constructs_nipa_request(self):
        with self.assertRaises(BEADataError):
            BEAAPIAdapter(api_key=None, get_json=lambda _url: {}).fetch_nipa_table(
                table_name="T10105", frequency="Q"
            )

        seen: list[str] = []

        def get_json(url: str) -> dict[str, object]:
            seen.append(url)
            return {"BEAAPI": {"Results": {"UTCProductionTime": "2026-08-26T12:00:00Z", "Data": []}}}

        results = BEAAPIAdapter(api_key="secret", get_json=get_json).fetch_nipa_table(
            table_name="T10105", frequency="Q"
        )
        query = parse_qs(urlparse(seen[0]).query)
        self.assertEqual(query["DataSetName"], ["NIPA"])
        self.assertEqual(query["TableName"], ["T10105"])
        self.assertEqual(query["Frequency"], ["Q"])
        self.assertEqual(query["Year"], ["X"])
        self.assertEqual(results["UTCProductionTime"], "2026-08-26T12:00:00Z")


class BEAGDPDatasetTests(unittest.TestCase):
    def test_normalization_preserves_current_dollar_level(self):
        record = flat_adapter().fetch_gdp_current_dollars().records[0]
        normalized = bea_gdp.normalize_source_record(record)
        self.assertEqual(normalized["period"], "1947-Q1")
        self.assertEqual(normalized["gdp_millions_current_dollars"], "243164")
        self.assertEqual(normalized["source_series_code"], "A191RC")
        self.assertEqual(normalized["source_table_id"], "T10105")
        self.assertEqual(normalized["source_line_number"], "1")

    def test_sync_is_idempotent_and_rebuild_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = bea_gdp.sync(
                root,
                adapter=flat_adapter(),
                retrieved_at=datetime(2026, 9, 6, 1, 0, tzinfo=timezone.utc),
            )
            second = bea_gdp.sync(
                root,
                adapter=flat_adapter(),
                retrieved_at=datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(first.normalized_records, 5)
            self.assertTrue(first.raw_snapshot_created)
            self.assertFalse(second.raw_snapshot_created)
            self.assertEqual(second.changed_partitions, 0)
            before = bea_gdp.validate(root)
            rows = bea_gdp.rebuild(root)
            after = bea_gdp.validate(root)
            self.assertEqual(before, after)
            self.assertEqual(len(rows), 5)

    def test_revision_replaces_canonical_quarter_and_preserves_raw_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bea_gdp.sync(
                root,
                adapter=flat_adapter(),
                retrieved_at=datetime(2026, 9, 6, 1, 0, tzinfo=timezone.utc),
            )
            revised = DATA.replace(b'1947Q2,"245,968"', b'1947Q2,"245,969"')
            summary = bea_gdp.sync(
                root,
                adapter=flat_adapter(revised),
                retrieved_at=datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(summary.raw_snapshot_created)
            rows = bea_gdp.rebuild(root)
            by_period = {row["period"]: row for row in rows}
            self.assertEqual(by_period["1947-Q2"]["gdp_millions_current_dollars"], "245969")
            raw_snapshots = list((root / "data" / "raw" / "us_bea" / "NIPA.T10105.A191RC.Q").glob("sha256-*"))
            self.assertEqual(len(raw_snapshots), 2)

    def test_gap_in_quarterly_history_fails_validation(self):
        gapped = DATA.replace(b'A191RC,1947Q3,"249,585"\n', b"")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(bea_gdp.DatasetValidationError):
                bea_gdp.sync(Path(temp), adapter=flat_adapter(gapped))


if __name__ == "__main__":
    unittest.main()
