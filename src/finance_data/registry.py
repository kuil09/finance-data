from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .datasets import (
    bea_gdp,
    bls_cpi,
    bok_base_rate,
    eia_crude_stocks,
    nyfed_sofr,
    treasury_debt,
)
from .storage import DATASET_ID as TREASURY_DATASET_ID


@dataclass(frozen=True)
class DatasetHandler:
    sync: Callable[..., object]
    rebuild: Callable[[Path], object]
    validate: Callable[[Path], object]


DATASET_HANDLERS: dict[str, DatasetHandler] = {
    TREASURY_DATASET_ID: DatasetHandler(
        sync=treasury_debt.sync,
        rebuild=treasury_debt.rebuild,
        validate=treasury_debt.validate,
    ),
    eia_crude_stocks.DATASET_ID: DatasetHandler(
        sync=eia_crude_stocks.sync,
        rebuild=eia_crude_stocks.rebuild,
        validate=eia_crude_stocks.validate,
    ),
    bls_cpi.DATASET_ID: DatasetHandler(
        sync=bls_cpi.sync,
        rebuild=bls_cpi.rebuild,
        validate=bls_cpi.validate,
    ),
    bea_gdp.DATASET_ID: DatasetHandler(
        sync=bea_gdp.sync,
        rebuild=bea_gdp.rebuild,
        validate=bea_gdp.validate,
    ),
    nyfed_sofr.DATASET_ID: DatasetHandler(
        sync=nyfed_sofr.sync,
        rebuild=nyfed_sofr.rebuild,
        validate=nyfed_sofr.validate,
    ),
    bok_base_rate.DATASET_ID: DatasetHandler(
        sync=bok_base_rate.sync,
        rebuild=bok_base_rate.rebuild,
        validate=bok_base_rate.validate,
    ),
}
SUPPORTED_DATASETS = tuple(sorted(DATASET_HANDLERS))


def _handler(dataset_id: str) -> DatasetHandler:
    try:
        return DATASET_HANDLERS[dataset_id]
    except KeyError as exc:
        raise KeyError(f"unsupported dataset: {dataset_id}") from exc


def sync_dataset(dataset_id: str, root: Path, **kwargs: Any):
    return _handler(dataset_id).sync(root, **kwargs)


def rebuild_dataset(dataset_id: str, root: Path):
    return _handler(dataset_id).rebuild(root)


def validate_dataset(dataset_id: str, root: Path):
    return _handler(dataset_id).validate(root)
