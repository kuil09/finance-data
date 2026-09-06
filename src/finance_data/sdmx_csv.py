from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SDMXCSVError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, object]]
    request: dict[str, object]
    pages: int
    source_total_count: int


GetText = Callable[[str, Mapping[str, str]], str]


class SDMXCSVAdapter:
    """Minimal reusable SDMX REST adapter for CSV-formatted observations."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: int = 60,
        user_agent: str = "finance-data/0.1 (+https://github.com/kuil09/finance-data)",
        get_text: GetText | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent
        self._get_text = get_text or self._request_text

    def _request_text(self, url: str, headers: Mapping[str, str]) -> str:
        request = Request(url, headers=dict(headers))
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise SDMXCSVError(f"SDMX endpoint returned HTTP {status}")
                return response.read().decode("utf-8-sig")
        except SDMXCSVError:
            raise
        except Exception as exc:
            raise SDMXCSVError(f"SDMX request failed: {exc}") from exc

    def fetch(
        self,
        *,
        dataflow: str,
        key: str,
        start_period: str | None = None,
        end_period: str | None = None,
        detail: str = "full",
        extra_params: Mapping[str, str] | None = None,
    ) -> FetchResult:
        if not dataflow or "/" in dataflow:
            raise ValueError("dataflow must be a non-empty SDMX dataflow id")
        if not key:
            raise ValueError("key must be non-empty")

        params: dict[str, str] = {"format": "csvdata", "detail": detail}
        if start_period is not None:
            params["startPeriod"] = start_period
        if end_period is not None:
            params["endPeriod"] = end_period
        if extra_params:
            params.update({str(k): str(v) for k, v in extra_params.items()})

        url = f"{self.base_url}/data/{dataflow}/{key}?{urlencode(params)}"
        text = self._get_text(
            url,
            {
                "Accept": "text/csv",
                "User-Agent": self.user_agent,
            },
        )
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames:
            raise SDMXCSVError("SDMX CSV response has no header")

        records: list[dict[str, object]] = []
        for line_number, row in enumerate(reader, 2):
            if None in row:
                raise SDMXCSVError(f"malformed SDMX CSV row at line {line_number}")
            records.append({str(k): "" if v is None else str(v) for k, v in row.items()})

        return FetchResult(
            records=records,
            request={
                "access": "official_sdmx_rest",
                "base_url": self.base_url,
                "dataflow": dataflow,
                "key": key,
                "parameters": params,
                "format": "csvdata",
            },
            pages=1,
            source_total_count=len(records),
        )
