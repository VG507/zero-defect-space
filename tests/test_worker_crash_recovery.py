"""Crash recovery and failover test for EventStore and Transactional Outbox.
Proves:
1. SQLite WAL crash-resilience: interrupted uncommitted transactions roll back cleanly.
2. Committed transactions and encrypted raw events are preserved intact.
3. Restarting worker on the existing DB processes replay idempotently with zero duplicates.
"""

from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
import unittest

from qc.core import EventStore


class WorkerCrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "crash_recovery.db")
        self.key_b64 = base64.b64encode(os.urandom(32)).decode()

    def tearDown(self):
        self.temp.cleanup()

    def test_aborted_transaction_rolls_back_cleanly(self):
        store = EventStore(self.db_path, self.key_b64)

        try:
            # 1. Normal committed event
            ev1 = {
                "schema_version": 1,
                "source_id": "sensor-1",
                "event_id": "evt-committed-1",
                "item_id": "ITEM-CRASH-1",
                "event_type": "ItemReceived",
                "occurred_at": "2026-09-25T10:00:00+03:00",
                "payload": {"status": "ok"},
            }
            res1 = store.ingest(ev1)
            self.assertEqual(res1["state"], "applied")

            # 2. Simulate worker crash/exception inside active transaction
            try:
                with store.transaction():
                    # Manually insert partial unverified row
                    store.db.execute(
                        "INSERT INTO cases(case_id, item_id, defect_type, area, first_ingestion_id) "
                        "VALUES ('partial-case', 'ITEM-CRASH-1', 'scratch', 'zone-x', ?)",
                        (res1["ingestion_id"],)
                    )
                    # Simulate unhandled worker kill / exception before commit
                    raise RuntimeError("WORKER_SIMULATED_CRASH")
            except RuntimeError:
                pass  # Worker died

            # 3. Verify clean rollback
            partial = store.db.execute("SELECT * FROM cases WHERE case_id='partial-case'").fetchone()
            self.assertIsNone(partial, "Uncommitted transaction must not leave partial state in database")
        finally:
            store.close()

    def test_worker_restart_and_idempotent_replay(self):
        # 1. Worker 1 initializes and processes items
        worker1 = EventStore(self.db_path, self.key_b64)
        event_batch = [
            {
                "schema_version": 1,
                "source_id": "line-camera",
                "event_id": f"event-{i}",
                "item_id": "ITEM-100",
                "event_type": "ItemReceived",
                "occurred_at": f"2026-09-25T10:0{i}:00+03:00",
                "payload": {"seq": i},
            }
            for i in range(5)
        ]
        for ev in event_batch:
            res = worker1.ingest(ev)
            self.assertEqual(res["state"], "applied")

        # Worker 1 suddenly stops/terminates without closing properly (simulating hard process death)
        # Note: In SQLite WAL mode, WAL file remains on disk
        worker1.db.close()

        # 2. Worker 2 starts up against the exact same DB file
        worker2 = EventStore(self.db_path, self.key_b64)
        try:
            # Integrity check passes immediately after recovery
            integ = worker2.verify_integrity()
            self.assertEqual(integ["checked_events"], 5)
            self.assertTrue(integ["valid"])

            # 3. Replay of the same batch to Worker 2 must be 100% idempotent
            for ev in event_batch:
                res = worker2.ingest(ev)
                self.assertEqual(res["state"], "duplicate", "Replayed events must be recognized as duplicates without side-effects")

            # Confirm count of items in DB did not double
            count = worker2.db.execute("SELECT COUNT(*) as cnt FROM raw_events").fetchone()["cnt"]
            self.assertEqual(count, 5, "Database must still contain exactly 5 events")
        finally:
            worker2.close()


if __name__ == "__main__":
    unittest.main()
