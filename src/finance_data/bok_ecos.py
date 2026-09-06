from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Callable
from urllib.request import Request, urlopen

ECOS_BASE_URL = "https://ecos.bok.or.kr/api"
BASE_RATE_STAT_CODE = "722Y001"
BASE_RATE_ITEM_CODE = "0101000"
BASE_RATE_CYCLE = "M"
BASE_RATE_START = "199905"


class ECOSError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, object]]
    request: dict[str, object]
    pages: int
    source_total_count: int


GetJSON = Callable[[str], dict[str, object]]


class ECOSAdapter:
    """Read source-published Bank of Korea statistics from the ECOS Open API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = 60,
        get_json: GetJSON | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("BOK_ECOS_API_KEY") or None
        self.timeout = timeout
        self._get_json = get_json or self._request_json

    @property
    def access_key(self) -> str:
        return self.api_key or "sample"

    @property
    def registered(self) -> bool:
        return self.api_key is not None

    @property
    def page_size(self) -> int:
        # ECOS explicitly limits the public sample key to 10 rows per request.
        return 1000 if self.registered else 10

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
                    raise ECOSError(f"ECOS API returned HTTP {status}")
                value = json.load(response)
        except ECOSError:
            raise
        except Exception as exc:
            raise ECOSError(f"ECOS API request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise ECOSError("ECOS response is not an object")
        return value

    @staticmethod
    def _raise_result_error(payload: dict[str, object]) -> None:
        result = payload.get("RESULT")
        if not isinstance(result, dict):
            return
        code = result.get("CODE")
        message = result.get("MESSAGE")
        raise ECOSError(f"ECOS error {code}: {message}")

    def _search_url(
        self,
        *,
        start_index: int,
        end_index: int,
        stat_code: str,
        cycle: str,
        start_time: str,
        end_time: str,
        item_code: str,
    ) -> str:
        return (
            f"{ECOS_BASE_URL}/StatisticSearch/{self.access_key}/json/kr/"
            f"{start_index}/{end_index}/{stat_code}/{cycle}/{start_time}/{end_time}/{item_code}"
        )

    def fetch_series(
        self,
        *,
        stat_code: str,
        item_code: str,
        cycle: str,
        start_time: str,
        end_time: str,
    ) -> FetchResult:
        if cycle not in {"A", "Q", "M", "D"}:
            raise ValueError(f"unsupported ECOS cycle: {cycle}")
        if start_time > end_time:
            raise ValueError("start_time must not exceed end_time")

        page_size = self.page_size
        first_url = self._search_url(
            start_index=1,
            end_index=page_size,
            stat_code=stat_code,
            cycle=cycle,
            start_time=start_time,
            end_time=end_time,
            item_code=item_code,
        )
        first = self._get_json(first_url)
        self._raise_result_error(first)
        root = first.get("StatisticSearch")
        if not isinstance(root, dict):
            raise ECOSError("ECOS response does not contain StatisticSearch")
        total = root.get("list_total_count")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ECOSError(f"invalid ECOS list_total_count: {total!r}")

        raw_rows: list[object] = list(root.get("row") or [])
        pages = 1
        next_index = page_size + 1
        while len(raw_rows) < total:
            end_index = min(next_index + page_size - 1, total)
            payload = self._get_json(
                self._search_url(
                    start_index=next_index,
                    end_index=end_index,
                    stat_code=stat_code,
                    cycle=cycle,
                    start_time=start_time,
                    end_time=end_time,
                    item_code=item_code,
                )
            )
            self._raise_result_error(payload)
            page_root = payload.get("StatisticSearch")
            if not isinstance(page_root, dict):
                raise ECOSError("ECOS page does not contain StatisticSearch")
            page_total = page_root.get("list_total_count")
            if page_total != total:
                raise ECOSError(
                    f"ECOS total changed during pagination: first={total} page={page_total}"
                )
            page_rows = page_root.get("row") or []
            if not isinstance(page_rows, list) or not page_rows:
                raise ECOSError("ECOS pagination returned an empty page before total count")
            raw_rows.extend(page_rows)
            pages += 1
            next_index = end_index + 1

        if len(raw_rows) != total:
            raise ECOSError(f"ECOS row count mismatch: total={total}, rows={len(raw_rows)}")

        records: list[dict[str, object]] = []
        for row in raw_rows:
            if not isinstance(row, dict):
                raise ECOSError(f"unexpected ECOS row: {row!r}")
            records.append(
                {
                    "time": row.get("TIME"),
                    "stat_code": row.get("STAT_CODE"),
                    "stat_name": row.get("STAT_NAME"),
                    "item_code": row.get("ITEM_CODE1"),
                    "item_name": row.get("ITEM_NAME1"),
                    "unit_name": row.get("UNIT_NAME"),
                    "data_value": row.get("DATA_VALUE"),
                }
            )

        records.sort(key=lambda row: str(row["time"]))
        return FetchResult(
            records=records,
            request={
                "access": "official_rest",
                "endpoint": f"{ECOS_BASE_URL}/StatisticSearch/...",
                "registered": self.registered,
                "page_size": page_size,
                "stat_code": stat_code,
                "item_code": item_code,
                "cycle": cycle,
                "start_time": start_time,
                "end_time": end_time,
            },
            pages=pages,
            source_total_count=total,
        )

    def fetch_base_rate_monthly(
        self,
        *,
        start_time: str = BASE_RATE_START,
        end_time: str | None = None,
    ) -> FetchResult:
        if end_time is None:
            today = date.today()
            end_time = f"{today.year:04d}{today.month:02d}"
        return self.fetch_series(
            stat_code=BASE_RATE_STAT_CODE,
            item_code=BASE_RATE_ITEM_CODE,
            cycle=BASE_RATE_CYCLE,
            start_time=start_time,
            end_time=end_time,
        )
