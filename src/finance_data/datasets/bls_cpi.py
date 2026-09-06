from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from .. import storage_core
from ..bls import BLSPublicDataAdapter, CPI_U_ALL_ITEMS_SERIES, FetchResult

DATASET_ID = "us.prices.cpi_u.all_items"
SOURCE_ID = "us_bls"
SOURCE_DATASET_ID = CPI_U_ALL_ITEMS_SERIES
EARLIEST_YEAR = 1913
NORMALIZED_COLUMNS = (
    "period",
    "index_value",
    "observation_status",
    "footnote_codes",
    "footnote_text",
    "source_latest",
    "source_record_sha256",
)
STORAGE = storage_core.StorageSpec(
    source_id=SOURCE_ID,
    source_dataset_id=SOURCE_DATASET_ID,
    dataset_id=DATASET_ID,
    normalized_columns=NORMALIZED_COLUMNS,
    raw_sort_fields=("year", "period"),
)
REQUIRED_SOURCE_FIELDS = frozenset(
    {"series_id", "year", "period", "periodName", "value", "footnotes"}
)
ALLOWED_SOURCE_FIELDS = REQUIRED_SOURCE_FIELDS | {"latest"}
MONTH_NAMES = {
    f"M{month:02d}": name
    for month, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}


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
    request_windows: int
    validation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "fetched_records": self.fetched_records,
            "raw_snapshot_created": self.raw_snapshot_created,
            "normalized_records": self.normalized_records,
            "latest_period": self.latest_period,
            "changed_partitions": self.changed_partitions,
            "request_windows": self.request_windows,
            "validation": self.validation,
        }


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"BLS CPI value is not numeric: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise DatasetValidationError(f"BLS CPI value must be a non-negative finite decimal: {value!r}")
    return parsed


def _footnotes(record: Mapping[str, object]) -> tuple[str, str]:
    value = record["footnotes"]
    if not isinstance(value, list):
        raise DatasetValidationError("BLS footnotes must be a list")
    codes: list[str] = []
    texts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise DatasetValidationError(f"invalid BLS footnote: {item!r}")
        code = item.get("code")
        text = item.get("text")
        if code not in (None, ""):
            codes.append(str(code))
        if text not in (None, ""):
            texts.append(str(text))
    return "|".join(codes), " | ".join(texts)


def source_period(record: Mapping[str, object]) -> str:
    year = str(record["year"])
    period = str(record["period"])
    if period not in MONTH_NAMES:
        raise DatasetValidationError(f"BLS CPI period must be a regular month M01-M12: {period!r}")
    try:
        numeric_year = int(year)
    except ValueError as exc:
        raise DatasetValidationError(f"invalid BLS year: {year!r}") from exc
    if numeric_year < EARLIEST_YEAR:
        raise DatasetValidationError(f"BLS CPI year precedes known series coverage: {year}")
    return f"{numeric_year:04d}-{int(period[1:]):02d}"


def validate_source_record(record: Mapping[str, object]) -> None:
    fields = frozenset(record)
    missing = REQUIRED_SOURCE_FIELDS - fields
    unexpected = fields - ALLOWED_SOURCE_FIELDS
    if missing or unexpected:
        raise DatasetValidationError(
            f"source schema mismatch; missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    if record["series_id"] != CPI_U_ALL_ITEMS_SERIES:
        raise DatasetValidationError(f"unexpected BLS series id: {record['series_id']!r}")
    period = str(record["period"])
    expected_name = MONTH_NAMES.get(period)
    if expected_name is None:
        raise DatasetValidationError(f"unexpected BLS CPI period: {period!r}")
    if record["periodName"] != expected_name:
        raise DatasetValidationError(
            f"BLS period name mismatch for {period}: {record['periodName']!r}"
        )
    source_period(record)
    latest = record.get("latest")
    if latest not in (None, "true"):
        raise DatasetValidationError(f"unexpected BLS latest marker: {latest!r}")
    codes, texts = _footnotes(record)
    raw_value = record["value"]
    if not isinstance(raw_value, str):
        raise DatasetValidationError("BLS CPI value must be a string")
    if raw_value == "-":
        if not codes and not texts:
            raise DatasetValidationError("unavailable BLS CPI value must carry a source footnote")
    else:
        _decimal(raw_value)


def normalize_source_record(record: Mapping[str, object]) -> dict[str, str]:
    validate_source_record(record)
    codes, texts = _footnotes(record)
    raw_value = str(record["value"])
    if raw_value == "-":
        index_value = "null"
        status = "unavailable"
    else:
        index_value = format(_decimal(raw_value), "f")
        status = "available"
    return {
        "period": source_period(record),
        "index_value": index_value,
        "observation_status": status,
        "footnote_codes": codes,
        "footnote_text": texts,
        "source_latest": "true" if record.get("latest") == "true" else "false",
        "source_record_sha256": storage_core.source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, object]]:
    by_period: dict[str, dict[str, object]] = {}
    for _metadata, records in storage_core.iter_raw_snapshots(root, STORAGE):
        event_periods: set[str] = set()
        for record in records:
            validate_source_record(record)
            period = source_period(record)
            if period in event_periods:
                raise DatasetValidationError(f"duplicate BLS observation in one retrieval: {period}")
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

    previous: str | None = None
    by_period: dict[str, dict[str, str]] = {}
    unavailable = 0
    for row in normalized:
        if tuple(row) != NORMALIZED_COLUMNS:
            raise DatasetValidationError(f"unexpected normalized columns: {tuple(row)!r}")
        period = row["period"]
        try:
            parsed_period = date.fromisoformat(period + "-01")
        except ValueError as exc:
            raise DatasetValidationError(f"invalid normalized monthly period: {period!r}") from exc
        if parsed_period.year < EARLIEST_YEAR:
            raise DatasetValidationError(f"normalized period precedes coverage: {period}")
        if period in by_period:
            raise DatasetValidationError(f"duplicate normalized period: {period}")
        if previous is not None and period <= previous:
            raise DatasetValidationError("normalized BLS records are not strictly ordered")
        previous = period

        if row["observation_status"] == "available":
            if row["index_value"] == "null":
                raise DatasetValidationError(f"available BLS observation is null: {period}")
            _decimal(row["index_value"])
        elif row["observation_status"] == "unavailable":
            unavailable += 1
            if row["index_value"] != "null":
                raise DatasetValidationError(f"unavailable BLS observation has a numeric value: {period}")
            if not row["footnote_codes"] and not row["footnote_text"]:
                raise DatasetValidationError(f"unavailable BLS observation lost its footnote: {period}")
        else:
            raise DatasetValidationError(
                f"unexpected BLS observation status: {row['observation_status']!r}"
            )
        if row["source_latest"] not in {"true", "false"}:
            raise DatasetValidationError("invalid BLS source_latest value")
        if len(row["source_record_sha256"]) != 64:
            raise DatasetValidationError("invalid BLS source record hash")
        by_period[period] = row

    for source in source_records:
        expected = normalize_source_record(source)
        if by_period.get(expected["period"]) != expected:
            raise DatasetValidationError(
                f"normalized BLS record does not match source: {expected['period']}"
            )

    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
        "unavailable_records": unavailable,
        "unit": "index_1982_84_100",
        "source_series": CPI_U_ALL_ITEMS_SERIES,
        "status": "PASS",
    }


def latest_period(root: Path) -> date | None:
    rows = storage_core.read_normalized_rows(root, STORAGE)
    if not rows:
        return None
    return date.fromisoformat(rows[-1]["period"] + "-01")


def _year_windows(start_year: int, end_year: int) -> list[tuple[int, int]]:
    if start_year > end_year:
        return []
    windows: list[tuple[int, int]] = []
    current = start_year
    while current <= end_year:
        window_end = min(current + 9, end_year)
        windows.append((current, window_end))
        current = window_end + 1
    return windows


def sync(
    root: Path,
    *,
    full: bool = False,
    overlap_years: int = 2,
    adapter: BLSPublicDataAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    if overlap_years < 0:
        raise ValueError("overlap_years cannot be negative")
    adapter = adapter or BLSPublicDataAdapter()
    current_year = datetime.now(timezone.utc).year
    latest = latest_period(root)

    if full or latest is None:
        start_year = EARLIEST_YEAR
    else:
        start_year = max(EARLIEST_YEAR, latest.year - overlap_years)
    windows = _year_windows(start_year, current_year)

    records: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []
    for start, end in windows:
        result: FetchResult = adapter.fetch_series(
            CPI_U_ALL_ITEMS_SERIES,
            start_year=start,
            end_year=end,
        )
        requests.append(result.request)
        records.extend(result.records)

    seen: set[str] = set()
    for record in records:
        validate_source_record(record)
        period = source_period(record)
        if period in seen:
            raise DatasetValidationError(f"duplicate BLS period across request windows: {period}")
        seen.add(period)

    _snapshot, raw_created = storage_core.store_raw_snapshot(
        root,
        STORAGE,
        records,
        request={
            "access": "BLS Public Data API",
            "series_id": CPI_U_ALL_ITEMS_SERIES,
            "registered": adapter.registered,
            "windows": requests,
        },
        pages=len(windows),
        source_total_count=len(records),
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
        fetched_records=len(records),
        raw_snapshot_created=raw_created,
        normalized_records=len(rows),
        latest_period=report["latest_period"],
        changed_partitions=len(changed_years),
        request_windows=len(windows),
        validation="PASS",
    )
