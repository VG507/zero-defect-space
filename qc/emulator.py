"""Independent HTTP production-system emulator; synthetic contract, not 1C/Galaktika API."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ORDERS = [
    {"external_order_id": "DEMO-ORDER-26-04", "version": 1, "item_id": f"I-{number:03d}",
     "item_type": "demo-bracket", "route": ["incoming", "A", "B"],
     "checkpoints": [{"id": "incoming-check", "station_id": "incoming",
                      "event_type": "IncomingInspectionCompleted", "due_at": "2026-09-25T10:05:00+03:00"}]
     if number == 4 else []}
    for number in range(1, 5)
]


class EmulatorServer(ThreadingHTTPServer):
    def __init__(self, address, token: str):
        super().__init__(address, EmulatorHandler)
        self.token = token
        self.receipts: dict[str, dict] = {}
        self.fail_next = False


class EmulatorHandler(BaseHTTPRequestHandler):
    server: EmulatorServer

    def _respond(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            self._respond(HTTPStatus.UNAUTHORIZED, {"error": "invalid integration token"})
            return False
        return True

    def do_GET(self):
        if self.path == "/health":
            self._respond(HTTPStatus.OK, {"status": "ok"})
            return
        if not self._auth():
            return
        if self.path == "/work-orders":
            self._respond(HTTPStatus.OK, ORDERS)
        elif self.path == "/receipts":
            self._respond(HTTPStatus.OK, list(self.server.receipts.values()))
        else:
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            return
        if self.path == "/control/fail-next":
            self.server.fail_next = True
            self._respond(HTTPStatus.OK, {"fail_next": True})
            return
        if self.path != "/results":
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 65536:
                raise ValueError("invalid body length")
            body = json.loads(self.rfile.read(length))
            message_id = body["message_id"]
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("message_id required")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if self.server.fail_next:
            self.server.fail_next = False
            self._respond(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "simulated temporary failure"})
            return
        if message_id in self.server.receipts:
            self._respond(HTTPStatus.OK, self.server.receipts[message_id])
            return
        receipt = {"message_id": message_id, "status": "acknowledged", "external_receipt_id": "ACK-" + message_id}
        self.server.receipts[message_id] = receipt
        self._respond(HTTPStatus.CREATED, receipt)


def main():
    token = os.environ.get("QC_INTEGRATION_TOKEN")
    if not token:
        raise SystemExit("QC_INTEGRATION_TOKEN is required")
    port = int(os.environ.get("QC_EMULATOR_PORT", "8766"))
    host = os.environ.get("QC_BIND", "127.0.0.1")
    server = EmulatorServer((host, port), token)
    print(f"Synthetic ERP emulator listening at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
