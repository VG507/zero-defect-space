import base64
import os
import threading
import unittest

from qc.bridge import Bridge
from qc.core import EventStore
from qc.demo import seed
from qc.emulator import EmulatorServer


class DemoTests(unittest.TestCase):
    def test_full_demo_route_with_separate_emulator(self):
        store = EventStore(":memory:", base64.b64encode(os.urandom(32)).decode())
        emulator = EmulatorServer(("127.0.0.1", 0), "route-secret")
        thread = threading.Thread(target=emulator.serve_forever, daemon=True)
        thread.start()
        try:
            bridge = Bridge(store, f"http://127.0.0.1:{emulator.server_port}", "route-secret")
            self.assertEqual(len(bridge.pull_orders()), 4)
            seed(store)
            self.assertEqual(len(store.items()), 4)
            self.assertEqual(store.metrics()["checked_items"], 3)
            self.assertEqual(store.metrics()["items_with_confirmed_defect"], 2)
            self.assertEqual(store.metrics()["items_without_usable_inspection"], 1)
            normal = store.history("I-001")
            self.assertEqual([run["station_id"] for run in normal["operation_runs"]], ["A", "B"])
            self.assertEqual([run["active_seconds"] for run in normal["operation_runs"]], [240, 300])
            self.assertEqual(normal["cases"], [])
            incoming = store.history("I-002")["cases"][0]
            self.assertEqual(incoming["context"]["classification"], "incoming_signal")
            self.assertEqual(incoming["cause_status"], "unknown")
            post = store.history("I-003")
            self.assertEqual(post["cases"][0]["context"]["classification"], "new_after_last_good_observation")
            self.assertEqual(len(post["cases"][0]["context"]["machine_ingestion_ids"]), 1)
            self.assertEqual(len(post["cases"][0]["context"]["operator_ingestion_ids"]), 1)
            warning = next(record["event"] for record in post["events"]
                           if record["event"]["event_type"] == "MachineWarning")
            self.assertEqual((warning["payload"]["equipment_id"], warning["payload"]["operation_run_id"]),
                             ("W-01", "R-003-A"))
            self.assertEqual(post["cases"][0]["cause_status"], "unknown")
            self.assertEqual(store.scan_checkpoints(), [{"item_id": "I-004", "checkpoint_id": "incoming-check", "state": "missing"}])
            self.assertEqual(store.history("I-004")["checkpoints"][0]["state"], "missing")
            self.assertEqual({row["item_id"]: row["state"] for row in store.line_overview()}["I-004"], "missing_control")
            self.assertEqual([row["state"] for row in bridge.push_results()], ["acknowledged", "acknowledged"])
            self.assertEqual(len(emulator.receipts), 2)
        finally:
            emulator.shutdown()
            emulator.server_close()
            thread.join()
            store.close()

    def test_synthetic_scenario_expected_results(self):
        store = EventStore(":memory:", base64.b64encode(os.urandom(32)).decode())
        try:
            seed(store)
            self.assertEqual(store.metrics()["checked_items"], 3)
            self.assertEqual(store.metrics()["items_with_confirmed_defect"], 2)
            self.assertEqual(store.metrics()["items_without_usable_inspection"], 1)
            self.assertEqual(store.metrics()["completed_operation_runs"], 3)
            case = store.history("I-003")["cases"][0]
            self.assertEqual(case["context"]["classification"], "new_after_last_good_observation")
            self.assertEqual(len(case["context"]["machine_ingestion_ids"]), 1)
            self.assertEqual(len(case["context"]["operator_ingestion_ids"]), 1)
            self.assertEqual(case["cause_status"], "unknown")
            states = {entry["item_id"]: entry["state"] for entry in store.line_overview()}
            self.assertEqual(states["I-001"], "no_confirmed_nonconformance")
            self.assertEqual(states["I-002"], "confirmed_nonconformance")
            self.assertEqual(states["I-004"], "insufficient_observation")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
