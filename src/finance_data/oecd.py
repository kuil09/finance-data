from __future__ import annotations

from dataclasses import dataclass

from .sdmx_csv import FetchResult, SDMXCSVAdapter

OECD_SDMX_BASE_URL = "https://sdmx.oecd.org/public/rest"
CLI_DATAFLOW = "OECD.SDD.STES,DSD_STES@DF_CLI,4.1"
CLI_KEY = ".M.LI...AA...H"


@dataclass(frozen=True)
class OECDDataAdapter:
    sdmx: SDMXCSVAdapter

    def __init__(self, *, sdmx: SDMXCSVAdapter | None = None) -> None:
        object.__setattr__(
            self,
            "sdmx",
            sdmx or SDMXCSVAdapter(base_url=OECD_SDMX_BASE_URL),
        )

    def fetch_composite_leading_indicator(self) -> FetchResult:
        return self.sdmx.fetch(
            dataflow=CLI_DATAFLOW,
            key=CLI_KEY,
            detail="full",
            extra_params={"dimensionAtObservation": "AllDimensions"},
        )
