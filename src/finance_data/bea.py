from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BEA_API_URL = "https://apps.bea.gov/api/data/"
BEA_NIPA_FLAT_BASE = "https://apps.bea.gov/national/Release/TXT"
GDP_SERIES_CODE = "A191RC"
GDP_TABLE_ID = "T10105"
GDP_LINE_NUMBER = "1"


class BEADataError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, object]]
    request: dict[str, object]
    pages: int
    source_total_count: int


@dataclass(frozen=True)
class DownloadedFile:
    url: str
    content: bytes
    last_modified: str | None = None
    etag: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


FetchBytes = Callable[[str], DownloadedFile]
GetJSON = Callable[[str], dict[str, object]]


def _csv_rows(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [
        {str(key).lstrip("%"): ("" if value is None else str(value)) for key, value in row.items()}
        for row in reader
    ]


class BEANIPAFlatFileAdapter:
    """Read current NIPA registers and quarterly data directly from BEA flat files."""

    def __init__(self, *, timeout: int = 120, fetch_bytes: FetchBytes | None = None) -> None:
        self.timeout = timeout
        self._fetch_bytes = fetch_bytes or self._download

    def _download(self, url: str) -> DownloadedFile:
        request = Request(
            url,
            headers={
                "User-Agent": "finance-data/0.1 (+https://github.com/kuil09/finance-data/issues)",
                "Accept": "text/plain,text/csv,*/*",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise BEADataError(f"BEA flat file returned HTTP {status}: {url}")
                content = response.read()
                return DownloadedFile(
                    url=url,
                    content=content,
                    last_modified=response.headers.get("Last-Modified"),
                    etag=response.headers.get("ETag"),
                )
        except BEADataError:
            raise
        except Exception as exc:
            raise BEADataError(f"BEA flat-file request failed for {url}: {exc}") from exc

    def fetch_gdp_current_dollars(self) -> FetchResult:
        filenames = ("SeriesRegister.txt", "TablesRegister.txt", "nipadataQ.txt")
        files = {
            name: self._fetch_bytes(f"{BEA_NIPA_FLAT_BASE}/{name}") for name in filenames
        }
        series_rows = _csv_rows(files["SeriesRegister.txt"].content)
        table_rows = _csv_rows(files["TablesRegister.txt"].content)
        data_rows = _csv_rows(files["nipadataQ.txt"].content)

        series_matches = [row for row in series_rows if row.get("SeriesCode") == GDP_SERIES_CODE]
        if len(series_matches) != 1:
            raise BEADataError(f"expected one BEA GDP series register row, found {len(series_matches)}")
        series = series_matches[0]
        table_links = str(series.get("TableId:LineNo", "")).split("|")
        if f"{GDP_TABLE_ID}:{GDP_LINE_NUMBER}" not in table_links:
            raise BEADataError(
                f"BEA GDP series no longer maps to {GDP_TABLE_ID}:{GDP_LINE_NUMBER}: {table_links!r}"
            )

        table_matches = [row for row in table_rows if row.get("TableId") == GDP_TABLE_ID]
        if len(table_matches) != 1:
            raise BEADataError(f"expected one BEA GDP table row, found {len(table_matches)}")
        table = table_matches[0]

        records: list[dict[str, object]] = []
        for row in data_rows:
            if row.get("SeriesCode") != GDP_SERIES_CODE:
                continue
            records.append(
                {
                    "series_code": GDP_SERIES_CODE,
                    "period": row.get("Period", ""),
                    "value": row.get("Value", ""),
                    "series_label": series.get("SeriesLabel", ""),
                    "metric_name": series.get("MetricName", ""),
                    "calculation_type": series.get("CalculationType", ""),
                    "default_scale": series.get("DefaultScale", ""),
                    "table_id": GDP_TABLE_ID,
                    "line_number": GDP_LINE_NUMBER,
                    "table_title": table.get("TableTitle", ""),
                }
            )
        records.sort(key=lambda row: str(row["period"]))
        if not records:
            raise BEADataError("BEA GDP quarterly series returned no observations")

        file_metadata = {
            name: {
                "url": item.url,
                "content_sha256": item.sha256,
                "bytes": len(item.content),
                "last_modified": item.last_modified,
                "etag": item.etag,
            }
            for name, item in files.items()
        }
        return FetchResult(
            records=records,
            request={
                "access": "BEA NIPA flat files",
                "dataset": "NIPA",
                "table_id": GDP_TABLE_ID,
                "line_number": GDP_LINE_NUMBER,
                "series_code": GDP_SERIES_CODE,
                "frequency": "Q",
                "files": file_metadata,
            },
            pages=len(files),
            source_total_count=len(records),
        )


class BEAAPIAdapter:
    """Optional registered BEA API client for NIPA metadata/data access."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = 60,
        get_json: GetJSON | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("BEA_API_KEY") or None
        self.timeout = timeout
        self._get_json = get_json or self._request_json

    def _request_json(self, url: str) -> dict[str, object]:
        request = Request(
            url,
            headers={"User-Agent": "finance-data/0.1 (+https://github.com/kuil09/finance-data/issues)"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                value = json.load(response)
        except Exception as exc:
            raise BEADataError(f"BEA API request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise BEADataError("BEA API response is not an object")
        return value

    def fetch_nipa_table(
        self,
        *,
        table_name: str,
        frequency: str,
        year: str = "X",
    ) -> dict[str, object]:
        if not self.api_key:
            raise BEADataError("BEA_API_KEY is required for BEA API access")
        params = {
            "UserID": self.api_key,
            "method": "GetData",
            "DataSetName": "NIPA",
            "TableName": table_name,
            "Frequency": frequency,
            "Year": year,
            "ResultFormat": "JSON",
        }
        value = self._get_json(f"{BEA_API_URL}?{urlencode(params)}")
        bea = value.get("BEAAPI")
        if not isinstance(bea, dict):
            raise BEADataError("BEA API response does not contain BEAAPI")
        results = bea.get("Results")
        if not isinstance(results, dict):
            raise BEADataError("BEA API response does not contain Results")
        if "Error" in results:
            raise BEADataError(f"BEA API returned an error: {results['Error']!r}")
        return results
