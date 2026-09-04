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


def _decimal(value: str, field: str) -> Decimal:
    if value in ("", "null", "None"):
        raise DatasetValidationError(f"{field} is null")
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(f"{field} is not a decimal: {value!r}") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise DatasetValidationError(f"{field} must be a non-negative finite decimal")
    return decimal_value


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

    held = _decimal(record["debt_held_public_amt"], "debt_held_public_amt")
    intragov = _decimal(record["intragov_hold_amt"], "intragov_hold_amt")
    total = _decimal(record["tot_pub_debt_out_amt"], "tot_pub_debt_out_amt")
    if total != held + intragov:
        raise DatasetValidationError(
            f"source invariant failed for {record['record_date']}: total != public + intragov"
        )


def normalize_source_record(record: Mapping[str, str]) -> dict[str, str]:
    validate_source_record(record)
    return {
        "period": record["record_date"],
        "debt_held_by_public": format(Decimal(record["debt_held_public_amt"]), "f"),
        "intragovernmental_holdings": format(Decimal(record["intragov_hold_amt"]), "f"),
        "total_public_debt_outstanding": format(Decimal(record["tot_pub_debt_out_amt"]), "f"),
        "source_line_number": str(int(record["src_line_nbr"])),
        "source_record_sha256": source_record_hash(record),
    }


def canonical_source_records(root: Path) -> list[dict[str, str]]:
    """Resolve preserved snapshots, with the latest retrieval winning by record_date.

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

        held = _decimal(row["debt_held_by_public"], "debt_held_by_public")
        intragov = _decimal(row["intragovernmental_holdings"], "intragovernmental_holdings")
        total = _decimal(row["total_public_debt_outstanding"], "total_public_debt_outstanding")
        if total != held + intragov:
            raise DatasetValidationError(f"normalized invariant failed for {period}")

    for source in source_records:
        period = source["record_date"]
        row = normalized_by_period.get(period)
        if row is None:
            raise DatasetValidationError(f"source record dropped during normalization: {period}")
        expected = normalize_source_record(source)
        if row != expected:
            raise DatasetValidationError(f"normalized record does not match source: {period}")

    return {
        "dataset": DATASET_ID,
        "records": len(normalized),
        "first_period": normalized[0]["period"] if normalized else None,
        "latest_period": normalized[-1]["period"] if normalized else None,
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

    before = {row["period"]: row for row in read_normalized_rows(root)}
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
