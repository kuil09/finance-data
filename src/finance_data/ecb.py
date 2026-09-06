from __future__ import annotations

from dataclasses import dataclass

from .sdmx_csv import FetchResult, SDMXCSVAdapter

ECB_DATA_API_BASE_URL = "https://data-api.ecb.europa.eu/service"
DEPOSIT_RATE_DATAFLOW = "FM"
DEPOSIT_RATE_KEY = "D.U2.EUR.4F.KR.DFR.LEV"
DEPOSIT_RATE_START = "1999-01-01"


@dataclass(frozen=True)
class ECBDataAdapter:
    sdmx: SDMXCSVAdapter

    def __init__(self, *, sdmx: SDMXCSVAdapter | None = None) -> None:
        object.__setattr__(
            self,
            "sdmx",
            sdmx or SDMXCSVAdapter(base_url=ECB_DATA_API_BASE_URL),
        )

    def fetch_deposit_facility_rate(self) -> FetchResult:
        return self.sdmx.fetch(
            dataflow=DEPOSIT_RATE_DATAFLOW,
            key=DEPOSIT_RATE_KEY,
            start_period=DEPOSIT_RATE_START,
            detail="full",
        )
