from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from ..storage import (
    DATASET_ID,
    iter_raw_snapshots,
    read_normalized_rows,
    source_record_hash,
    store_raw_snapshot,
    write_normalized_rows,
)
from ..treasury import DEBT_TO_PENNY_FIELDS, FetchResult, TreasuryFiscalDataAdapter

EXPECTED_SOURCE_FIELDS = frozenset(DEBT_TO_PENNY_FIELDS)
COMPONENT_COVERAGE_START = date(2005, 3, 31)
NULL_TEXT = "null"
KNOWN_INVARIANT_EXCEPTIONS = {
    "2011-02-01": (
        "9482575172379.45",
        "4627267706524.08",
        "14109842878903.50",
    ),
    "2025-08-04": (
        "29523300148538.85",
        "7314864649375.46",
        "36828164797914.31",
    ),
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


def _is_null(value: str | None) -> bool:
    return value in (None, "", "null", "None")


def _decimal(value: str, field: str) -> Decimal:
    if _is_null(value):
        raise DatasetValidationError(f"{field} is null")
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"{field} is not a decimal: {value!r}") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise DatasetValidationError(f"{field} must be a non-negative finite decimal")
    return decimal_value


def _optional_decimal(value: str, field: str) -> Decimal | None:
    if _is_null(value):
        return None
    return _decimal(value, field)


def _normalized_decimal(value: str, field: str, *, nullable: bool = False) -> str:
    if nullable and _is_null(value):
        return NULL_TEXT
    return format(_decimal(value, field), "f")


def _known_invariant_exception(
    period: str, held: Decimal, intragov: Decimal, total: Decimal
) -> bool:
    expected = KNOWN_INVARIANT_EXCEPTIONS.get(period)
    if expected is None:
        return False
    return (held, intragov, total) == tuple(Decimal(value) for value in expected)


def validate_source_record(record: Mapping[str, str]) -> None:
    fields = frozenset(record)
    if fields != EXPECTED_SOURCE_FIELDS:
        missing = sorted(EXPECTED_SOURCE_FIELDS - fields)
        unexpected = sorted(fields - EXPECTED_SOURCE_FIELDS)
        raise DatasetValidationError(
            f"source schema mismatch; missing={missing}, unexpected={unexpected}"
        )
    try:
        date.fromisoformat(record["record_date"])
    except ValueError as exc:
        raise DatasetValidationError(f"invalid record_date: {record['record_date']!r}") from exc
    try:
        source_line = int(record["src_line_nbr"])
    except ValueError as exc:
        raise DatasetValidationError("src_line_nbr must be an integer") from exc
    if source_line < 0:
        raise DatasetValidationError("src_line_nbr must be non-negative")

    period = date.fromisoformat(record["record_date"])
    held = _optional_decimal(record["debt_held_public_amt"], "debt_held_public_amt")
    intragov = _optional_decimal(record["intragov_hold_amt"], "intragov_hold_amt")
    total = _decimal(record["tot_pub_debt_out_amt"], "tot_pub_debt_out_amt")

    if (held is None) != (intragov is None):
        raise DatasetValidationError(
            f"component nullability mismatch for {record['record_date']}: "
            "public and intragovernmental amounts must be present or null together"
        )
    if period >= COMPONENT_COVERAGE_START and held is None:
        raise DatasetValidationError(
            f"unexpected missing debt components on or after {COMPONENT_COVERAGE_START.isoformat()}: "
            f"{record['record_date']}"
        )
    if held is not None and total != held + intragov:
        if not _known_invariant_exception(record["record_date"], held, intragov, total):
            raise DatasetValidationError(
                f"source invariant failed for {record['record_date']}: total != public + intragov"
            )


def normalize_source_record(record: Mapping[str, str]) -> dict[str, str]:
    validate_source_record(record)
    return {
        "period": record["record_date"],
        "debt_held_by_public": _normalized_decimal(
            record["debt_held_public_amt"], "debt_held_public_amt", nullable=True
        ),
        "intragovernmental_holdings": _normalized_decimal(
            record["intragov_hold_amt"], "intragov_hold_amt", nullable=True
        ),
        "total_public_debt_outstanding": _normalized_decimal(
            record["tot_pub_debt_out_amt"], "tot_pub_debt_out_amt"
        ),
        "source_line_number": str(int(record["src_line_nbr"])),
        "source_record_sha256": source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, str]]:
    """Resolve all preserved snapshots, with the latest retrieval winning by record_date.

    Corrections are not discarded: earlier source values remain in immutable raw snapshots,
    while the latest retrieved source record is the canonical normalized representation.
    """

    by_date: dict[str, dict[str, str]] = {}
    seen_snapshot = False
    for _metadata, records in iter_raw_snapshots(root):
        seen_snapshot = True
        for record in records:
            validate_source_record(record)
            by_date[record["record_date"]] = record
    if not seen_snapshot:
        return []
    return [by_date[key] for key in sorted(by_date)]


def rebuild(root: Path) -> list[dict[str, str]]:
    source_records = canonical_source_records(root)
    rows = [normalize_source_record(record) for record in source_records]
    write_normalized_rows(root, rows)
    validate(root)
    return rows


def validate(root: Path) -> dict[str, object]:
    source_records = canonical_source_records(root)
    normalized = read_normalized_rows(root)

    if len(source_records) != len(normalized):
        raise DatasetValidationError(
            f"record count mismatch: source={len(source_records)} normalized={len(normalized)}"
        )

    normalized_by_period: dict[str, dict[str, str]] = {}
    previous: str | None = None
    for row in normalized:
        period = row["period"]
        try:
            date.fromisoformat(period)
        except ValueError as exc:
            raise DatasetValidationError(f"invalid normalized period: {period!r}") from exc
        if period in normalized_by_period:
            raise DatasetValidationError(f"duplicate normalized period: {period}")
        if previous is not None and period <= previous:
            raise DatasetValidationError("normalized records are not strictly ordered")
        previous = period
        normalized_by_period[period] = row

        held = _optional_decimal(row["debt_held_by_public"], "debt_held_by_public")
        intragov = _optional_decimal(
            row["intragovernmental_holdings"], "intragovernmental_holdings"
        )
        total = _decimal(row["total_public_debt_outstanding"], "total_public_debt_outstanding")
        if (held is None) != (intragov is None):
            raise DatasetValidationError(f"normalized component nullability mismatch for {period}")
        if date.fromisoformat(period) >= COMPONENT_COVERAGE_START and held is None:
            raise DatasetValidationError(
                f"unexpected normalized missing debt components on or after "
                f"{COMPONENT_COVERAGE_START.isoformat()}: {period}"
            )
        if held is not None and total != held + intragov:
            if not _known_invariant_exception(period, held, intragov, total):
                raise DatasetValidationError(f"normalized invariant failed for {period}")

    for source in source_records:
        period = source["record_date"]
        row = normalized_by_period.get(period)
        if row is None:
            raise DatasetValidationError(f"source record dropped during normalization: {period}")
        expected = normalize_source_record(source)
        if row != expected:
            raise DatasetValidationError(f"normalized record does not match source: {period}")

    component_null_records = sum(
        1 for row in normalized if _is_null(row["debt_held_by_public"])
    )
    known_anomalies_present = [
        period
        for period, row in normalized_by_period.items()
        if period in KNOWN_INVARIANT_EXCEPTIONS
        and not _is_null(row["debt_held_by_public"])
        and _known_invariant_exception(
            period,
            _decimal(row["debt_held_by_public"], "debt_held_by_public"),
            _decimal(row["intragovernmental_holdings"], "intragovernmental_holdings"),
            _decimal(row["total_public_debt_outstanding"], "total_public_debt_outstanding"),
        )
    ]
    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
        "component_null_records": component_null_records,
        "component_coverage_start": COMPONENT_COVERAGE_START.isoformat(),
        "known_source_invariant_exceptions": known_anomalies_present,
        "status": "PASS",
    }


def latest_period(root: Path) -> date | None:
    rows = read_normalized_rows(root)
    if not rows:
        return None
    return date.fromisoformat(rows[-1]["period"])


def sync(
    root: Path,
    *,
    full: bool = False,
    overlap_days: int = 10,
    adapter: TreasuryFiscalDataAdapter | None = None,
    retrieved_at: datetime | None = None,
) -> SyncSummary:
    if overlap_days < 0:
        raise ValueError("overlap_days cannot be negative")
    adapter = adapter or TreasuryFiscalDataAdapter()

    start_date: date | None = None
    if not full:
        latest = latest_period(root)
        if latest is not None:
            start_date = latest - timedelta(days=overlap_days)

    result: FetchResult = adapter.fetch_debt_to_penny(start_date=start_date)
    for record in result.records:
        validate_source_record(record)

    _snapshot, raw_created = store_raw_snapshot(
        root,
        result.records,
        request=result.request,
        pages=result.pages,
        source_total_count=result.source_total_count,
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
    )

    before = {
        row["period"]: row for row in read_normalized_rows(root)
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
        raw_snapshot_created=raw_created,
        normalized_records=len(rows),
        latest_period=report["latest_period"],
        changed_partitions=len(changed_years),
        validation="PASS",
    )
