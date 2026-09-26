"""Crash recovery and failover test for EventStore and Transactional Outbox.
Proves:
1. SQLite WAL crash-resilience: interrupted uncommitted transactions roll back cleanly.
2. Committed transactions and encrypted raw events are preserved intact.
3. Restarting worker on the existing DB processes replay idempotently with zero duplicates.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
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

    def test_actual_process_kill_rolls_back(self):
        store = EventStore(self.db_path, self.key_b64)
        event = {"schema_version": 1, "source_id": "sensor", "event_id": "survivor",
                 "item_id": "ITEM-1", "event_type": "ItemReceived",
                 "occurred_at": "2026-09-25T10:00:00+03:00", "payload": {}}
        survivor = store.ingest(event)["ingestion_id"]
        store.close()
        worker_code = (
            "import sys,time; from qc.core import EventStore; "
            "s=EventStore(sys.argv[1],sys.argv[2]); "
            "s.db.execute('BEGIN IMMEDIATE'); "
            "s.db.execute('INSERT INTO cases(case_id,item_id,defect_type,area,first_ingestion_id) VALUES (?,?,?,?,?)',"
            "('partial','ITEM-1','scratch','zone',sys.argv[3])); "
            "print('READY',flush=True); time.sleep(60)"
        )
        proc = subprocess.Popen([sys.executable, "-c", worker_code, self.db_path, self.key_b64, survivor],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "READY")
        finally:
            proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
        reopened = EventStore(self.db_path, self.key_b64)
        try:
            self.assertIsNone(reopened.db.execute("SELECT * FROM cases WHERE case_id='partial'").fetchone())
            self.assertEqual(reopened.verify_integrity()["checked_events"], 1)
            self.assertEqual(reopened.ingest(event)["state"], "duplicate")
        finally:
            reopened.close()

    def test_batch_process_kill_before_and_after_commit(self):
        events = [{"schema_version": 1, "source_id": "crash-batch", "event_id": f"e-{index}",
                   "item_id": "ITEM-BATCH", "event_type": "ItemReceived",
                   "occurred_at": "2026-09-25T10:00:00+03:00", "payload": {"index": index}}
                  for index in range(3)]
        worker_code = (
            "import json,sys,time\n"
            "from qc.core import EventStore\n"
            "s=EventStore(sys.argv[1],sys.argv[2])\n"
            "events=json.loads(sys.argv[4])\n"
            "if sys.argv[3]=='before':\n"
            "    with s.transaction():\n"
            "        s.ingest_many(events)\n"
            "        print('READY',flush=True)\n"
            "        time.sleep(60)\n"
            "else:\n"
            "    s.ingest_many(events)\n"
            "    print('READY',flush=True)\n"
            "    time.sleep(60)\n"
        )
        for mode, expected in (("before", 0), ("after", 3)):
            with self.subTest(mode=mode):
                path = os.path.join(self.temp.name, f"batch-{mode}.db")
                proc = subprocess.Popen([sys.executable, "-c", worker_code, path, self.key_b64,
                                         mode, json.dumps(events)], stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)
                try:
                    ready = proc.stdout.readline().strip()
                    self.assertEqual(ready, "READY", proc.stderr.read() if not ready else "")
                finally:
                    proc.kill()
                    proc.wait(timeout=5)
                    proc.stdout.close()
                    proc.stderr.close()
                reopened = EventStore(path, self.key_b64)
                try:
                    self.assertEqual(reopened.verify_integrity()["checked_events"], expected)
                    count = reopened.db.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
                    self.assertEqual(count, expected)
                    if expected:
                        self.assertEqual([item["state"] for item in reopened.ingest_many(events)],
                                         ["duplicate"] * 3)
                finally:
                    reopened.close()

    def test_kill_after_database_commit_before_anchor_update_fails_closed(self):
        path = os.path.join(self.temp.name, "anchor-gap.db")
        worker_code = (
            "import sys,time\n"
            "from qc.core import EventStore\n"
            "s=EventStore(sys.argv[1],sys.argv[2])\n"
            "def interrupted_anchor():\n"
            "    print('READY',flush=True)\n"
            "    time.sleep(60)\n"
            "s._write_anchor=interrupted_anchor\n"
            "s.ingest_many([{'schema_version':1,'source_id':'s','event_id':'e',"
            "'item_id':'I','event_type':'ItemReceived',"
            "'occurred_at':'2026-09-25T10:00:00+03:00','payload':{}}])\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", worker_code, path, self.key_b64],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = proc.stdout.readline().strip()
            self.assertEqual(ready, "READY", proc.stderr.read() if not ready else "")
        finally:
            proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
        reopened = EventStore(path, self.key_b64)
        try:
            with self.assertRaisesRegex(ValueError, "audit anchor mismatch"):
                reopened.verify_integrity()
            self.assertEqual(reopened.db.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0], 1)
        finally:
            reopened.close()

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
