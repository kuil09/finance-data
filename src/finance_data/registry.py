from __future__ import annotations

from pathlib import Path
from typing import Any

from .datasets import treasury_debt
from .storage import DATASET_ID

SUPPORTED_DATASETS = (DATASET_ID,)


def sync_dataset(dataset_id: str, root: Path, **kwargs: Any):
    if dataset_id != DATASET_ID:
        raise KeyError(f"unsupported dataset: {dataset_id}")
    return treasury_debt.sync(root, **kwargs)


def rebuild_dataset(dataset_id: str, root: Path):
    if dataset_id != DATASET_ID:
        raise KeyError(f"unsupported dataset: {dataset_id}")
    return treasury_debt.rebuild(root)


def validate_dataset(dataset_id: str, root: Path):
    if dataset_id != DATASET_ID:
        raise KeyError(f"unsupported dataset: {dataset_id}")
    return treasury_debt.validate(root)
