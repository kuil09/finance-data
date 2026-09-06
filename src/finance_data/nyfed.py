from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

NYFED_MARKETS_BASE = "https://markets.newyorkfed.org/api/rates/secured/sofr"
SOFR_START_DATE = date(2018, 4, 3)


class NYFedMarketsError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, object]]
    request: dict[str, object]
    pages: int
    source_total_count: int


GetJSON = Callable[[str], dict[str, object]]


class NYFedMarketsAdapter:
    """Read SOFR directly from the New York Fed Markets Data API."""

    def __init__(
        self,
        *,
        timeout: int = 60,
        get_json: GetJSON | None = None,
    ) -> None:
        self.timeout = timeout
        self._get_json = get_json or self._request_json

    def _request_json(self, url: str) -> dict[str, object]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "finance-data/0.1 (+https://github.com/kuil09/finance-data)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise NYFedMarketsError(f"New York Fed API returned HTTP {status}")
                value = json.load(response)
        except NYFedMarketsError:
            raise
        except Exception as exc:
            raise NYFedMarketsError(f"New York Fed API request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise NYFedMarketsError("New York Fed API response is not an object")
        return value

    def fetch_sofr(
        self,
        *,
        start_date: date = SOFR_START_DATE,
        end_date: date | None = None,
    ) -> FetchResult:
        if end_date is None:
            end_date = date.today()
        if start_date < SOFR_START_DATE:
            start_date = SOFR_START_DATE
        if start_date > end_date:
            raise ValueError("start_date must not exceed end_date")

        params = urlencode(
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                # Both rate and volume are requested explicitly. Omitting type has
                # the same current behavior, but explicit intent is safer.
                "type": "rate,volume",
            }
        )
        url = f"{NYFED_MARKETS_BASE}/search.json?{params}"
        payload = self._get_json(url)
        rows = payload.get("refRates")
        if not isinstance(rows, list):
            raise NYFedMarketsError("New York Fed response does not contain refRates")

        records: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise NYFedMarketsError(f"unexpected New York Fed SOFR row: {row!r}")
            if row.get("type") != "SOFR":
                raise NYFedMarketsError(f"unexpected reference-rate type: {row.get('type')!r}")
            required = {
                "effectiveDate",
                "percentRate",
                "percentPercentile1",
                "percentPercentile25",
                "percentPercentile75",
                "percentPercentile99",
                "volumeInBillions",
                "revisionIndicator",
            }
            missing = sorted(required - set(row))
            if missing:
                raise NYFedMarketsError(f"SOFR row missing required fields: {missing}")
            footnote = row.get("footnoteId")
            records.append(
                {
                    "effective_date": row["effectiveDate"],
                    "type": row["type"],
                    "percent_rate": row["percentRate"],
                    "percentile_1": row["percentPercentile1"],
                    "percentile_25": row["percentPercentile25"],
                    "percentile_75": row["percentPercentile75"],
                    "percentile_99": row["percentPercentile99"],
                    "volume_billions": row["volumeInBillions"],
                    "revision_indicator": row.get("revisionIndicator") or "",
                    "footnote_id": None if footnote in (None, "") else str(footnote),
                }
            )

        records.sort(key=lambda row: str(row["effective_date"]))
        return FetchResult(
            records=records,
            request={
                "access": "official_rest",
                "endpoint": f"{NYFED_MARKETS_BASE}/search.json",
                "url": url,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "type": "rate,volume",
            },
            pages=1,
            source_total_count=len(records),
        )
