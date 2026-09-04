from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

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


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def source_record_bytes(record: Mapping[str, str]) -> bytes:
    return canonical_json_bytes(dict(record))


def source_record_hash(record: Mapping[str, str]) -> str:
    return hashlib.sha256(source_record_bytes(record)).hexdigest()


def snapshot_payload(records: Sequence[Mapping[str, str]]) -> bytes:
    ordered = sorted(records, key=lambda row: (row["record_date"], row.get("src_line_nbr", "")))
    return b"".join(source_record_bytes(row) for row in ordered)


def snapshot_hash(records: Sequence[Mapping[str, str]]) -> str:
    return hashlib.sha256(snapshot_payload(records)).hexdigest()


def raw_root(root: Path) -> Path:
    return root / "data" / "raw" / RAW_SOURCE_ID / RAW_TABLE_ID


def receipt_root(root: Path) -> Path:
    return raw_root(root) / "_receipts"


def normalized_root(root: Path) -> Path:
    return root / "data" / "normalized" / DATASET_ID


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_filename(retrieved_at: datetime, digest: str) -> str:
    utc = retrieved_at.astimezone(timezone.utc)
    stamp = utc.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}--sha256-{digest}.json"


def _raw_events(root: Path) -> list[dict[str, object]]:
    """Return chronological retrieval events for preserved raw snapshots.

    Snapshot metadata from the first storage format is treated as a retrieval event.
    Newer receipt files record later observations of content-addressed snapshots,
    including a source reversion to bytes that were already preserved earlier.
    """

    base = raw_root(root)
    if not base.exists():
        return []

    events: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for path in base.glob("sha256-*/metadata.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        key = (str(metadata["retrieved_at"]), str(metadata["content_sha256"]))
        if key not in seen:
            events.append(metadata)
            seen.add(key)

    receipts = receipt_root(root)
    if receipts.exists():
        for path in receipts.glob("*.json"):
            metadata = json.loads(path.read_text(encoding="utf-8"))
            key = (str(metadata["retrieved_at"]), str(metadata["content_sha256"]))
            if key not in seen:
                events.append(metadata)
                seen.add(key)

    return sorted(
        events,
        key=lambda item: (str(item["retrieved_at"]), str(item["content_sha256"])),
    )


def _latest_effective_digest(root: Path) -> str | None:
    events = _raw_events(root)
    if not events:
        return None
    return str(events[-1]["content_sha256"])


def store_raw_snapshot(
    root: Path,
    records: Sequence[Mapping[str, str]],
    *,
    request: Mapping[str, object],
    pages: int,
    source_total_count: int | None,
    retrieved_at: datetime | None = None,
) -> tuple[Path, bool]:
    if retrieved_at is None:
        retrieved_at = datetime.now(timezone.utc)

    digest = snapshot_hash(records)
    previous_digest = _latest_effective_digest(root)
    destination = raw_root(root) / f"sha256-{digest}"
    snapshot_created = False

    if not destination.exists():
        destination.mkdir(parents=True, exist_ok=False)
        payload = snapshot_payload(records)
        (destination / "records.jsonl").write_bytes(payload)
        (destination / "request.json").write_text(
            json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        metadata = {
            "content_sha256": digest,
            "pages": pages,
            "record_count": len(records),
            "retrieved_at": _utc_timestamp(retrieved_at),
            "source": RAW_SOURCE_ID,
            "source_dataset": RAW_TABLE_ID,
            "source_total_count": source_total_count,
        }
        (destination / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        snapshot_created = True

    receipt_created = False
    if digest != previous_digest:
        receipts = receipt_root(root)
        receipts.mkdir(parents=True, exist_ok=True)
        receipt_path = receipts / _receipt_filename(retrieved_at, digest)
        receipt = {
            "content_sha256": digest,
            "pages": pages,
            "record_count": len(records),
            "request": dict(request),
            "retrieved_at": _utc_timestamp(retrieved_at),
            "source": RAW_SOURCE_ID,
            "source_dataset": RAW_TABLE_ID,
            "source_total_count": source_total_count,
        }
        serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if receipt_path.exists():
            if receipt_path.read_text(encoding="utf-8") != serialized:
                raise ValueError(f"raw receipt collision: {receipt_path}")
        else:
            receipt_path.write_text(serialized, encoding="utf-8")
            receipt_created = True

    return destination, snapshot_created or receipt_created


def iter_raw_snapshots(root: Path) -> Iterable[tuple[dict[str, object], list[dict[str, str]]]]:
    base = raw_root(root)
    for event in _raw_events(root):
        digest = str(event["content_sha256"])
        directory = base / f"sha256-{digest}"
        if not directory.exists():
            raise ValueError(f"raw receipt references missing snapshot: {digest}")

        records: list[dict[str, str]] = []
        with (directory / "records.jsonl").open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"invalid raw record at {directory}:{line_number}")
                records.append({str(k): str(v) for k, v in value.items()})
        if snapshot_hash(records) != digest:
            raise ValueError(f"raw snapshot checksum mismatch: {directory}")
        yield event, records


def read_normalized_rows(root: Path) -> list[dict[str, str]]:
    base = normalized_root(root)
    rows: list[dict[str, str]] = []
    if not base.exists():
        return rows
    for path in sorted(base.glob("year=*/data.csv")):
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != NORMALIZED_COLUMNS:
                raise ValueError(f"unexpected normalized CSV header: {path}")
            rows.extend(dict(row) for row in reader)
    return rows


def write_normalized_rows(root: Path, rows: Sequence[Mapping[str, str]]) -> list[Path]:
    base = normalized_root(root)
    base.mkdir(parents=True, exist_ok=True)
    by_year: dict[str, list[Mapping[str, str]]] = {}
    for row in sorted(rows, key=lambda item: item["period"]):
        by_year.setdefault(row["period"][:4], []).append(row)

    expected_paths: set[Path] = set()
    changed: list[Path] = []
    for year, year_rows in sorted(by_year.items()):
        path = base / f"year={year}" / "data.csv"
        expected_paths.add(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        from io import StringIO

        buffer = StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=NORMALIZED_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(year_rows)
        new_text = buffer.getvalue()
        old_text = path.read_text(encoding="utf-8") if path.exists() else None
        if old_text != new_text:
            path.write_text(new_text, encoding="utf-8", newline="")
            changed.append(path)

    for path in list(base.glob("year=*/data.csv")):
        if path not in expected_paths:
            path.unlink()
            changed.append(path)
            try:
                path.parent.rmdir()
            except OSError:
                pass
    return changed
