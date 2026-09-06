from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from .. import storage_core
from ..nyfed import FetchResult, NYFedMarketsAdapter, SOFR_START_DATE

DATASET_ID = "us.money_market.sofr"
SOURCE_ID = "us_nyfed"
SOURCE_DATASET_ID = "rates.secured.sofr"
NORMALIZED_COLUMNS = (
    "period",
    "sofr_percent",
    "percentile_1_percent",
    "percentile_25_percent",
    "percentile_75_percent",
    "percentile_99_percent",
    "volume_billions",
    "revision_indicator",
    "footnote_id",
    "source_record_sha256",
)
STORAGE = storage_core.StorageSpec(
    source_id=SOURCE_ID,
    source_dataset_id=SOURCE_DATASET_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("effective_date",),
)
EXPECTED_SOURCE_FIELDS = frozenset(
    {
        "effective_date",
        "type",
        "percent_rate",
        "percentile_1",
        "percentile_25",
        "percentile_75",
        "percentile_99",
        "volume_billions",
        "revision_indicator",
        "footnote_id",
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


def _decimal(value: object, *, name: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise DatasetValidationError(f"{name} must be numeric: {value!r}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"{name} must be numeric: {value!r}") from exc
    if not parsed.is_finite():
        raise DatasetValidationError(f"{name} must be finite: {value!r}")
    if positive and parsed <= 0:
        raise DatasetValidationError(f"{name} must be positive: {value!r}")
    return parsed


def _optional_decimal(value: object, *, name: str) -> Decimal | None:
    if value in (None, "", "NA", "null"):
        return None
    return _decimal(value, name=name)


def _format_decimal(value: object, *, name: str, positive: bool = False) -> str:
    parsed = _decimal(value, name=name, positive=positive)
    return format(parsed, "f")


def _format_optional_decimal(value: object, *, name: str) -> str:
    parsed = _optional_decimal(value, name=name)
    return "null" if parsed is None else format(parsed, "f")


def validate_source_record(record: Mapping[str, object]) -> None:
    fields = frozenset(record)
    if fields != EXPECTED_SOURCE_FIELDS:
        raise DatasetValidationError(
            f"source schema mismatch; missing={sorted(EXPECTED_SOURCE_FIELDS-fields)}, "
            f"unexpected={sorted(fields-EXPECTED_SOURCE_FIELDS)}"
        )
    if record["type"] != "SOFR":
        raise DatasetValidationError(f"unexpected New York Fed rate type: {record['type']!r}")

    period = record["effective_date"]
    if not isinstance(period, str):
        raise DatasetValidationError("SOFR effective_date must be a string")
    try:
        parsed_period = date.fromisoformat(period)
    except ValueError as exc:
        raise DatasetValidationError(f"invalid SOFR effective date: {period!r}") from exc
    if parsed_period < SOFR_START_DATE:
        raise DatasetValidationError(f"SOFR date precedes official history: {period}")

    if not isinstance(record["revision_indicator"], str):
        raise DatasetValidationError("revision_indicator must be a string")
    footnote = record["footnote_id"]
    if footnote is not None and not isinstance(footnote, str):
        raise DatasetValidationError("footnote_id must be null or a string")

    rate = _decimal(record["percent_rate"], name="percent_rate")
    percentiles = [
        _optional_decimal(record["percentile_1"], name="percentile_1"),
        _optional_decimal(record["percentile_25"], name="percentile_25"),
        _optional_decimal(record["percentile_75"], name="percentile_75"),
        _optional_decimal(record["percentile_99"], name="percentile_99"),
    ]
    missing_percentiles = [value is None for value in percentiles]
    if any(missing_percentiles):
        if not all(missing_percentiles):
            raise DatasetValidationError(f"SOFR percentiles must be all present or all unavailable: {period}")
        if footnote is None:
            raise DatasetValidationError(f"unavailable SOFR percentiles require a source footnote: {period}")
    else:
        p1, p25, p75, p99 = percentiles
        assert p1 is not None and p25 is not None and p75 is not None and p99 is not None
        if not (p1 <= p25 <= p75 <= p99):
            raise DatasetValidationError(f"SOFR percentile ordering is invalid for {period}")
        if not (p1 <= rate <= p99):
            raise DatasetValidationError(f"SOFR rate falls outside published percentiles for {period}")

    values = [rate, *(value for value in percentiles if value is not None)]
    if any(value < Decimal("-10") or value > Decimal("100") for value in values):
        raise DatasetValidationError(f"SOFR percent value outside sanity bounds for {period}")

    _decimal(record["volume_billions"], name="volume_billions", positive=True)


def normalize_source_record(record: Mapping[str, object]) -> dict[str, str]:
    validate_source_record(record)
    return {
        "period": str(record["effective_date"]),
        "sofr_percent": _format_decimal(record["percent_rate"], name="percent_rate"),
        "percentile_1_percent": _format_optional_decimal(record["percentile_1"], name="percentile_1"),
        "percentile_25_percent": _format_optional_decimal(record["percentile_25"], name="percentile_25"),
        "percentile_75_percent": _format_optional_decimal(record["percentile_75"], name="percentile_75"),
        "percentile_99_percent": _format_optional_decimal(record["percentile_99"], name="percentile_99"),
        "volume_billions": _format_decimal(record["volume_billions"], name="volume_billions", positive=True),
        "revision_indicator": str(record["revision_indicator"]),
        "footnote_id": "" if record["footnote_id"] is None else str(record["footnote_id"]),
        "source_record_sha256": storage_core.source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, object]]:
    by_period: dict[str, dict[str, object]] = {}
    for _metadata, records in storage_core.iter_raw_snapshots(root, STORAGE):
        event_periods: set[str] = set()
        for record in records:
            validate_source_record(record)
            period = str(record["effective_date"])
            if period in event_periods:
                raise DatasetValidationError(f"duplicate SOFR period in one retrieval: {period}")
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

    previous: date | None = None
    by_period: dict[str, dict[str, str]] = {}
    revised = 0
    footnoted = 0
    percentile_unavailable = 0
    for row in normalized:
        if tuple(row) != NORMALIZED_COLUMNS:
            raise DatasetValidationError(f"unexpected SOFR normalized columns: {tuple(row)!r}")
        try:
            current = date.fromisoformat(row["period"])
        except ValueError as exc:
            raise DatasetValidationError(f"invalid normalized SOFR period: {row['period']!r}") from exc
        if current < SOFR_START_DATE:
            raise DatasetValidationError("normalized SOFR record predates official history")
        if previous is not None and current <= previous:
            raise DatasetValidationError("normalized SOFR periods are not strictly increasing")
        previous = current
        if row["period"] in by_period:
            raise DatasetValidationError(f"duplicate normalized SOFR period: {row['period']}")

        rate = _decimal(row["sofr_percent"], name="sofr_percent")
        percentiles = [
            _optional_decimal(row["percentile_1_percent"], name="percentile_1_percent"),
            _optional_decimal(row["percentile_25_percent"], name="percentile_25_percent"),
            _optional_decimal(row["percentile_75_percent"], name="percentile_75_percent"),
            _optional_decimal(row["percentile_99_percent"], name="percentile_99_percent"),
        ]
        missing_percentiles = [value is None for value in percentiles]
        if any(missing_percentiles):
            if not all(missing_percentiles):
                raise DatasetValidationError(f"normalized SOFR percentiles are partially missing: {row['period']}")
            if not row["footnote_id"]:
                raise DatasetValidationError(f"normalized unavailable percentiles lack footnote: {row['period']}")
            percentile_unavailable += 1
        else:
            p1, p25, p75, p99 = percentiles
            assert p1 is not None and p25 is not None and p75 is not None and p99 is not None
            if not (p1 <= p25 <= p75 <= p99 and p1 <= rate <= p99):
                raise DatasetValidationError(f"invalid normalized SOFR distribution: {row['period']}")

        _decimal(row["volume_billions"], name="volume_billions", positive=True)
        if len(row["source_record_sha256"]) != 64:
            raise DatasetValidationError("invalid SOFR source record hash")
        revised += int(bool(row["revision_indicator"]))
        footnoted += int(bool(row["footnote_id"]))
        by_period[row["period"]] = row

    for source in source_records:
        expected = normalize_source_record(source)
        if by_period.get(expected["period"]) != expected:
            raise DatasetValidationError(f"normalized SOFR does not match source: {expected['period']}")

    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
        "revised_records": revised,
        "footnoted_records": footnoted,
        "percentile_unavailable_records": percentile_unavailable,
        "unit": "percent_and_billions_usd",
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
    overlap_days: int = 21,
    adapter: NYFedMarketsAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    if overlap_days < 0:
        raise ValueError("overlap_days cannot be negative")
    adapter = adapter or NYFedMarketsAdapter()

    start_date = SOFR_START_DATE
    if not full:
        latest = latest_period(root)
        if latest is not None:
            start_date = max(SOFR_START_DATE, latest - timedelta(days=overlap_days))

    result: FetchResult = adapter.fetch_sofr(start_date=start_date)
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
