from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

PETROLEUM_BULK_URL = "https://www.eia.gov/opendata/bulk/PET.zip"
COMMERCIAL_CRUDE_STOCKS_SERIES = "PET.WCESTUS1.W"


class EIAOpenDataError(RuntimeError):
    pass


class EIARightsError(EIAOpenDataError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, object]]
    request: dict[str, object]
    pages: int
    source_total_count: int


FetchToPath = Callable[[str, Path], dict[str, str]]


class EIAOpenDataAdapter:
    """Read selected EIA-owned series from the official Open Data bulk packages."""

    def __init__(
        self,
        *,
        timeout: int = 180,
        fetch_to_path: FetchToPath | None = None,
    ) -> None:
        self.timeout = timeout
        self._fetch_to_path = fetch_to_path or self._download_to_path

    def _download_to_path(self, url: str, destination: Path) -> dict[str, str]:
        request = Request(
            url,
            headers={
                "Accept": "application/zip",
                "User-Agent": "finance-data/0.1 (+https://github.com/kuil09/finance-data)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise EIAOpenDataError(f"EIA bulk download returned HTTP {status}")
                with destination.open("wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                return {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in {"etag", "last-modified", "content-length"}
                }
        except EIAOpenDataError:
            raise
        except Exception as exc:
            raise EIAOpenDataError(f"EIA bulk download failed: {exc}") from exc

    def fetch_series(
        self,
        series_id: str,
        *,
        start_date: date | None = None,
    ) -> FetchResult:
        with tempfile.TemporaryDirectory(prefix="finance-data-eia-") as temp:
            archive = Path(temp) / "PET.zip"
            response_headers = self._fetch_to_path(PETROLEUM_BULK_URL, archive)
            series = self._read_series(archive, series_id)

        self._validate_reuse_metadata(series, series_id)
        data = series.get("data")
        if not isinstance(data, list):
            raise EIAOpenDataError(f"EIA series {series_id} does not contain a data array")

        records: list[dict[str, object]] = []
        for item in data:
            if not isinstance(item, list) or len(item) != 2:
                raise EIAOpenDataError(f"unexpected EIA observation for {series_id}: {item!r}")
            period, value = item
            if not isinstance(period, str):
                raise EIAOpenDataError("EIA observation period is not a string")
            try:
                parsed_period = datetime.strptime(period, "%Y%m%d").date()
            except ValueError as exc:
                raise EIAOpenDataError(f"invalid EIA weekly period: {period!r}") from exc
            if start_date is not None and parsed_period < start_date:
                continue
            records.append(
                {
                    "period": period,
                    "series_id": series_id,
                    "value": value,
                }
            )

        metadata = {key: value for key, value in series.items() if key != "data"}
        return FetchResult(
            records=sorted(records, key=lambda row: str(row["period"])),
            request={
                "access": "official_bulk",
                "bulk_url": PETROLEUM_BULK_URL,
                "series_id": series_id,
                "start_date": start_date.isoformat() if start_date else None,
                "series_metadata": metadata,
                "response_headers": response_headers,
            },
            pages=1,
            source_total_count=len(data),
        )

    @staticmethod
    def _read_series(archive: Path, series_id: str) -> dict[str, object]:
        marker = f'"series_id":"{series_id}"'.encode("utf-8")
        try:
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                if "PET.txt" not in names:
                    raise EIAOpenDataError("EIA PET bulk archive does not contain PET.txt")
                with bundle.open("PET.txt") as handle:
                    for raw_line in handle:
                        if marker not in raw_line:
                            continue
                        value = json.loads(raw_line)
                        if not isinstance(value, dict) or value.get("series_id") != series_id:
                            continue
                        return value
        except EIAOpenDataError:
            raise
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise EIAOpenDataError(f"failed to parse EIA PET bulk archive: {exc}") from exc
        raise EIAOpenDataError(f"EIA series not found in PET bulk archive: {series_id}")

    @staticmethod
    def _validate_reuse_metadata(series: dict[str, object], series_id: str) -> None:
        copyright_value = series.get("copyright")
        source = str(series.get("source", ""))
        if copyright_value not in (None, "", "None"):
            raise EIARightsError(
                f"EIA series {series_id} carries third-party copyright metadata: "
                f"{copyright_value!r}"
            )
        if "EIA" not in source or "Energy Information Administration" not in source:
            raise EIARightsError(
                f"EIA series {series_id} is not explicitly sourced to EIA: {source!r}"
            )
