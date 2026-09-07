from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WORLD_BANK_API_BASE_URL = "https://api.worldbank.org/v2"
GDP_CURRENT_USD_INDICATOR = "NY.GDP.MKTP.CD"
WDI_SOURCE_ID = "2"


class WorldBankAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, object]]
    request: dict[str, object]
    pages: int
    source_total_count: int


GetJSON = Callable[[str], object]


class WorldBankIndicatorsAdapter:
    def __init__(
        self,
        *,
        base_url: str = WORLD_BANK_API_BASE_URL,
        timeout: int = 60,
        user_agent: str = "finance-data/0.1 (+https://github.com/kuil09/finance-data)",
        per_page: int = 20000,
        get_json: GetJSON | None = None,
    ) -> None:
        if per_page <= 0:
            raise ValueError("per_page must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent
        self.per_page = per_page
        self._get_json = get_json or self._request_json

    def _request_json(self, url: str) -> object:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise WorldBankAPIError(f"World Bank API returned HTTP {status}")
                return json.loads(response.read().decode("utf-8"))
        except WorldBankAPIError:
            raise
        except Exception as exc:
            raise WorldBankAPIError(f"World Bank API request failed: {exc}") from exc

    @staticmethod
    def _unpack(payload: object, *, context: str) -> tuple[dict[str, object], list[dict[str, object]]]:
        if not isinstance(payload, list) or len(payload) != 2:
            raise WorldBankAPIError(f"unexpected World Bank {context} response shape")
        header, rows = payload
        if not isinstance(header, dict) or not isinstance(rows, list):
            raise WorldBankAPIError(f"unexpected World Bank {context} response types")
        normalized: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise WorldBankAPIError(f"unexpected World Bank {context} row type")
            normalized.append(dict(row))
        return dict(header), normalized

    def fetch_gdp_current_usd(self) -> FetchResult:
        indicator_url = (
            f"{self.base_url}/indicator/{GDP_CURRENT_USD_INDICATOR}?"
            + urlencode({"source": WDI_SOURCE_ID, "format": "json"})
        )
        indicator_header, indicator_rows = self._unpack(
            self._get_json(indicator_url), context="indicator metadata"
        )
        if len(indicator_rows) != 1 or indicator_rows[0].get("id") != GDP_CURRENT_USD_INDICATOR:
            raise WorldBankAPIError("World Bank GDP indicator metadata did not resolve uniquely")

        records: list[dict[str, object]] = []
        page = 1
        pages = 1
        source_total = 0
        last_updated: str | None = None
        while page <= pages:
            params = {
                "source": WDI_SOURCE_ID,
                "format": "json",
                "per_page": str(self.per_page),
                "page": str(page),
            }
            url = (
                f"{self.base_url}/country/all/indicator/{GDP_CURRENT_USD_INDICATOR}?"
                + urlencode(params)
            )
            header, rows = self._unpack(self._get_json(url), context="GDP observations")
            try:
                returned_page = int(header["page"])
                pages = int(header["pages"])
                source_total = int(header["total"])
            except (KeyError, TypeError, ValueError) as exc:
                raise WorldBankAPIError("World Bank pagination metadata is invalid") from exc
            if returned_page != page or pages < page or source_total < 0:
                raise WorldBankAPIError("World Bank pagination metadata is inconsistent")
            if str(header.get("sourceid", WDI_SOURCE_ID)) != WDI_SOURCE_ID:
                raise WorldBankAPIError("World Bank response source id changed")
            current_last_updated = header.get("lastupdated")
            if current_last_updated is not None:
                current_last_updated = str(current_last_updated)
                if last_updated is not None and current_last_updated != last_updated:
                    raise WorldBankAPIError("World Bank lastupdated changed during pagination")
                last_updated = current_last_updated
            records.extend(rows)
            page += 1

        if len(records) != source_total:
            raise WorldBankAPIError(
                f"World Bank record count mismatch: fetched={len(records)} total={source_total}"
            )
        return FetchResult(
            records=records,
            request={
                "access": "world_bank_indicators_api_v2",
                "base_url": self.base_url,
                "indicator": GDP_CURRENT_USD_INDICATOR,
                "source_id": WDI_SOURCE_ID,
                "country_selector": "all",
                "format": "json",
                "per_page": self.per_page,
                "last_updated": last_updated,
                "indicator_metadata_header": indicator_header,
                "indicator_metadata": indicator_rows[0],
            },
            pages=pages,
            source_total_count=source_total,
        )
