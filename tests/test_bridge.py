import base64
import json
import os
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from qc.bridge import Bridge
from qc.core import EventStore
from qc.emulator import EmulatorServer


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(os.path.join(self.temp.name, "qc.db"), base64.b64encode(os.urandom(32)).decode())
        self.emulator = EmulatorServer(("127.0.0.1", 0), "integration-secret")
        self.thread = threading.Thread(target=self.emulator.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.emulator.server_port}"
        self.bridge = Bridge(self.store, self.base, "integration-secret")

    def tearDown(self):
        self.emulator.shutdown()
        self.emulator.server_close()
        self.thread.join()
        self.store.close()
        self.temp.cleanup()

    def test_bidirectional_failure_retry_and_idempotency(self):
        self.assertEqual(len(self.bridge.pull_orders()), 4)
        self.assertTrue(all(x["state"] == "duplicate" for x in self.bridge.pull_orders()))
        self.assertEqual(len(self.store.items()), 4)
        event = {"schema_version": 1, "source_id": "camera", "event_id": "i-1", "item_id": "I-001",
                 "event_type": "InspectionReported", "occurred_at": "2026-09-25T10:00:00+03:00",
                 "payload": {"inspection_result": "signs_detected", "observation_quality": "good",
                             "defects": [{"type": "scratch", "area": "outer"}]}}
        self.store.ingest(event)
        case = self.store.cases()[0]
        self.store.decide(case["case_id"], "confirmed", "tester", "seen", 1, "decision-1")
        self.assertEqual(self.store.outbox()[0]["payload"]["external_order_id"], "DEMO-ORDER-26-04")
        req = Request(self.base + "/control/fail-next", data=b"{}", headers={"Authorization": "Bearer integration-secret"})
        with urlopen(req) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.bridge.push_results()[0]["state"], "error")
        self.assertEqual(len(self.emulator.receipts), 0)
        self.assertEqual(self.bridge.push_results()[0]["state"], "acknowledged")
        self.assertEqual(len(self.emulator.receipts), 1)
        self.assertEqual(self.bridge.push_results(), [])
        self.assertEqual(self.store.outbox()[0]["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
