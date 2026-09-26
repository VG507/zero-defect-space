"""Local HTTP transport for the quality-control demo. Bind to loopback by default."""

from __future__ import annotations

import json
import argparse
import os
import ssl
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from qc.core import EventStore
from qc.crypto import keyring_from_environment


class AppServer(ThreadingHTTPServer):
    def __init__(self, address, store: EventStore, tokens: dict[str, str], tls_cert: str | None = None, tls_key: str | None = None, auto_scan_interval: float = 0.0):
        if bool(tls_cert) != bool(tls_key):
            raise ValueError("TLS certificate and key must be provided together")
        ctx = None
        if tls_cert and tls_key:
            if not os.path.isfile(tls_cert) or not os.path.isfile(tls_key):
                raise FileNotFoundError("TLS certificate or key not found")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=tls_cert, keyfile=tls_key)
        super().__init__(address, AppHandler)
        self.store = store
        self.tokens = tokens
        self._stop_scheduler = threading.Event()
        self._scheduler_thread = None

        if ctx is not None:
            self.socket = ctx.wrap_socket(self.socket, server_side=True)

        if auto_scan_interval > 0:
            def _scheduler_loop():
                while not self._stop_scheduler.is_set():
                    try:
                        self.store.scan_checkpoints()
                    except Exception:
                        pass
                    self._stop_scheduler.wait(timeout=auto_scan_interval)

            self._scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
            self._scheduler_thread.start()

    def server_close(self) -> None:
        self._stop_scheduler.set()
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=1.0)
        super().server_close()


class AppHandler(BaseHTTPRequestHandler):
    server: AppServer

    def _respond(self, status: int, value: object) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, *roles: str) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            self._respond(HTTPStatus.UNAUTHORIZED, {"error": "bearer token required"})
            return False
        token = header[7:]
        if token not in [self.server.tokens[role] for role in roles]:
            self._respond(HTTPStatus.FORBIDDEN, {"error": "role not permitted"})
            return False
        return True

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0 or length > 1024 * 1024:
            raise ValueError("JSON body size must be 1..1048576 bytes")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be object")
        return value

    def _serve_file(self, name: str, media: str) -> None:
        data = (Path(__file__).parent.parent / "web" / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/health":
            self._respond(HTTPStatus.OK, {"status": "ok"})
            return
        if path in {"/", "/app.js", "/style.css", "/scenario.css"}:
            name, media = {"/": ("index.html", "text/html; charset=utf-8"),
                           "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                           "/style.css": ("style.css", "text/css; charset=utf-8"),
                           "/scenario.css": ("scenario.css", "text/css; charset=utf-8")}[path]
            self._serve_file(name, media)
            return
        if not self._authorized("viewer", "controller", "admin"):
            return
        try:
            if path == "/api/me":
                token = self.headers["Authorization"][7:]
                result = {"role": next(role for role, value in self.server.tokens.items() if value == token)}
            elif path == "/api/items":
                result = self.server.store.items()
            elif path == "/api/line":
                result = self.server.store.line_overview()
            elif path.startswith("/api/items/"):
                result = self.server.store.history(path.removeprefix("/api/items/"))
            elif path == "/api/cases":
                result = self.server.store.cases()
            elif path == "/api/metrics":
                result = self.server.store.metrics()
            elif path == "/api/outbox":
                result = self.server.store.outbox()
            elif path == "/api/checkpoints":
                result = [{"item_id": item["item_id"], "checkpoints": self.server.store.checkpoints(item["item_id"])}
                          for item in self.server.store.items()]
            elif path == "/api/integrity":
                if not self._authorized("admin"):
                    return
                result = self.server.store.verify_integrity()
            else:
                self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._respond(HTTPStatus.OK, result)
        except Exception as exc:
            self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__, "detail": str(exc)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/events":
                if not self._authorized("source", "admin"):
                    return
                result = self.server.store.ingest(self._body())
                self._respond(HTTPStatus.CONFLICT if result["state"] == "conflict" else HTTPStatus.ACCEPTED, result)
                return
            if path == "/api/checkpoints/scan":
                if not self._authorized("admin"):
                    return
                body = self._body()
                self._respond(HTTPStatus.OK, self.server.store.scan_checkpoints(body.get("as_of")))
                return
            if path.startswith("/api/cases/") and path.endswith("/decisions"):
                if not self._authorized("controller", "admin"):
                    return
                case_id = path.split("/")[3]
                body = self._body()
                result = self.server.store.decide(
                    case_id, body.get("action", ""), body.get("actor", ""),
                    body.get("reason", ""), body.get("expected_version"),
                    body.get("idempotency_key", ""),
                )
                self._respond(HTTPStatus.CREATED, result)
                return
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except KeyError as exc:
            self._respond(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            status = HTTPStatus.CONFLICT if "conflict" in str(exc) or "stale" in str(exc) else HTTPStatus.BAD_REQUEST
            self._respond(status, {"error": str(exc)})
        except Exception as exc:
            self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__, "detail": str(exc)})


def serve() -> None:
    parser = argparse.ArgumentParser(description="Quality-control HTTP server")
    parser.add_argument("--ssl-cert", default=os.environ.get("QC_TLS_CERT"))
    parser.add_argument("--ssl-key", default=os.environ.get("QC_TLS_KEY"))
    args = parser.parse_args()
    key = os.environ.get("QC_ENCRYPTION_KEY")
    if not key:
        raise SystemExit("QC_ENCRYPTION_KEY is required")
    names = {role: os.environ.get(f"QC_{role.upper()}_TOKEN") for role in ("source", "viewer", "controller", "admin")}
    if any(not value for value in names.values()) or len(set(names.values())) != 4:
        raise SystemExit("four distinct QC_SOURCE/VIEWER/CONTROLLER/ADMIN_TOKEN values are required")
    store = EventStore(os.environ.get("QC_DB_PATH", "qc-demo.db"), keyring_from_environment(key))
    host = os.environ.get("QC_BIND", "127.0.0.1")
    port = int(os.environ.get("QC_PORT", "8765"))
    tls_cert = args.ssl_cert
    tls_key = args.ssl_key
    scan_interval = float(os.environ.get("QC_CHECKPOINT_SCAN_INTERVAL", "0"))
    server = AppServer((host, port), store, names, tls_cert=tls_cert, tls_key=tls_key, auto_scan_interval=scan_interval)
    protocol = "https" if (tls_cert and tls_key) else "http"
    print(f"Zero Defect Space demo listening at {protocol}://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    serve()
