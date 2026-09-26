import base64
import os
import tempfile
import unittest

from qc.core import EventStore
from qc.crypto import DEFAULT_PROFILE, HYBRID_PQ_PROFILE, KeyRing


class CryptoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.k1 = base64.b64encode(os.urandom(32)).decode()
        self.k2 = base64.b64encode(os.urandom(32)).decode()
        self.key_ring = KeyRing.from_single_key(self.k1, key_id="k1")
        self.db_path = os.path.join(self.temp.name, "crypto_test.db")
        self.store = EventStore(self.db_path, self.key_ring)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_key_rotation_and_historical_decryption(self):
        # 1. Ingest event using key k1 (standard AES-256-GCM)
        ev1 = {
            "schema_version": 1,
            "source_id": "sensor-1",
            "event_id": "e-1",
            "item_id": "ITEM-A",
            "event_type": "ItemReceived",
            "occurred_at": "2026-09-25T10:00:00+03:00",
            "payload": {"line": "L1"},
        }
        res1 = self.store.ingest(ev1)
        self.assertEqual(res1["state"], "applied")

        # 2. Rotate keys in KeyRing: add k2 and make it primary
        self.key_ring.add_key("k2", self.k2, make_primary=True)

        # 3. Ingest event using new key k2
        ev2 = {
            "schema_version": 1,
            "source_id": "sensor-1",
            "event_id": "e-2",
            "item_id": "ITEM-A",
            "event_type": "OperationStarted",
            "occurred_at": "2026-09-25T10:05:00+03:00",
            "payload": {"operation_run_id": "run-1", "operation": "weld"},
        }
        res2 = self.store.ingest(ev2)
        self.assertEqual(res2["state"], "applied")

        # 4. Verify integrity can read and decrypt both events despite different keys
        check = self.store.verify_integrity()
        self.assertEqual(check["checked_events"], 2)
        self.assertTrue(check["valid"])

        # Check raw database rows reflect different key IDs
        rows = self.store.db.execute("SELECT event_id, key_id, crypto_profile_id FROM raw_events ORDER BY rowid").fetchall()
        self.assertEqual(rows[0]["key_id"], "k1")
        self.assertEqual(rows[0]["crypto_profile_id"], DEFAULT_PROFILE)
        self.assertEqual(rows[1]["key_id"], "k2")
        self.assertEqual(rows[1]["crypto_profile_id"], DEFAULT_PROFILE)

    def test_post_quantum_hybrid_kem_envelope(self):
        # Ingest event using Post-Quantum Hybrid envelope profile
        ev_pq = {
            "schema_version": 1,
            "source_id": "pq-sensor",
            "event_id": "pq-1",
            "item_id": "ITEM-PQ",
            "event_type": "ItemReceived",
            "occurred_at": "2026-09-25T11:00:00+03:00",
            "payload": {"critical_aerospace_part": True},
        }
        res = self.store.ingest(ev_pq, profile_id=HYBRID_PQ_PROFILE)
        self.assertEqual(res["state"], "applied")

        # Check DB metadata
        row = self.store.db.execute("SELECT * FROM raw_events WHERE event_id='pq-1'").fetchone()
        self.assertEqual(row["crypto_profile_id"], HYBRID_PQ_PROFILE)

        # Decrypt and verify contents
        decoded = self.store._decode(row)
        self.assertEqual(decoded["item_id"], "ITEM-PQ")
        self.assertEqual(decoded["payload"]["critical_aerospace_part"], True)

    def test_missing_key_fails_closed(self):
        # Ingest event under k1
        ev = {
            "schema_version": 1,
            "source_id": "sensor-x",
            "event_id": "ex-1",
            "item_id": "ITEM-X",
            "event_type": "ItemReceived",
            "occurred_at": "2026-09-25T10:00:00+03:00",
            "payload": {},
        }
        self.store.ingest(ev)
        self.store.close()

        # Re-open store with only k2 (k1 omitted)
        restricted_ring = KeyRing.from_single_key(self.k2, key_id="k2")
        store_restricted = EventStore(self.db_path, restricted_ring)
        try:
            with self.assertRaises(KeyError):
                store_restricted.verify_integrity()
        finally:
            store_restricted.close()


if __name__ == "__main__":
    unittest.main()
