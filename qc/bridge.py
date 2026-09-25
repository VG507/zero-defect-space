"""Explicit inbound/outbound integration commands with retryable outbox."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from qc.core import EventStore


class Bridge:
    def __init__(self, store: EventStore, base_url: str, token: str):
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, path: str, body: dict | None = None):
        request = Request(self.base_url + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def pull_orders(self) -> list[dict]:
        results = []
        for order in self._request("/work-orders"):
            event = {"schema_version": 1, "source_id": "erp-emulator",
                     "event_id": f"order:{order['external_order_id']}:{order['version']}:{order['item_id']}",
                     "item_id": order["item_id"], "event_type": "WorkOrderReceived",
                     "occurred_at": "2026-09-25T09:00:00+03:00", "payload": order}
            results.append(self.store.ingest(event))
        return results

    def push_results(self) -> list[dict]:
        results = []
        for message in self.store.outbox():
            if message["state"] == "acknowledged":
                continue
            try:
                receipt = self._request("/results", message["payload"])
                if receipt.get("message_id") != message["message_id"] or receipt.get("status") != "acknowledged":
                    raise ValueError("invalid acknowledgement")
                self.store.record_delivery(message["message_id"], True)
                results.append({"message_id": message["message_id"], "state": "acknowledged"})
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                self.store.record_delivery(message["message_id"], False, str(exc))
                results.append({"message_id": message["message_id"], "state": "error", "detail": str(exc)})
        return results


def main():
    import sys
    if len(sys.argv) != 2 or sys.argv[1] not in {"pull", "push"}:
        raise SystemExit("usage: python -m qc.bridge pull|push")
    key, token = os.environ.get("QC_ENCRYPTION_KEY"), os.environ.get("QC_INTEGRATION_TOKEN")
    if not key or not token:
        raise SystemExit("QC_ENCRYPTION_KEY and QC_INTEGRATION_TOKEN are required")
    store = EventStore(os.environ.get("QC_DB_PATH", "qc-demo.db"), key)
    try:
        bridge = Bridge(store, os.environ.get("QC_EMULATOR_URL", "http://127.0.0.1:8766"), token)
        result = bridge.pull_orders() if sys.argv[1] == "pull" else bridge.push_results()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
