import base64
import os
import tempfile
import threading
import time
import unittest

from qc.adapters.erp_profiles import ErpProfileMapper
from qc.core import EventStore
from qc.edge import EdgeSpoolBuffer
from qc.server import AppServer


class AdvancedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "adv_test.db")
        self.key_b64 = base64.b64encode(os.urandom(32)).decode()
        self.store = EventStore(self.db_path, self.key_b64)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_hash_chain_detects_row_deletion(self):
        # Ingest 3 events to form a cryptographic chain
        for i in range(3):
            ev = {
                "schema_version": 1,
                "source_id": "sensor-chain",
                "event_id": f"chain-evt-{i}",
                "item_id": f"ITEM-CHAIN-{i}",
                "event_type": "ItemReceived",
                "occurred_at": f"2026-09-25T10:0{i}:00+03:00",
                "payload": {"step": i},
            }
            res = self.store.ingest(ev)
            self.assertEqual(res["state"], "applied")

        # Initial chain passes
        integ = self.store.verify_integrity()
        self.assertTrue(integ["valid"])
        self.assertTrue(integ["hash_chain_verified"])
        self.assertEqual(integ["checked_events"], 3)

        # Maliciously delete middle row (event index 1), simulating DB tampering bypassing app layer
        self.store.db.execute("PRAGMA foreign_keys=OFF")
        self.store.db.execute("DELETE FROM processing_events WHERE ingestion_id IN (SELECT ingestion_id FROM raw_events WHERE event_id='chain-evt-1')")
        self.store.db.execute("DELETE FROM raw_events WHERE event_id='chain-evt-1'")
        self.store.db.execute("PRAGMA foreign_keys=ON")

        # Verification must detect the break in the hash chain
        with self.assertRaises(ValueError) as ctx:
            self.store.verify_integrity()
        self.assertTrue("hash chain broken" in str(ctx.exception) or "audit anchor mismatch" in str(ctx.exception))

    def test_anchor_detects_tail_deletion_and_metadata_edit(self):
        event = {"schema_version": 1, "source_id": "s", "event_id": "tail", "item_id": "A",
                 "event_type": "ItemReceived", "occurred_at": "2026-09-25T10:00:00+03:00", "payload": {}}
        self.store.ingest(event)
        self.store.db.execute("UPDATE raw_events SET item_id='B' WHERE event_id='tail'")
        with self.assertRaisesRegex(ValueError, "block hash mismatch"):
            self.store.verify_integrity()
        self.store.db.execute("UPDATE raw_events SET item_id='A' WHERE event_id='tail'")
        self.store.db.execute("DELETE FROM processing_events")
        self.store.db.execute("DELETE FROM raw_events")
        with self.assertRaisesRegex(ValueError, "audit anchor mismatch"):
            self.store.verify_integrity()

    def test_missing_anchor_is_not_silently_recreated(self):
        event = {"schema_version": 1, "source_id": "s", "event_id": "one", "item_id": "A",
                 "event_type": "ItemReceived", "occurred_at": "2026-09-25T10:00:00+03:00", "payload": {}}
        self.store.ingest(event)
        os.unlink(self.db_path + ".audit-anchor")
        with self.assertRaisesRegex(ValueError, "audit anchor missing"):
            self.store.verify_integrity()

    def test_v2_contract_rejects_invalid_new_fields(self):
        base = {"schema_version": 2, "source_id": "s", "event_id": "bad-v2", "item_id": "A",
                "event_type": "ItemReceived", "occurred_at": "2026-09-25T10:00:00+03:00",
                "payload": {}, "source_firmware_version": 42}
        self.assertEqual(self.store.ingest(base)["state"], "quarantined")
        other = {**base, "event_id": "bad-extra", "source_firmware_version": "1.0", "unexpected": True}
        self.assertEqual(self.store.ingest(other)["state"], "quarantined")

    def test_tls_cannot_fall_back_to_http(self):
        tokens = {"source": "s", "viewer": "v", "controller": "c", "admin": "a"}
        with self.assertRaises(ValueError):
            AppServer(("127.0.0.1", 0), self.store, tokens, tls_cert="missing.pem")
        with self.assertRaises(FileNotFoundError):
            AppServer(("127.0.0.1", 0), self.store, tokens, tls_cert="missing.pem", tls_key="missing.key")

    def test_erp_and_kompas_profile_mapping(self):
        # 1. Map 1C Order
        doc_1c = {
            "doc_number": "1C-2026-X1",
            "version": 1,
            "item_code": "PART-TURB-01",
            "route_points": ["ST-1", "ST-2"],
            "checkpoints": [{"checkpoint_id": "CP-1", "station": "ST-1", "type": "IncomingInspectionCompleted", "due": "2026-09-25T12:00:00+03:00"}],
        }
        ev_1c = ErpProfileMapper.from_1c_order(doc_1c, "2026-09-25T10:00:00+03:00")
        res_1c = self.store.ingest(ev_1c)
        self.assertEqual(res_1c["state"], "applied")

        # 2. Map KOMPAS BOM
        bom = {
            "assembly_id": "ASM-TURB-ROOT",
            "assembly_version": "1.0",
            "components": [{"component_id": "C-1", "name": "Blade", "quantity": 12}],
            "geometry_attached": False,
        }
        ev_kompas = ErpProfileMapper.from_kompas_bom(bom, "PART-TURB-01", "2026-09-25T10:01:00+03:00")
        res_kompas = self.store.ingest(ev_kompas)
        self.assertEqual(res_kompas["state"], "applied")

        hist = self.store.history("PART-TURB-01")
        self.assertEqual(len(hist["events"]), 2)

    def test_edge_spool_offline_buffer_and_drain(self):
        spool_file = os.path.join(self.temp.name, "edge_spool.db")
        spool = EdgeSpoolBuffer(spool_file)

        tokens = {"source": "src-tok", "viewer": "v-tok", "controller": "c-tok", "admin": "a-tok"}
        server = AppServer(("127.0.0.1", 0), self.store, tokens)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        target_url = f"http://127.0.0.1:{port}/api/events"

        try:
            # 1. Generate 3 offline events
            for i in range(3):
                spool.push({
                    "schema_version": 1,
                    "source_id": "edge-terminal",
                    "event_id": f"spool-{i}",
                    "item_id": "ITEM-SPOOL",
                    "event_type": "ItemReceived",
                    "occurred_at": f"2026-09-25T10:0{i}:00+03:00",
                    "payload": {"edge_seq": i},
                })
            self.assertEqual(spool.count(), 3)
            spool.close()
            spool = EdgeSpoolBuffer(spool_file)
            self.assertEqual(spool.count(), 3)

            # 2. Flush to online server
            flushed = spool.flush(target_url, token="src-tok")
            self.assertEqual(flushed, 3)
            self.assertEqual(spool.count(), 0, "All events must be drained from spool")

            # 3. Check events in core store
            hist = self.store.history("ITEM-SPOOL")
            self.assertEqual(len(hist["events"]), 3)
        finally:
            spool.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_background_checkpoint_auto_scheduler(self):
        tokens = {"source": "src-tok", "viewer": "v-tok", "controller": "c-tok", "admin": "a-tok"}
        # Start server with auto_scan_interval = 0.2s
        server = AppServer(("127.0.0.1", 0), self.store, tokens, auto_scan_interval=0.2)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # Ingest work order with overdue checkpoint
            wo = {
                "schema_version": 1,
                "source_id": "erp",
                "event_id": "wo-overdue",
                "item_id": "ITEM-OVERDUE",
                "event_type": "WorkOrderReceived",
                "occurred_at": "2026-09-25T10:00:00+03:00",
                "payload": {
                    "external_order_id": "WO-99",
                    "version": 1,
                    "item_id": "ITEM-OVERDUE",
                    "route": ["A"],
                    "checkpoints": [
                        {
                            "id": "CP-OVERDUE",
                            "station_id": "A",
                            "event_type": "InspectionReported",
                            "due_at": "2020-01-01T00:00:00+00:00",  # past date
                        }
                    ],
                },
            }
            self.store.ingest(wo)

            # Wait 0.5s for background scheduler to tick
            time.sleep(0.5)

            # Status should now be automatically 'missing' without calling /scan
            cps = self.store.checkpoints("ITEM-OVERDUE")
            self.assertEqual(len(cps), 1)
            self.assertEqual(cps[0]["state"], "missing")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
