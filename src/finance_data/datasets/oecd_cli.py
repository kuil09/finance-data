from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from .. import storage_core
from ..oecd import CLI_DATAFLOW, CLI_KEY, OECDDataAdapter
from ..sdmx_csv import FetchResult

DATASET_ID = "global.leading_indicators.oecd_cli"
SOURCE_ID = "oecd_data_explorer"
SOURCE_DATASET_ID = "OECD.SDD.STES.DSD_STES_DF_CLI.4.1.M.LI.AA.H"
NORMALIZED_COLUMNS = (
    "period",
    "ref_area",
    "cli_index",
    "obs_status",
    "source_record_sha256",
)
STORAGE = storage_core.StorageSpec(
    source_id=SOURCE_ID,
    source_dataset_id=SOURCE_DATASET_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("TIME_PERIOD", "REF_AREA"),
)

EXPECTED_DIMENSIONS = {
    "DATAFLOW": "OECD.SDD.STES:DSD_STES@DF_CLI(4.1)",
    "FREQ": "M",
    "MEASURE": "LI",
    "UNIT_MEASURE": "IX",
    "ACTIVITY": "_Z",
    "ADJUSTMENT": "AA",
    "TRANSFORMATION": "IX",
    "TIME_HORIZ": "_Z",
    "METHODOLOGY": "H",
    "UNIT_MULT": "0",
}
REQUIRED_FIELDS = frozenset(
    set(EXPECTED_DIMENSIONS)
    | {"REF_AREA", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "DECIMALS", "BASE_PER"}
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
    reference_areas: int
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
            "reference_areas": self.reference_areas,
            "changed_partitions": self.changed_partitions,
            "validation": self.validation,
        }


def _month(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        raise DatasetValidationError(f"invalid OECD monthly period: {value!r}")
    try:
        year = int(value[:4])
        month = int(value[5:7])
    except ValueError as exc:
        raise DatasetValidationError(f"invalid OECD monthly period: {value!r}") from exc
    if year < 1900 or not 1 <= month <= 12:
        raise DatasetValidationError(f"invalid OECD monthly period: {value!r}")
    return year, month


def _value(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise DatasetValidationError(f"OECD CLI value must be numeric: {value!r}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"OECD CLI value must be numeric: {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("200"):
        raise DatasetValidationError(f"OECD CLI value outside sanity bounds: {value!r}")
    return parsed


def validate_source_record(record: Mapping[str, object]) -> None:
    missing = REQUIRED_FIELDS - frozenset(record)
    if missing:
        raise DatasetValidationError(f"OECD CLI source fields missing: {sorted(missing)}")
    for field, expected in EXPECTED_DIMENSIONS.items():
        if str(record.get(field, "")) != expected:
            raise DatasetValidationError(
                f"unexpected OECD CLI dimension {field}: {record.get(field)!r}; expected {expected!r}"
            )
    ref_area = record["REF_AREA"]
    if not isinstance(ref_area, str) or not ref_area.strip():
        raise DatasetValidationError("OECD CLI REF_AREA must be non-empty")
    _month(record["TIME_PERIOD"])
    _value(record["OBS_VALUE"])
    if not isinstance(record["OBS_STATUS"], str):
        raise DatasetValidationError("OECD CLI OBS_STATUS must be a string")


def normalize_source_record(record: Mapping[str, object]) -> dict[str, str]:
    validate_source_record(record)
    return {
        "period": str(record["TIME_PERIOD"]),
        "ref_area": str(record["REF_AREA"]),
        "cli_index": format(_value(record["OBS_VALUE"]), "f"),
        "obs_status": str(record["OBS_STATUS"]),
        "source_record_sha256": storage_core.source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, object]]:
    by_key: dict[tuple[str, str], dict[str, object]] = {}
    for _metadata, records in storage_core.iter_raw_snapshots(root, STORAGE):
        event_keys: set[tuple[str, str]] = set()
        for record in records:
            validate_source_record(record)
            key = (str(record["REF_AREA"]), str(record["TIME_PERIOD"]))
            if key in event_keys:
                raise DatasetValidationError(
                    f"duplicate OECD CLI observation in one retrieval: {key[0]} {key[1]}"
                )
            event_keys.add(key)
            by_key[key] = record
    return [by_key[key] for key in sorted(by_key, key=lambda item: (item[1], item[0]))]


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

    by_key: dict[tuple[str, str], dict[str, str]] = {}
    last_by_area: dict[str, tuple[int, int]] = {}
    status_values: set[str] = set()
    for row in normalized:
        if tuple(row) != NORMALIZED_COLUMNS:
            raise DatasetValidationError(f"unexpected OECD CLI normalized columns: {tuple(row)!r}")
        ref_area = row["ref_area"]
        if not ref_area:
            raise DatasetValidationError("normalized OECD CLI REF_AREA is empty")
        current = _month(row["period"])
        previous = last_by_area.get(ref_area)
        if previous is not None and current <= previous:
            raise DatasetValidationError(
                f"OECD CLI periods are not increasing for {ref_area}: {row['period']}"
            )
        last_by_area[ref_area] = current
        key = (ref_area, row["period"])
        if key in by_key:
            raise DatasetValidationError(f"duplicate normalized OECD CLI observation: {key}")
        _value(row["cli_index"])
        if len(row["source_record_sha256"]) != 64:
            raise DatasetValidationError("invalid OECD CLI source record hash")
        status_values.add(row["obs_status"])
        by_key[key] = row

    for source in source_records:
        expected = normalize_source_record(source)
        key = (expected["ref_area"], expected["period"])
        if by_key.get(key) != expected:
            raise DatasetValidationError(
                f"normalized OECD CLI does not match source: {expected['ref_area']} {expected['period']}"
            )

    periods = [row["period"] for row in normalized]
    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "reference_areas": len(last_by_area),
        "first_period": min(periods) if periods else None,
        "latest_period": max(periods) if periods else None,
        "source_dataflow": CLI_DATAFLOW,
        "source_key": CLI_KEY,
        "observation_statuses": sorted(status_values),
        "unit": "amplitude_adjusted_index",
        "status": "PASS",
    }


def sync(
    root: Path,
    *,
    full: bool = False,
    adapter: OECDDataAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    # The complete headline CLI slice is small enough to refetch on every run.
    # Full snapshots preserve revisions across all countries and historical periods.
    _ = full
    adapter = adapter or OECDDataAdapter()
    result: FetchResult = adapter.fetch_composite_leading_indicator()
    if not result.records:
        raise DatasetValidationError("OECD CLI response is empty")
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
        (row["ref_area"], row["period"]): row
        for row in storage_core.read_normalized_rows(root, STORAGE)
    }
    rows = rebuild(root)
    after = {(row["ref_area"], row["period"]): row for row in rows}
    changed_years = {
        key[1][:4]
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }
    report = validate(root)
    return SyncSummary(
        dataset=DATASET_ID,
        fetched_records=len(result.records),
        source_total_count=result.source_total_count,
        raw_snapshot_created=raw_created,
        normalized_records=len(rows),
        latest_period=report["latest_period"],
        reference_areas=int(report["reference_areas"]),
        changed_partitions=len(changed_years),
        validation="PASS",
    )
