import base64
import os
import tempfile
import unittest

from qc.core import EventStore


def event(item, number, kind, result=None, quality="good", defects=None, time="2026-09-25T10:00:00+03:00"):
    payload = {}
    if result is not None:
        payload = {"inspection_result": result, "observation_quality": quality, "defects": defects or []}
    return {"schema_version": 1, "source_id": "demo-camera", "event_id": f"{item}-{number}",
            "item_id": item, "event_type": kind, "occurred_at": time, "payload": payload}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(os.path.join(self.temp.name, "qc.db"), base64.b64encode(os.urandom(32)).decode())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_dedup_conflict_and_quarantine(self):
        value = event("I-1", 1, "InspectionReported", "no_signs_detected")
        first = self.store.ingest(value)
        self.assertEqual(first["state"], "applied")
        self.assertEqual(self.store.ingest(value)["state"], "duplicate")
        self.assertEqual(len(self.store.history("I-1")["events"]), 1)
        self.assertEqual(self.store.ingest({**value, "payload": {"inspection_result": "unable_to_assess"}})["state"], "conflict")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ingestion_conflicts").fetchone()[0], 1)
        bad = event("I-1", 2, "InspectionReported", "invalid")
        self.assertEqual(self.store.ingest(bad)["state"], "quarantined")
        invalid_order = event("I-1", 3, "WorkOrderReceived")
        invalid_order["payload"] = {"external_order_id": "X", "version": 1, "item_id": "WRONG", "route": ["A"]}
        self.assertEqual(self.store.ingest(invalid_order)["state"], "quarantined")
        self.assertEqual(self.store.metrics()["checked_items"], 1)

    def test_baseline_metrics_and_decision_idempotency(self):
        values = [
            event("I-001", 1, "InspectionReported", "no_signs_detected"),
            event("I-002", 1, "IncomingInspectionCompleted", "signs_detected",
                  defects=[{"type": "scratch", "area": "outer"}]),
            event("I-003", 1, "InspectionReported", "signs_detected",
                  defects=[{"type": "weld_anomaly", "area": "seam-1"}]),
            event("I-004", 1, "InspectionReported", "unable_to_assess", quality="poor"),
        ]
        for value in values:
            self.store.ingest(value)
        cases = self.store.cases()
        self.assertEqual(len(cases), 2)
        for index, case in enumerate(cases):
            decided = self.store.decide(case["case_id"], "confirmed", "controller-1", "demo evidence", 1, f"decision-{index}")
            self.assertEqual(decided["status"], "confirmed")
            same = self.store.decide(case["case_id"], "confirmed", "controller-1", "demo evidence", 1, f"decision-{index}")
            self.assertEqual(same["version"], 2)
        self.assertEqual(len(self.store.outbox()), 2)
        metrics = self.store.metrics()
        self.assertEqual(metrics["checked_items"], 3)
        self.assertEqual(metrics["items_with_confirmed_defect"], 2)
        self.assertEqual(metrics["items_without_usable_inspection"], 1)
        self.assertEqual(metrics["confirmed_incoming_cases"], 1)
        self.assertEqual(metrics["confirmed_post_operation_cases"], 1)

    def test_multiple_defects_and_changed_decision(self):
        value = event("I-5", 1, "InspectionReported", "signs_detected", defects=[
            {"type": "scratch", "area": "A"}, {"type": "dent", "area": "B"}])
        self.store.ingest(value)
        self.store.ingest(event("I-5", 2, "InspectionReported", "signs_detected",
                                defects=[{"type": "scratch", "area": "A"}]))
        cases = self.store.cases()
        self.assertEqual(len(cases), 2)
        first = cases[0]
        self.store.decide(first["case_id"], "confirmed", "controller", "initial", 1, "a")
        updated = self.store.decide(first["case_id"], "rejected", "controller", "new evidence", 2, "b")
        self.assertEqual(updated["version"], 3)
        self.assertEqual(len(updated["decisions"]), 2)
        self.assertEqual(self.store.metrics()["confirmed_defect_cases"], 0)

    def test_late_order_and_tamper_detection(self):
        late = event("I-6", 1, "ItemReceived", time="2026-09-25T09:00:00+03:00")
        newer = event("I-6", 2, "InspectionReported", "no_signs_detected",
                      time="2026-09-25T10:00:00+03:00")
        self.store.ingest(newer)
        self.store.ingest(late)
        history = self.store.history("I-6")["events"]
        self.assertEqual([r["event"]["event_id"] for r in history], ["I-6-1", "I-6-2"])
        self.assertTrue(self.store.verify_integrity()["valid"])
        self.store.db.execute("UPDATE raw_events SET ciphertext=? WHERE event_id=?", (b"tampered", "I-6-1"))
        with self.assertRaises(Exception):
            self.store.verify_integrity()

    def test_late_incoming_evidence_reclassifies_without_losing_decision(self):
        post = event("I-7", 2, "InspectionReported", "signs_detected",
                     defects=[{"type": "scratch", "area": "outer"}], time="2026-09-25T11:00:00+03:00")
        self.store.ingest(post)
        case = self.store.cases()[0]
        self.store.decide(case["case_id"], "confirmed", "controller", "first evidence", 1, "decision")
        before = event("I-7", 1, "IncomingInspectionCompleted", "signs_detected",
                       defects=[{"type": "scratch", "area": "outer"}], time="2026-09-25T09:00:00+03:00")
        self.store.ingest(before)
        self.assertEqual(self.store.cases()[0]["case_id"], case["case_id"])
        self.assertTrue(self.store.cases()[0]["review_required"])
        self.assertEqual(self.store.line_overview()[0]["state"], "review_required")
        self.assertEqual(self.store.cases()[0]["context"]["classification"], "incoming_signal")
        self.assertEqual(self.store.metrics()["confirmed_incoming_cases"], 1)

    def test_operation_intervals_rework_and_incomplete_boundary(self):
        values = [
            event("I-8", 1, "OperationStarted", time="2026-09-25T10:00:00+03:00"),
            event("I-8", 2, "OperationPaused", time="2026-09-25T10:02:00+03:00"),
            event("I-8", 3, "OperationResumed", time="2026-09-25T10:03:00+03:00"),
            event("I-8", 4, "OperationFinished", time="2026-09-25T10:05:00+03:00"),
            event("I-8", 5, "ReworkStarted", time="2026-09-25T10:06:00+03:00"),
        ]
        for value in values:
            value["payload"] = {"operation_run_id": "R-8-1" if value["event_type"] != "ReworkStarted" else "R-8-2"}
            if value["event_type"] == "ReworkStarted":
                value["payload"]["previous_run_id"] = "R-8-1"
            self.store.ingest(value)
        runs = self.store.history("I-8")["operation_runs"]
        self.assertEqual(runs[0]["elapsed_seconds"], 300)
        self.assertEqual(runs[0]["active_seconds"], 240)
        self.assertIsNone(runs[1]["active_seconds"])
        self.assertEqual(runs[1]["incomplete_reason"], "missing start or finish")
        self.assertEqual(self.store.metrics()["rework_runs"], 1)

    def test_checkpoint_missing_then_resolved_by_late_usable_observation(self):
        order = event("I-9", 1, "WorkOrderReceived", time="2026-09-25T08:00:00+03:00")
        order["payload"] = {"external_order_id": "W-9", "version": 1, "item_id": "I-9",
                            "route": ["incoming"], "checkpoints": [{"id": "C-1", "station_id": "incoming",
                            "event_type": "IncomingInspectionCompleted", "due_at": "2026-09-25T10:05:00+03:00"}]}
        self.store.ingest(order)
        poor = event("I-9", 2, "IncomingInspectionCompleted", "unable_to_assess", quality="poor",
                     time="2026-09-25T10:03:00+03:00")
        poor["station_id"] = "incoming"
        self.store.ingest(poor)
        self.assertEqual(self.store.checkpoints("I-9", "2026-09-25T10:04:00+03:00")[0]["state"], "awaiting")
        self.assertEqual(self.store.scan_checkpoints("2026-09-25T10:06:00+03:00")[0]["state"], "missing")
        self.assertEqual(self.store.scan_checkpoints("2026-09-25T10:06:00+03:00"), [])
        good = event("I-9", 3, "IncomingInspectionCompleted", "no_signs_detected",
                     time="2026-09-25T10:04:00+03:00")
        good["station_id"] = "incoming"
        self.store.ingest(good)
        self.assertEqual(self.store.scan_checkpoints("2026-09-25T10:07:00+03:00")[0]["state"], "resolved")
        checkpoint = self.store.checkpoints("I-9", "2026-09-25T10:07:00+03:00")[0]
        self.assertEqual(checkpoint["state"], "observed")
        self.assertEqual([entry["state"] for entry in checkpoint["alert_history"]], ["missing", "resolved"])

    def test_poor_quality_signal_is_not_a_confirmable_case(self):
        poor = event("I-10", 1, "InspectionReported", "signs_detected", quality="poor",
                     defects=[{"type": "scratch", "area": "A"}])
        self.assertEqual(self.store.ingest(poor)["state"], "applied")
        self.assertEqual(self.store.cases(), [])
        self.assertEqual(self.store.metrics()["checked_items"], 0)
        self.assertEqual(self.store.metrics()["items_without_usable_inspection"], 1)


if __name__ == "__main__":
    unittest.main()
