from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from .. import storage_core
from ..bea import (
    BEANIPAFlatFileAdapter,
    FetchResult,
    GDP_LINE_NUMBER,
    GDP_SERIES_CODE,
    GDP_TABLE_ID,
)

DATASET_ID = "us.national_accounts.gdp.current_dollars"
SOURCE_ID = "us_bea"
SOURCE_DATASET_ID = "NIPA.T10105.A191RC.Q"
NORMALIZED_COLUMNS = (
    "period",
    "gdp_millions_current_dollars",
    "source_series_code",
    "source_table_id",
    "source_line_number",
    "source_record_sha256",
)
STORAGE = storage_core.StorageSpec(
    source_id=SOURCE_ID,
    source_dataset_id=SOURCE_DATASET_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("period",),
)
EXPECTED_SOURCE_FIELDS = frozenset(
    {
        "series_code",
        "period",
        "value",
        "series_label",
        "metric_name",
        "calculation_type",
        "default_scale",
        "table_id",
        "line_number",
        "table_title",
    }
)


class DatasetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SyncSummary:
    dataset: str
    fetched_records: int
    raw_snapshot_created: bool
    normalized_records: int
    latest_period: str | None
    changed_partitions: int
    validation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "fetched_records": self.fetched_records,
            "raw_snapshot_created": self.raw_snapshot_created,
            "normalized_records": self.normalized_records,
            "latest_period": self.latest_period,
            "changed_partitions": self.changed_partitions,
            "validation": self.validation,
        }


def _quarter(period: str) -> tuple[int, int]:
    if len(period) != 6 or period[4] != "Q" or period[5] not in "1234":
        raise DatasetValidationError(f"invalid BEA quarterly period: {period!r}")
    try:
        year = int(period[:4])
    except ValueError as exc:
        raise DatasetValidationError(f"invalid BEA quarterly year: {period!r}") from exc
    if year < 1947:
        raise DatasetValidationError(f"BEA quarterly GDP precedes expected coverage: {period}")
    return year, int(period[5])


def canonical_period(period: str) -> str:
    year, quarter = _quarter(period)
    return f"{year:04d}-Q{quarter}"


def _value(value: str) -> str:
    cleaned = value.replace(",", "").strip()
    try:
        parsed = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"invalid BEA GDP value: {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise DatasetValidationError(f"BEA GDP value must be positive and finite: {value!r}")
    return format(parsed, "f")


def validate_source_record(record: Mapping[str, object]) -> None:
    fields = frozenset(record)
    if fields != EXPECTED_SOURCE_FIELDS:
        raise DatasetValidationError(
            f"source schema mismatch; missing={sorted(EXPECTED_SOURCE_FIELDS-fields)}, "
            f"unexpected={sorted(fields-EXPECTED_SOURCE_FIELDS)}"
        )
    if record["series_code"] != GDP_SERIES_CODE:
        raise DatasetValidationError(f"unexpected BEA GDP series: {record['series_code']!r}")
    if record["table_id"] != GDP_TABLE_ID or str(record["line_number"]) != GDP_LINE_NUMBER:
        raise DatasetValidationError(
            f"unexpected BEA GDP table identity: {record['table_id']}:{record['line_number']}"
        )
    if record["series_label"] != "Gross domestic product":
        raise DatasetValidationError(f"unexpected BEA GDP label: {record['series_label']!r}")
    if record["metric_name"] != "Current Dollars":
        raise DatasetValidationError(f"unexpected BEA GDP metric: {record['metric_name']!r}")
    if record["calculation_type"] != "Level":
        raise DatasetValidationError(f"unexpected BEA GDP calculation type: {record['calculation_type']!r}")
    if str(record["default_scale"]) != "-6":
        raise DatasetValidationError(f"unexpected BEA GDP default scale: {record['default_scale']!r}")
    if record["table_title"] != "Table 1.1.5. Gross Domestic Product":
        raise DatasetValidationError(f"unexpected BEA GDP table title: {record['table_title']!r}")
    _quarter(str(record["period"]))
    _value(str(record["value"]))


def normalize_source_record(record: Mapping[str, object]) -> dict[str, str]:
    validate_source_record(record)
    return {
        "period": canonical_period(str(record["period"])),
        "gdp_millions_current_dollars": _value(str(record["value"])),
        "source_series_code": GDP_SERIES_CODE,
        "source_table_id": GDP_TABLE_ID,
        "source_line_number": GDP_LINE_NUMBER,
        "source_record_sha256": storage_core.source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, object]]:
    by_period: dict[str, dict[str, object]] = {}
    for _metadata, records in storage_core.iter_raw_snapshots(root, STORAGE):
        event_periods: set[str] = set()
        for record in records:
            validate_source_record(record)
            period = canonical_period(str(record["period"]))
            if period in event_periods:
                raise DatasetValidationError(f"duplicate BEA GDP period in one retrieval: {period}")
            event_periods.add(period)
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

    previous: tuple[int, int] | None = None
    by_period: dict[str, dict[str, str]] = {}
    for row in normalized:
        period = row["period"]
        if len(period) != 7 or period[4:6] != "-Q" or period[6] not in "1234":
            raise DatasetValidationError(f"invalid normalized BEA period: {period!r}")
        current = (int(period[:4]), int(period[6]))
        if previous is not None:
            expected = (previous[0] + 1, 1) if previous[1] == 4 else (previous[0], previous[1] + 1)
            if current != expected:
                raise DatasetValidationError(
                    f"BEA GDP quarterly coverage is not contiguous: expected {expected}, got {current}"
                )
        previous = current
        if period in by_period:
            raise DatasetValidationError(f"duplicate normalized BEA GDP period: {period}")
        _value(row["gdp_millions_current_dollars"])
        if row["source_series_code"] != GDP_SERIES_CODE:
            raise DatasetValidationError("BEA source series identity was not preserved")
        if row["source_table_id"] != GDP_TABLE_ID or row["source_line_number"] != GDP_LINE_NUMBER:
            raise DatasetValidationError("BEA table/line identity was not preserved")
        if len(row["source_record_sha256"]) != 64:
            raise DatasetValidationError("invalid BEA source record hash")
        by_period[period] = row

    for source in source_records:
        expected = normalize_source_record(source)
        if by_period.get(expected["period"]) != expected:
            raise DatasetValidationError(f"normalized BEA GDP does not match source: {expected['period']}")

    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
        "source_series": GDP_SERIES_CODE,
        "source_table": GDP_TABLE_ID,
        "source_line": GDP_LINE_NUMBER,
        "unit": "millions_current_dollars_saar",
        "status": "PASS",
    }


def sync(
    root: Path,
    *,
    full: bool = False,
    adapter: BEANIPAFlatFileAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    # BEA publishes the complete quarterly flat file and annual/comprehensive
    # revisions can reach far back in history, so every sync intentionally
    # fetches the complete selected series rather than using a short overlap.
    _ = full
    adapter = adapter or BEANIPAFlatFileAdapter()
    result: FetchResult = adapter.fetch_gdp_current_dollars()
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
        raw_snapshot_created=raw_created,
        normalized_records=len(rows),
        latest_period=report["latest_period"],
        changed_partitions=len(changed_years),
        validation="PASS",
    )
