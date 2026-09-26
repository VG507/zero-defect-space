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
            integrity = store.verify_integrity()
            self.assertEqual(integrity["checked_events"], 39)
            self.assertTrue(all(integrity[name] for name in
                                ("valid", "event_digests_verified", "hash_chain_verified", "anchor_verified")))
            self.assertEqual(len(store.items()), 6)
            self.assertEqual(store.metrics()["checked_items"], 5)
            self.assertEqual(store.metrics()["items_with_confirmed_defect"], 3)
            self.assertEqual(store.metrics()["items_without_usable_inspection"], 1)
            normal = store.history("I-001")
            self.assertEqual(len(normal["components"]), 2)
            self.assertEqual(normal["components"][0]["component_id"], "C-001-A")
            self.assertTrue(normal["components"][0]["installed"])
            self.assertEqual([run["station_id"] for run in normal["operation_runs"]], ["A", "B"])
            self.assertEqual([run["operator_alias"] for run in normal["operation_runs"]], ["operator-1", "operator-2"])
            self.assertEqual([run["equipment_id"] for run in normal["operation_runs"]], ["CNC-01", "ASM-01"])
            self.assertEqual([run["active_seconds"] for run in normal["operation_runs"]], [240, 300])
            self.assertEqual(normal["cases"], [])
            incoming = store.history("I-002")["cases"][0]
            self.assertEqual(incoming["context"]["classification"], "incoming_signal")
            self.assertEqual(incoming["cause_status"], "unknown")
            post = store.history("I-003")
            self.assertEqual(post["cases"][0]["context"]["classification"], "new_after_last_good_observation")
            self.assertEqual(len(post["cases"][0]["context"]["machine_ingestion_ids"]), 1)
            self.assertEqual(len(post["cases"][0]["context"]["operator_ingestion_ids"]), 1)
            self.assertEqual(post["cases"][0]["context"]["component_id"], "C-003-A")
            self.assertEqual(post["cases"][0]["context"]["operation_run_id"], "R-003-A")
            warning = next(record["event"] for record in post["events"]
                           if record["event"]["event_type"] == "MachineWarning")
            self.assertEqual((warning["payload"]["equipment_id"], warning["payload"]["operation_run_id"]),
                             ("W-01", "R-003-A"))
            self.assertEqual(post["cases"][0]["cause_status"], "unknown")
            rework = store.history("I-005")
            self.assertEqual([run["active_seconds"] for run in rework["operation_runs"]], [240, 240])
            self.assertEqual(rework["operation_runs"][1]["previous_run_id"], "R-005-1")
            self.assertEqual([change["action"] for change in rework["components"][0]["transitions"]],
                             ["imported", "removed", "installed"])
            self.assertEqual([d["action"] for d in rework["cases"][0]["decisions"]],
                             ["confirmed", "needs_more_inspection"])
            self.assertEqual(rework["cases"][0]["status"], "needs_more_inspection")
            late = store.history("I-006")
            self.assertEqual([record["event"]["event_id"] for record in late["events"]],
                             ["I-006-0", "I-006-1", "I-006-3", "I-006-2"])
            self.assertEqual(late["cases"][0]["first_ingestion_id"], late["events"][3]["ingestion_id"])
            self.assertEqual(late["cases"][0]["context"]["classification"], "incoming_signal")
            self.assertTrue(late["cases"][0]["review_required"])
            self.assertEqual(len(late["cases"][0]["decisions"]), 1)
            self.assertEqual(store.scan_checkpoints(), [{"item_id": "I-004", "checkpoint_id": "incoming-check", "state": "missing"}])
            self.assertEqual(store.history("I-004")["checkpoints"][0]["state"], "missing")
            self.assertEqual({row["item_id"]: row["state"] for row in store.line_overview()}["I-004"], "missing_control")
            self.assertEqual([row["state"] for row in bridge.push_results()], ["acknowledged"] * 5)
            self.assertEqual(len(emulator.receipts), 5)
            self.assertEqual({row["payload"]["item_id"] for row in store.outbox()},
                             {"I-002", "I-003", "I-005", "I-006"})
        finally:
            emulator.shutdown()
            emulator.server_close()
            thread.join()
            store.close()

    def test_synthetic_scenario_expected_results(self):
        store = EventStore(":memory:", base64.b64encode(os.urandom(32)).decode())
        try:
            seed(store)
            self.assertEqual(store.metrics()["checked_items"], 5)
            self.assertEqual(store.metrics()["items_with_confirmed_defect"], 3)
            self.assertEqual(store.metrics()["items_without_usable_inspection"], 1)
            self.assertEqual(store.metrics()["completed_operation_runs"], 5)
            self.assertEqual(store.metrics()["rework_runs"], 1)
            case = store.history("I-003")["cases"][0]
            self.assertEqual(case["context"]["classification"], "new_after_last_good_observation")
            self.assertEqual(len(case["context"]["machine_ingestion_ids"]), 1)
            self.assertEqual(len(case["context"]["operator_ingestion_ids"]), 1)
            self.assertEqual(case["cause_status"], "unknown")
            states = {entry["item_id"]: entry["state"] for entry in store.line_overview()}
            self.assertEqual(states["I-001"], "no_confirmed_nonconformance")
            self.assertEqual(states["I-002"], "confirmed_nonconformance")
            self.assertEqual(states["I-004"], "insufficient_observation")
            self.assertEqual(states["I-005"], "review_required")
            self.assertEqual(states["I-006"], "review_required")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
