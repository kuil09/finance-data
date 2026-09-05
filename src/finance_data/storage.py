from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import storage_core

RAW_SOURCE_ID = "us_treasury_fiscal_data"
RAW_TABLE_ID = "debt_to_penny"
DATASET_ID = "us.fiscal.debt.outstanding"
NORMALIZED_COLUMNS = (
    "period",
    "debt_held_by_public",
    "intragovernmental_holdings",
    "total_public_debt_outstanding",
    "source_line_number",
    "source_record_sha256",
)

TREASURY_STORAGE = storage_core.StorageSpec(
    source_id=RAW_SOURCE_ID,
    source_dataset_id=RAW_TABLE_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("record_date", "src_line_nbr"),
)

canonical_json_bytes = storage_core.canonical_json_bytes
source_record_bytes = storage_core.source_record_bytes
source_record_hash = storage_core.source_record_hash


def snapshot_payload(records: Sequence[Mapping[str, str]]) -> bytes:
    return storage_core.snapshot_payload(TREASURY_STORAGE, records)


def snapshot_hash(records: Sequence[Mapping[str, str]]) -> str:
    return storage_core.snapshot_hash(TREASURY_STORAGE, records)


def raw_root(root: Path) -> Path:
    return storage_core.raw_root(root, TREASURY_STORAGE)


def receipt_root(root: Path) -> Path:
    return storage_core.receipt_root(root, TREASURY_STORAGE)


def normalized_root(root: Path) -> Path:
    return storage_core.normalized_root(root, TREASURY_STORAGE)


def store_raw_snapshot(
    root: Path,
    records: Sequence[Mapping[str, str]],
    *,
    request: Mapping[str, object],
    pages: int,
    source_total_count: int | None,
    retrieved_at: datetime | None = None,
) -> tuple[Path, bool]:
    return storage_core.store_raw_snapshot(
        root,
        TREASURY_STORAGE,
        records,
        request=request,
        pages=pages,
        source_total_count=source_total_count,
        retrieved_at=retrieved_at,
    )


def iter_raw_snapshots(
    root: Path,
) -> Iterable[tuple[dict[str, object], list[dict[str, str]]]]:
    for metadata, records in storage_core.iter_raw_snapshots(root, TREASURY_STORAGE):
        yield metadata, [
            {str(key): str(value) for key, value in record.items()} for record in records
        ]


def read_normalized_rows(root: Path) -> list[dict[str, str]]:
    return storage_core.read_normalized_rows(root, TREASURY_STORAGE)


def write_normalized_rows(
    root: Path, rows: Sequence[Mapping[str, str]]
) -> list[Path]:
    return storage_core.write_normalized_rows(root, TREASURY_STORAGE, rows)
