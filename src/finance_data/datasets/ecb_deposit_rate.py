from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from .. import storage_core
from ..ecb import (
    DEPOSIT_RATE_DATAFLOW,
    DEPOSIT_RATE_KEY,
    DEPOSIT_RATE_START,
    ECBDataAdapter,
)
from ..sdmx_csv import FetchResult

DATASET_ID = "ea.monetary_policy.deposit_facility_rate"
SOURCE_ID = "ecb_data_portal"
SOURCE_DATASET_ID = "FM.D.U2.EUR.4F.KR.DFR.LEV"
NORMALIZED_COLUMNS = (
    "period",
    "deposit_facility_rate_percent",
    "obs_status",
    "obs_conf",
    "obs_pre_break",
    "source_record_sha256",
)
STORAGE = storage_core.StorageSpec(
    source_id=SOURCE_ID,
    source_dataset_id=SOURCE_DATASET_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("TIME_PERIOD",),
)

EXPECTED_DIMENSIONS = {
    "KEY": SOURCE_DATASET_ID,
    "FREQ": "D",
    "REF_AREA": "U2",
    "CURRENCY": "EUR",
    "PROVIDER_FM": "4F",
    "INSTRUMENT_FM": "KR",
    "PROVIDER_FM_ID": "DFR",
    "DATA_TYPE_FM": "LEV",
    "TIME_FORMAT": "P1D",
    "UNIT": "PCPA",
    "UNIT_MULT": "0",
}
REQUIRED_FIELDS = frozenset(
    set(EXPECTED_DIMENSIONS)
    | {
        "TIME_PERIOD",
        "OBS_VALUE",
        "OBS_STATUS",
        "OBS_CONF",
        "OBS_PRE_BREAK",
    }
)


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


def _parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise DatasetValidationError(f"ECB period must be a string: {value!r}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DatasetValidationError(f"invalid ECB period: {value!r}") from exc
    if parsed < date.fromisoformat(DEPOSIT_RATE_START):
        raise DatasetValidationError(f"ECB period precedes selected coverage: {value}")
    return parsed


def _rate(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise DatasetValidationError(f"ECB deposit rate must be numeric: {value!r}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"ECB deposit rate must be numeric: {value!r}") from exc
    if not parsed.is_finite() or parsed < Decimal("-10") or parsed > Decimal("100"):
        raise DatasetValidationError(f"ECB deposit rate outside sanity bounds: {value!r}")
    return parsed


def validate_source_record(record: Mapping[str, object]) -> None:
    missing = REQUIRED_FIELDS - frozenset(record)
    if missing:
        raise DatasetValidationError(f"ECB source fields missing: {sorted(missing)}")
    for field, expected in EXPECTED_DIMENSIONS.items():
        if str(record.get(field, "")) != expected:
            raise DatasetValidationError(
                f"unexpected ECB dimension {field}: {record.get(field)!r}; expected {expected!r}"
            )
    _parse_date(record["TIME_PERIOD"])
    _rate(record["OBS_VALUE"])
    for field in ("OBS_STATUS", "OBS_CONF", "OBS_PRE_BREAK"):
        if not isinstance(record[field], str):
            raise DatasetValidationError(f"ECB {field} must be a string")


def normalize_source_record(record: Mapping[str, object]) -> dict[str, str]:
    validate_source_record(record)
    return {
        "period": str(record["TIME_PERIOD"]),
        "deposit_facility_rate_percent": format(_rate(record["OBS_VALUE"]), "f"),
        "obs_status": str(record["OBS_STATUS"]),
        "obs_conf": str(record["OBS_CONF"]),
        "obs_pre_break": str(record["OBS_PRE_BREAK"]),
        "source_record_sha256": storage_core.source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, object]]:
    by_period: dict[str, dict[str, object]] = {}
    for _metadata, records in storage_core.iter_raw_snapshots(root, STORAGE):
        seen: set[str] = set()
        for record in records:
            validate_source_record(record)
            period = str(record["TIME_PERIOD"])
            if period in seen:
                raise DatasetValidationError(f"duplicate ECB period in one retrieval: {period}")
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

    previous: date | None = None
    by_period: dict[str, dict[str, str]] = {}
    status_values: set[str] = set()
    confidence_values: set[str] = set()
    pre_break_records = 0
    for row in normalized:
        if tuple(row) != NORMALIZED_COLUMNS:
            raise DatasetValidationError(f"unexpected ECB normalized columns: {tuple(row)!r}")
        current = _parse_date(row["period"])
        if previous is not None and current != previous + timedelta(days=1):
            raise DatasetValidationError(
                f"ECB daily coverage is not contiguous: {previous.isoformat()} -> {current.isoformat()}"
            )
        previous = current
        if row["period"] in by_period:
            raise DatasetValidationError(f"duplicate normalized ECB period: {row['period']}")
        _rate(row["deposit_facility_rate_percent"])
        if len(row["source_record_sha256"]) != 64:
            raise DatasetValidationError("invalid ECB source record hash")
        status_values.add(row["obs_status"])
        confidence_values.add(row["obs_conf"])
        pre_break_records += int(bool(row["obs_pre_break"]))
        by_period[row["period"]] = row

    for source in source_records:
        expected = normalize_source_record(source)
        if by_period.get(expected["period"]) != expected:
            raise DatasetValidationError(
                f"normalized ECB deposit rate does not match source: {expected['period']}"
            )

    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
        "source_dataflow": DEPOSIT_RATE_DATAFLOW,
        "source_key": DEPOSIT_RATE_KEY,
        "observation_statuses": sorted(status_values),
        "observation_confidentiality": sorted(confidence_values),
        "pre_break_records": pre_break_records,
        "unit": "percent_per_annum",
        "status": "PASS",
    }


def sync(
    root: Path,
    *,
    full: bool = False,
    adapter: ECBDataAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    # The selected series is about 3 MB / 10k rows. Full retrieval is intentional:
    # it preserves corrections anywhere in history without inventing a revision window.
    _ = full
    adapter = adapter or ECBDataAdapter()
    result: FetchResult = adapter.fetch_deposit_facility_rate()
    if not result.records:
        raise DatasetValidationError("ECB deposit facility rate response is empty")
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
