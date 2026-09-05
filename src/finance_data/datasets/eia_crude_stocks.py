from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from .. import storage_core
from ..eia import (
    COMMERCIAL_CRUDE_STOCKS_SERIES,
    EIAOpenDataAdapter,
    FetchResult,
)

DATASET_ID = "us.energy.petroleum.crude_oil.commercial_stocks"
SOURCE_ID = "us_eia"
SOURCE_DATASET_ID = "PET.WCESTUS1.W"
NORMALIZED_COLUMNS = (
    "period",
    "stock_thousand_barrels",
    "source_record_sha256",
)
STORAGE = storage_core.StorageSpec(
    source_id=SOURCE_ID,
    source_dataset_id=SOURCE_DATASET_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("period",),
)
EXPECTED_SOURCE_FIELDS = frozenset({"period", "series_id", "value"})


class DatasetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SyncSummary:
    dataset: str
    fetched_records: int
    source_total_count: int
    raw_snapshot_created: bool
    normalized_records: int
    latest_period: str | None
    changed_partitions: int
    validation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "fetched_records": self.fetched_records,
            "source_total_count": self.source_total_count,
            "raw_snapshot_created": self.raw_snapshot_created,
            "normalized_records": self.normalized_records,
            "latest_period": self.latest_period,
            "changed_partitions": self.changed_partitions,
            "validation": self.validation,
        }


def validate_source_record(record: Mapping[str, object]) -> None:
    fields = frozenset(record)
    if fields != EXPECTED_SOURCE_FIELDS:
        missing = sorted(EXPECTED_SOURCE_FIELDS - fields)
        unexpected = sorted(fields - EXPECTED_SOURCE_FIELDS)
        raise DatasetValidationError(
            f"source schema mismatch; missing={missing}, unexpected={unexpected}"
        )
    if record["series_id"] != COMMERCIAL_CRUDE_STOCKS_SERIES:
        raise DatasetValidationError(f"unexpected EIA series id: {record['series_id']!r}")
    period = record["period"]
    if not isinstance(period, str):
        raise DatasetValidationError("source period must be a string")
    try:
        datetime.strptime(period, "%Y%m%d")
    except ValueError as exc:
        raise DatasetValidationError(f"invalid EIA source period: {period!r}") from exc
    value = record["value"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise DatasetValidationError(f"EIA stock value must be an integer: {value!r}")
    if value < 0:
        raise DatasetValidationError("EIA stock value must be non-negative")


def normalize_source_record(record: Mapping[str, object]) -> dict[str, str]:
    validate_source_record(record)
    source_period = str(record["period"])
    period = datetime.strptime(source_period, "%Y%m%d").date().isoformat()
    return {
        "period": period,
        "stock_thousand_barrels": str(record["value"]),
        "source_record_sha256": storage_core.source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, object]]:
    by_period: dict[str, dict[str, object]] = {}
    for _metadata, records in storage_core.iter_raw_snapshots(root, STORAGE):
        for record in records:
            validate_source_record(record)
            by_period[str(record["period"])] = record
    return [by_period[key] for key in sorted(by_period)]


def rebuild(root: Path) -> list[dict[str, str]]:
    rows = [normalize_source_record(record) for record in canonical_source_records(root)]
    storage_core.write_normalized_rows(root, STORAGE, rows)
    validate(root)
    return rows


def validate(root: Path) -> dict[str, object]:
    source_records = canonical_source_records(root)
    normalized = storage_core.read_normalized_rows(root, STORAGE)
    if len(source_records) != len(normalized):
        raise DatasetValidationError(
            f"record count mismatch: source={len(source_records)} normalized={len(normalized)}"
        )

    previous: str | None = None
    by_period: dict[str, dict[str, str]] = {}
    for row in normalized:
        if tuple(row) != NORMALIZED_COLUMNS:
            raise DatasetValidationError(f"unexpected normalized columns: {tuple(row)!r}")
        period = row["period"]
        try:
            date.fromisoformat(period)
        except ValueError as exc:
            raise DatasetValidationError(f"invalid normalized period: {period!r}") from exc
        if period in by_period:
            raise DatasetValidationError(f"duplicate normalized period: {period}")
        if previous is not None and period <= previous:
            raise DatasetValidationError("normalized records are not strictly ordered")
        previous = period
        try:
            stock = int(row["stock_thousand_barrels"])
        except ValueError as exc:
            raise DatasetValidationError("normalized stock must be an integer") from exc
        if stock < 0:
            raise DatasetValidationError("normalized stock must be non-negative")
        if len(row["source_record_sha256"]) != 64:
            raise DatasetValidationError("invalid normalized source record hash")
        by_period[period] = row

    for source in source_records:
        expected = normalize_source_record(source)
        actual = by_period.get(expected["period"])
        if actual != expected:
            raise DatasetValidationError(
                f"normalized record does not match source: {expected['period']}"
            )

    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
        "unit": "thousand_barrels",
        "source_series": COMMERCIAL_CRUDE_STOCKS_SERIES,
        "status": "PASS",
    }


def latest_period(root: Path) -> date | None:
    rows = storage_core.read_normalized_rows(root, STORAGE)
    if not rows:
        return None
    return date.fromisoformat(rows[-1]["period"])


def sync(
    root: Path,
    *,
    full: bool = False,
    overlap_days: int = 35,
    adapter: EIAOpenDataAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    if overlap_days < 0:
        raise ValueError("overlap_days cannot be negative")
    adapter = adapter or EIAOpenDataAdapter()

    start_date: date | None = None
    if not full:
        latest = latest_period(root)
        if latest is not None:
            start_date = latest - timedelta(days=overlap_days)

    result: FetchResult = adapter.fetch_series(
        COMMERCIAL_CRUDE_STOCKS_SERIES,
        start_date=start_date,
    )
    for record in result.records:
        validate_source_record(record)

    _snapshot, raw_created = storage_core.store_raw_snapshot(
        root,
        STORAGE,
        result.records,
        request=result.request,
        pages=result.pages,
        source_total_count=result.source_total_count,
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
    )

    before = {
        row["period"]: row for row in storage_core.read_normalized_rows(root, STORAGE)
    }
    rows = rebuild(root)
    after = {row["period"]: row for row in rows}
    changed_years = {
        period[:4]
        for period in set(before) | set(after)
        if before.get(period) != after.get(period)
    }
    report = validate(root)
    return SyncSummary(
        dataset=DATASET_ID,
        fetched_records=len(result.records),
        source_total_count=result.source_total_count,
        raw_snapshot_created=raw_created,
        normalized_records=len(rows),
        latest_period=report["latest_period"],
        changed_partitions=len(changed_years),
        validation="PASS",
    )
