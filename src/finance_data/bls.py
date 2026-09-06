from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen

BLS_V1_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_V2_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
CPI_U_ALL_ITEMS_SERIES = "CUUR0000SA0"


class BLSDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, object]]
    request: dict[str, object]
    pages: int
    source_total_count: int


PostJSON = Callable[[str, dict[str, object]], dict[str, object]]


class BLSPublicDataAdapter:
    """Access the official BLS Public Data API without requiring credentials."""

    def __init__(
        self,
        *,
        registration_key: str | None = None,
        timeout: int = 60,
        post_json: PostJSON | None = None,
    ) -> None:
        self.registration_key = registration_key or os.environ.get("BLS_REGISTRATION_KEY") or None
        self.timeout = timeout
        self._post_json = post_json or self._request_json

    @property
    def registered(self) -> bool:
        return self.registration_key is not None

    @property
    def endpoint(self) -> str:
        return BLS_V2_URL if self.registered else BLS_V1_URL

    def _request_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "finance-data/0.1 (+https://github.com/kuil09/finance-data/issues)",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise BLSDataError(f"BLS API returned HTTP {status}")
                value = json.load(response)
        except BLSDataError:
            raise
        except Exception as exc:
            raise BLSDataError(f"BLS API request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise BLSDataError("BLS API response is not an object")
        return value

    def fetch_series(self, series_id: str, *, start_year: int, end_year: int) -> FetchResult:
        if start_year > end_year:
            raise ValueError("start_year must not exceed end_year")
        if end_year - start_year > 9:
            raise ValueError("BLS request windows must not exceed 10 calendar years")

        payload: dict[str, object] = {
            "seriesid": [series_id],
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
        if self.registration_key is not None:
            payload["registrationkey"] = self.registration_key

        response = self._post_json(self.endpoint, payload)
        if response.get("status") != "REQUEST_SUCCEEDED":
            raise BLSDataError(
                f"BLS request failed: status={response.get('status')!r} "
                f"message={response.get('message')!r}"
            )
        results = response.get("Results")
        if not isinstance(results, dict):
            raise BLSDataError("BLS response does not contain Results")
        series = results.get("series")
        if not isinstance(series, list) or len(series) != 1:
            raise BLSDataError(f"unexpected BLS series response: {series!r}")
        item = series[0]
        if not isinstance(item, dict) or item.get("seriesID") != series_id:
            raise BLSDataError(f"unexpected BLS series id: {item!r}")
        data = item.get("data")
        if not isinstance(data, list):
            raise BLSDataError("BLS series does not contain a data array")

        records: list[dict[str, object]] = []
        filtered_periods: set[str] = set()
        for observation in data:
            if not isinstance(observation, dict):
                raise BLSDataError(f"unexpected BLS observation: {observation!r}")
            period = observation.get("period")
            # BLS examples explicitly filter API results to M01-M12 when a
            # monthly series is wanted. Annual-average M13 is not this dataset.
            if not isinstance(period, str) or not ("M01" <= period <= "M12"):
                filtered_periods.add(str(period))
                continue
            records.append({"series_id": series_id, **observation})

        records.sort(key=lambda row: (str(row.get("year", "")), str(row.get("period", ""))))
        response_metadata = {
            key: value for key, value in response.items() if key != "Results"
        }
        return FetchResult(
            records=records,
            request={
                "endpoint": self.endpoint,
                "registered": self.registered,
                "series_id": series_id,
                "start_year": start_year,
                "end_year": end_year,
                "period_filter": "M01-M12",
                "filtered_period_codes": sorted(filtered_periods),
                "response_metadata": response_metadata,
            },
            pages=1,
            source_total_count=len(data),
        )
