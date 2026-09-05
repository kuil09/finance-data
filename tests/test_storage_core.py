from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finance_data.storage_core import (
    StorageSpec,
    iter_raw_snapshots,
    read_normalized_rows,
    store_raw_snapshot,
    write_normalized_rows,
)


SPEC = StorageSpec(
    source_id="synthetic_source",
    source_dataset_id="sample_table",
    dataset_id="test.sample.dataset",
    normalized_columns=("period", "value", "source_record_sha256"),
    raw_sort_fields=("date", "code"),
)


class GenericStorageTests(unittest.TestCase):
    def test_native_raw_values_are_preserved_and_sorted(self):
        records = [
            {"date": "2024-01-02", "code": "b", "value": None},
            {"date": "2024-01-01", "code": "a", "value": 1.25},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, created = store_raw_snapshot(
                root,
                SPEC,
                records,
                request={"kind": "test"},
                pages=1,
                source_total_count=2,
                retrieved_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            )
            self.assertTrue(created)
            snapshots = list(iter_raw_snapshots(root, SPEC))
            self.assertEqual(len(snapshots), 1)
            _, restored = snapshots[0]
            self.assertEqual(restored[0]["date"], "2024-01-01")
            self.assertEqual(restored[0]["value"], 1.25)
            self.assertIsNone(restored[1]["value"])

    def test_unchanged_payload_is_idempotent(self):
        records = [{"date": "2024-01-01", "code": "a", "value": 1}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = store_raw_snapshot(
                root,
                SPEC,
                records,
                request={},
                pages=1,
                source_total_count=1,
                retrieved_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
            second = store_raw_snapshot(
                root,
                SPEC,
                records,
                request={},
                pages=1,
                source_total_count=1,
                retrieved_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            )
            self.assertTrue(first[1])
            self.assertFalse(second[1])

    def test_normalized_storage_is_dataset_parameterized(self):
        rows = [
            {"period": "2023-12-31", "value": "1", "source_record_sha256": "a" * 64},
            {"period": "2024-01-01", "value": "2", "source_record_sha256": "b" * 64},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changed = write_normalized_rows(root, SPEC, rows)
            self.assertEqual(len(changed), 2)
            self.assertEqual(read_normalized_rows(root, SPEC), rows)
            self.assertTrue(
                (root / "data/normalized/test.sample.dataset/year=2024/data.csv").exists()
            )


if __name__ == "__main__":
    unittest.main()
