from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from .. import storage_core
from ..bok_ecos import (
    BASE_RATE_CYCLE,
    BASE_RATE_ITEM_CODE,
    BASE_RATE_START,
    BASE_RATE_STAT_CODE,
    ECOSAdapter,
    FetchResult,
)

DATASET_ID = "kr.monetary_policy.base_rate"
SOURCE_ID = "kr_bok_ecos"
SOURCE_DATASET_ID = "722Y001.0101000.M"
NORMALIZED_COLUMNS = (
    "period",
    "base_rate_percent",
    "source_record_sha256",
)
STORAGE = storage_core.StorageSpec(
    source_id=SOURCE_ID,
    source_dataset_id=SOURCE_DATASET_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("time",),
)
EXPECTED_SOURCE_FIELDS = frozenset(
    {"time", "stat_code", "stat_name", "item_code", "item_name", "unit_name", "data_value"}
)
EXPECTED_STAT_NAME = "1.3.1. 한국은행 기준금리 및 여수신금리"
EXPECTED_ITEM_NAME = "한국은행 기준금리"
EXPECTED_UNIT = "연%"


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


def _parse_month(value: str) -> tuple[int, int]:
    if len(value) != 6 or not value.isdigit():
        raise DatasetValidationError(f"invalid ECOS monthly period: {value!r}")
    year = int(value[:4])
    month = int(value[4:])
    if month < 1 or month > 12:
        raise DatasetValidationError(f"invalid ECOS month: {value!r}")
    return year, month


def _period(value: str) -> str:
    year, month = _parse_month(value)
    return f"{year:04d}-{month:02d}"


def _month_index(period: str) -> int:
    year, month = map(int, period.split("-"))
    return year * 12 + month


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise DatasetValidationError(f"base rate must be numeric: {value!r}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"base rate must be numeric: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > 100:
        raise DatasetValidationError(f"base rate outside valid percent bounds: {value!r}")
    return parsed


def validate_source_record(record: Mapping[str, object]) -> None:
    fields = frozenset(record)
    if fields != EXPECTED_SOURCE_FIELDS:
        raise DatasetValidationError(
            f"source schema mismatch; missing={sorted(EXPECTED_SOURCE_FIELDS-fields)}, "
            f"unexpected={sorted(fields-EXPECTED_SOURCE_FIELDS)}"
        )
    if record["stat_code"] != BASE_RATE_STAT_CODE:
        raise DatasetValidationError(f"unexpected ECOS stat code: {record['stat_code']!r}")
    if record["item_code"] != BASE_RATE_ITEM_CODE:
        raise DatasetValidationError(f"unexpected ECOS item code: {record['item_code']!r}")
    if record["stat_name"] != EXPECTED_STAT_NAME:
        raise DatasetValidationError(f"unexpected ECOS stat name: {record['stat_name']!r}")
    if record["item_name"] != EXPECTED_ITEM_NAME:
        raise DatasetValidationError(f"unexpected ECOS item name: {record['item_name']!r}")
    if record["unit_name"] != EXPECTED_UNIT:
        raise DatasetValidationError(f"unexpected ECOS unit: {record['unit_name']!r}")
    if not isinstance(record["time"], str):
        raise DatasetValidationError("ECOS time must be a string")
    _parse_month(str(record["time"]))
    _decimal(record["data_value"])


def normalize_source_record(record: Mapping[str, object]) -> dict[str, str]:
    validate_source_record(record)
    value = _decimal(record["data_value"])
    return {
        "period": _period(str(record["time"])),
        "base_rate_percent": format(value, "f"),
        "source_record_sha256": storage_core.source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, object]]:
    by_period: dict[str, dict[str, object]] = {}
    for _metadata, records in storage_core.iter_raw_snapshots(root, STORAGE):
        seen: set[str] = set()
        for record in records:
            validate_source_record(record)
            period = str(record["time"])
            if period in seen:
                raise DatasetValidationError(f"duplicate ECOS month in one retrieval: {period}")
            seen.add(period)
            by_period[period] = record
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

    previous_index: int | None = None
    by_period: dict[str, dict[str, str]] = {}
    for row in normalized:
        if tuple(row) != NORMALIZED_COLUMNS:
            raise DatasetValidationError(f"unexpected base-rate columns: {tuple(row)!r}")
        period = row["period"]
        try:
            year, month = map(int, period.split("-"))
            date(year, month, 1)
        except (ValueError, TypeError) as exc:
            raise DatasetValidationError(f"invalid normalized monthly period: {period!r}") from exc
        current_index = _month_index(period)
        if previous_index is not None and current_index != previous_index + 1:
            raise DatasetValidationError(f"gap or disorder in monthly base-rate history at {period}")
        previous_index = current_index
        if period in by_period:
            raise DatasetValidationError(f"duplicate normalized base-rate period: {period}")
        _decimal(row["base_rate_percent"])
        if len(row["source_record_sha256"]) != 64:
            raise DatasetValidationError("invalid base-rate source record hash")
        by_period[period] = row

    for source in source_records:
        expected = normalize_source_record(source)
        if by_period.get(expected["period"]) != expected:
            raise DatasetValidationError(
                f"normalized base rate does not match ECOS source: {expected['period']}"
            )

    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
        "source_stat_code": BASE_RATE_STAT_CODE,
        "source_item_code": BASE_RATE_ITEM_CODE,
        "source_cycle": BASE_RATE_CYCLE,
        "unit": "annual_percent",
        "status": "PASS",
    }


def latest_period(root: Path) -> str | None:
    rows = storage_core.read_normalized_rows(root, STORAGE)
    return rows[-1]["period"] if rows else None


def _subtract_months(period: str, months: int) -> str:
    if months < 0:
        raise ValueError("overlap_months cannot be negative")
    year, month = map(int, period.split("-"))
    index = year * 12 + (month - 1) - months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def sync(
    root: Path,
    *,
    full: bool = False,
    overlap_months: int = 24,
    adapter: ECOSAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    if overlap_months < 0:
        raise ValueError("overlap_months cannot be negative")
    adapter = adapter or ECOSAdapter()

    start_time = BASE_RATE_START
    if not full:
        latest = latest_period(root)
        if latest is not None:
            start_time = max(BASE_RATE_START, _subtract_months(latest, overlap_months))

    result: FetchResult = adapter.fetch_base_rate_monthly(start_time=start_time)
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

    before = {row["period"]: row for row in storage_core.read_normalized_rows(root, STORAGE)}
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
