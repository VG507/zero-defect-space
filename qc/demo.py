"""Self-contained local demonstration with synthetic, non-production data."""

import base64
import os
import secrets
import threading

from qc.bridge import Bridge
from qc.core import EventStore
from qc.emulator import EmulatorServer
from qc.server import AppServer


def sample_event(item, number, kind, minute, payload, station=None, operator=None):
    event = {"schema_version": 1, "source_id": "synthetic-line", "event_id": f"{item}-{number}",
             "item_id": item, "event_type": kind, "occurred_at": f"2026-09-25T10:{minute:02d}:00+03:00",
             "payload": payload}
    if station:
        event["station_id"] = station
    if operator:
        event["operator_alias"] = operator
    return event


def seed(store: EventStore) -> None:
    normal = {"inspection_result": "no_signs_detected", "observation_quality": "good", "defects": []}
    incoming = {"inspection_result": "signs_detected", "observation_quality": "good",
                "defects": [{"type": "scratch", "area": "outer-surface"}]}
    after = {"inspection_result": "signs_detected", "observation_quality": "good",
             "defects": [{"type": "weld_anomaly", "area": "seam-A"}]}
    unknown = {"inspection_result": "unable_to_assess", "observation_quality": "poor", "defects": []}
    events = [
        sample_event("I-001", 1, "ItemReceived", 0, {"item_type": "demo-bracket"}),
        sample_event("I-001", 2, "IncomingInspectionCompleted", 1, normal, "incoming"),
        sample_event("I-001", 3, "OperationStarted", 5, {"operation_run_id": "R-001-A", "operation": "machining"}, "A", "operator-1"),
        sample_event("I-001", 4, "OperationFinished", 9, {"operation_run_id": "R-001-A"}, "A", "operator-1"),
        sample_event("I-001", 5, "OperationStarted", 12, {"operation_run_id": "R-001-B", "operation": "assembly"}, "B", "operator-2"),
        sample_event("I-001", 6, "OperationFinished", 17, {"operation_run_id": "R-001-B"}, "B", "operator-2"),
        sample_event("I-001", 7, "InspectionReported", 18, normal, "B"),
        sample_event("I-002", 1, "ItemReceived", 0, {"item_type": "demo-bracket"}),
        sample_event("I-002", 2, "IncomingInspectionCompleted", 2, incoming, "incoming"),
        sample_event("I-003", 1, "IncomingInspectionCompleted", 1, normal, "incoming"),
        sample_event("I-003", 2, "OperationStarted", 5, {"operation_run_id": "R-003-A", "operation": "welding"}, "A", "operator-1"),
        sample_event("I-003", 3, "MachineWarning", 7, {"equipment_id": "W-01", "code": "TEMP_WARN", "operation_run_id": "R-003-A"}, "A"),
        sample_event("I-003", 4, "OperatorActionObserved", 8, {"action": "manual_adjustment", "operation_run_id": "R-003-A", "confidence": 0.7}, "A", "operator-1"),
        sample_event("I-003", 5, "OperationFinished", 10, {"operation_run_id": "R-003-A"}, "A", "operator-1"),
        sample_event("I-003", 6, "InspectionReported", 11, after, "A"),
        sample_event("I-004", 1, "IncomingInspectionCompleted", 3, unknown, "incoming"),
    ]
    for event in events:
        store.ingest(event)
    for case in store.cases():
        store.decide(case["case_id"], "confirmed", "demo-controller",
                     "Synthetic scenario; manual confirmation for demonstration", 1,
                     f"seed-{case['item_id']}")


def main() -> None:
    key = base64.b64encode(os.urandom(32)).decode()
    store = EventStore(":memory:", key)
    integration_token = secrets.token_urlsafe(20)
    emulator = EmulatorServer(("127.0.0.1", 0), integration_token)
    emulator_thread = threading.Thread(target=emulator.serve_forever, daemon=True)
    emulator_thread.start()
    bridge = Bridge(store, f"http://127.0.0.1:{emulator.server_port}", integration_token)
    bridge.pull_orders()
    seed(store)
    checkpoint_alerts = store.scan_checkpoints()
    delivery = bridge.push_results()
    tokens = {role: os.environ.get(f"QC_DEMO_{role.upper()}_TOKEN") or secrets.token_urlsafe(20)
              for role in ("source", "viewer", "controller", "admin")}
    port = int(os.environ.get("QC_PORT", "8765"))
    server = AppServer(("127.0.0.1", port), store, tokens)
    print(f"Demo: http://127.0.0.1:{port}", flush=True)
    print(f"Viewer token: {tokens['viewer']}", flush=True)
    print(f"Controller token: {tokens['controller']}", flush=True)
    print(f"Integration: {len(delivery)} result(s) acknowledged by separate HTTP emulator", flush=True)
    print(f"Checkpoint alerts: {len(checkpoint_alerts)} unresolved synthetic checkpoint(s)", flush=True)
    print("Data and tokens exist only in this process; closing it discards the demo.", flush=True)
    try:
        server.serve_forever()
    finally:
        emulator.shutdown()
        emulator.server_close()
        emulator_thread.join()
        store.close()


if __name__ == "__main__":
    main()
