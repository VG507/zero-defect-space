"""Atomic batch ingestion and audit-anchor regression tests."""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from unittest.mock import patch

from qc.core import EventStore


def event(event_id: str, **changes) -> dict:
    value = {"schema_version": 1, "source_id": "batch-sensor", "event_id": event_id,
             "item_id": "ITEM-BATCH", "event_type": "ItemReceived",
             "occurred_at": "2026-09-25T10:00:00+03:00", "payload": {"value": event_id}}
    value.update(changes)
    return value


class BatchIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "batch.db")
        self.key = base64.b64encode(os.urandom(32)).decode()
        self.store = EventStore(self.path, self.key)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_batch_preserves_single_event_states_with_one_anchor_update(self):
        first = event("one")
        conflict = event("one", payload={"value": "changed"})
        quarantined = event("two", source_firmware_version=42)
        with (patch.object(self.store, "_write_anchor", wraps=self.store._write_anchor) as update,
              patch.object(self.store, "_check_anchor", wraps=self.store._check_anchor) as verify):
            result = self.store.ingest_many([first, first, conflict, quarantined])
        self.assertEqual([item["state"] for item in result],
                         ["applied", "duplicate", "conflict", "quarantined"])
        self.assertEqual(update.call_count, 1)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(self.store.verify_integrity()["checked_events"], 2)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ingestion_conflicts").fetchone()[0], 1)

    def test_invalid_identity_rolls_back_whole_batch_and_anchor(self):
        before = self.store._read_anchor()
        with self.assertRaisesRegex(ValueError, "source_id is required"):
            self.store.ingest_many([event("one"), event("two", source_id="")])
        self.assertEqual(self.store._read_anchor(), before)
        self.assertEqual(self.store.verify_integrity()["checked_events"], 0)

    def test_batch_tail_deletion_and_metadata_tampering_are_detected(self):
        self.store.ingest_many([event("one"), event("two"), event("three")])
        self.store.db.execute("UPDATE raw_events SET item_id='CHANGED' WHERE event_id='two'")
        with self.assertRaisesRegex(ValueError, "block hash mismatch"):
            self.store.verify_integrity()
        self.store.db.execute("UPDATE raw_events SET item_id='ITEM-BATCH' WHERE event_id='two'")
        self.store.db.execute("DELETE FROM processing_events WHERE ingestion_id IN "
                              "(SELECT ingestion_id FROM raw_events WHERE event_id='three')")
        self.store.db.execute("DELETE FROM raw_events WHERE event_id='three'")
        with self.assertRaisesRegex(ValueError, "audit anchor mismatch"):
            self.store.verify_integrity()

    def test_empty_batch_has_no_transaction_or_anchor_change(self):
        before = self.store._read_anchor()
        self.assertEqual(self.store.ingest_many([]), [])
        self.assertEqual(self.store._read_anchor(), before)


if __name__ == "__main__":
    unittest.main()
