from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
DEBT_TO_PENNY_ENDPOINT = "/v2/accounting/od/debt_to_penny"
DEBT_TO_PENNY_FIELDS = (
    "record_date",
    "debt_held_public_amt",
    "intragov_hold_amt",
    "tot_pub_debt_out_amt",
    "src_line_nbr",
)


class TreasuryAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, str]]
    request: dict[str, Any]
    pages: int
    source_total_count: int | None


class TreasuryFiscalDataAdapter:
    """Minimal reusable adapter for the U.S. Treasury Fiscal Data API."""

    def __init__(
        self,
        *,
        timeout: int = 30,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.timeout = timeout
        self._opener = opener or urlopen

    def fetch_debt_to_penny(
        self,
        *,
        start_date: date | None = None,
        page_size: int = 5000,
    ) -> FetchResult:
        if page_size <= 0:
            raise ValueError("page_size must be positive")

        base_params: dict[str, str] = {
            "fields": ",".join(DEBT_TO_PENNY_FIELDS),
            "format": "json",
            "sort": "record_date",
            "page[size]": str(page_size),
        }
        if start_date is not None:
            base_params["filter"] = f"record_date:gte:{start_date.isoformat()}"

        page = 1
        records: list[dict[str, str]] = []
        total_pages: int | None = None
        total_count: int | None = None

        while True:
            params = dict(base_params)
            params["page[number]"] = str(page)
            url = f"{BASE_URL}{DEBT_TO_PENNY_ENDPOINT}?{urlencode(params)}"
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "finance-data/0.1 (+https://github.com/kuil09/finance-data)",
                },
            )

            try:
                with self._opener(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status != 200:
                        raise TreasuryAPIError(f"Treasury API returned HTTP {status}")
                    payload = json.load(response)
            except TreasuryAPIError:
                raise
            except Exception as exc:  # urllib exposes several transport exception types
                raise TreasuryAPIError(f"Treasury API request failed: {exc}") from exc

            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise TreasuryAPIError("Treasury API response does not contain a data array")

            page_records = payload["data"]
            for record in page_records:
                if not isinstance(record, dict):
                    raise TreasuryAPIError("Treasury API returned a non-object record")
                records.append({key: value for key, value in record.items()})

            meta = payload.get("meta") or {}
            if total_pages is None and meta.get("total-pages") is not None:
                total_pages = int(meta["total-pages"])
            if total_count is None and meta.get("total-count") is not None:
                total_count = int(meta["total-count"])

            if total_pages is not None:
                if page >= total_pages:
                    break
            elif len(page_records) < page_size:
                break

            page += 1

        request_meta: dict[str, Any] = {
            "base_url": BASE_URL,
            "endpoint": DEBT_TO_PENNY_ENDPOINT,
            "fields": list(DEBT_TO_PENNY_FIELDS),
            "sort": "record_date",
            "page_size": page_size,
            "start_date": start_date.isoformat() if start_date else None,
        }
        return FetchResult(
            records=records,
            request=request_meta,
            pages=page,
            source_total_count=total_count,
        )
