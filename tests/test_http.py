import base64
import json
import os
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from qc.core import EventStore
from qc.server import AppServer


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(os.path.join(self.temp.name, "test.db"), base64.b64encode(os.urandom(32)).decode())
        self.server = AppServer(("127.0.0.1", 0), self.store,
                                {"viewer": "view", "source": "source", "controller": "control", "admin": "admin"})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.close()
        self.temp.cleanup()

    def request(self, path, token=None, body=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_auth_and_event_flow(self):
        self.assertEqual(self.request("/api/metrics")[0], 401)
        self.assertEqual(self.request("/api/me", "view")[1]["role"], "viewer")
        self.assertEqual(self.request("/api/events", "view", {"x": 1})[0], 403)
        value = {"schema_version": 1, "source_id": "camera", "event_id": "e-1", "item_id": "I-1",
                 "event_type": "InspectionReported", "occurred_at": "2026-09-25T10:00:00+03:00",
                 "payload": {"inspection_result": "signs_detected", "observation_quality": "good",
                             "defects": [{"type": "scratch", "area": "A"}]}}
        self.assertEqual(self.request("/api/events", "source", value)[0], 202)
        self.assertEqual(self.request("/api/events", "source", value)[1]["state"], "duplicate")
        status, cases = self.request("/api/cases", "view")
        self.assertEqual(status, 200)
        self.assertEqual(len(cases), 1)
        case = cases[0]
        decision = {"action": "confirmed", "actor": "tester", "reason": "visible mark",
                    "expected_version": case["version"], "idempotency_key": "d-1"}
        path = f"/api/cases/{case['case_id']}/decisions"
        self.assertEqual(self.request(path, "view", decision)[0], 403)
        self.assertEqual(self.request(path, "control", decision)[0], 201)
        self.assertEqual(self.request(path, "control", {**decision, "idempotency_key": "d-2"})[0], 409)
        self.assertEqual(self.request("/api/metrics", "view")[1]["items_with_confirmed_defect"], 1)
        self.assertEqual(self.request("/api/line", "view")[1][0]["state"], "confirmed_nonconformance")
        self.assertEqual(self.request("/api/checkpoints", "view")[1][0]["checkpoints"], [])
        self.assertEqual(self.request("/api/checkpoints/scan", "view", {})[0], 403)
        self.assertEqual(len(self.request("/api/outbox", "view")[1]), 1)
        self.assertTrue(self.request("/api/integrity", "admin")[1]["valid"])

    def test_batch_event_flow_and_atomic_rejection(self):
        first = {"schema_version": 1, "source_id": "source-a", "event_id": "batch-a",
                 "item_id": "ITEM-A", "event_type": "ItemReceived",
                 "occurred_at": "2026-09-25T10:00:00+03:00", "payload": {}}
        second = {**first, "event_id": "batch-b"}
        self.assertEqual(self.request("/api/events/batch", "view", [first])[0], 403)
        self.assertEqual(self.request("/api/events/batch", "source", [])[0], 400)
        self.assertEqual(self.request("/api/events/batch", "source", [first] * 101)[0], 400)
        status, body = self.request("/api/events/batch", "source", [first, second])
        self.assertEqual(status, 202)
        self.assertEqual([item["state"] for item in body["results"]], ["applied", "applied"])
        self.assertEqual(self.request("/api/integrity", "admin")[1]["checked_events"], 2)
        rejected = {**first, "event_id": "batch-c"}
        status, _ = self.request("/api/events/batch", "source", [rejected, {**second, "source_id": ""}])
        self.assertEqual(status, 400)
        self.assertEqual(self.request("/api/integrity", "admin")[1]["checked_events"], 2)


if __name__ == "__main__":
    unittest.main()
