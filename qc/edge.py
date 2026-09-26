"""Durable SQLite FIFO for offline event forwarding."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from urllib.request import Request, urlopen


class EdgeSpoolBuffer:
    def __init__(self, spool_file: Path | str):
        self.spool_path = Path(spool_file)
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.spool_path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS queued_events (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        self.lock = threading.RLock()

    def push(self, event: dict) -> None:
        body = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self.lock, self.db:
            self.db.execute("INSERT INTO queued_events(body) VALUES (?)", (body,))

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM queued_events").fetchone()[0]

    def flush(self, target_url: str, token: str, timeout: float = 3.0) -> int:
        sent = 0
        with self.lock:
            while True:
                row = self.db.execute("SELECT id,body FROM queued_events ORDER BY id LIMIT 1").fetchone()
                if row is None:
                    return sent
                request = Request(target_url, data=row[1].encode("utf-8"), headers={
                    "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                try:
                    with urlopen(request, timeout=timeout) as response:
                        answer = json.load(response)
                        if response.status not in (200, 201, 202) or answer.get("state") not in ("applied", "duplicate"):
                            return sent
                except Exception:
                    return sent
                with self.db:
                    self.db.execute("DELETE FROM queued_events WHERE id=?", (row[0],))
                sent += 1

    def forward_forever(self, target_url: str, token: str, stop: threading.Event,
                        interval: float = 3.0) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        while not stop.is_set():
            self.flush(target_url, token)
            stop.wait(interval)

    def close(self) -> None:
        self.db.close()
