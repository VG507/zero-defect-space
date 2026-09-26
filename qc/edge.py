"""Edge buffer and store-and-forward client for industrial terminals and sensors.
Allows accumulating telemetry events locally when offline, and flushing them
in guaranteed order once connection to the central server is restored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


class EdgeSpoolBuffer:
    """Persistent on-disk FIFO buffer for offline edge events."""

    def __init__(self, spool_file: Path | str):
        self.spool_path = Path(spool_file)
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.spool_path.exists():
            self.spool_path.write_text("[]", encoding="utf-8")

    def _read(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.spool_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _write(self, items: list[dict[str, Any]]) -> None:
        self.spool_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def push(self, event: dict[str, Any]) -> None:
        """Saves event locally when disconnected."""
        items = self._read()
        items.append(event)
        self._write(items)

    def count(self) -> int:
        return len(self._read())

    def flush(self, target_url: str, token: str, timeout: float = 3.0) -> int:
        """Attempts to drain all buffered events to target HTTP endpoint.
        Stops on first network failure to preserve FIFO ordering.
        Returns number of successfully sent events.
        """
        items = self._read()
        if not items:
            return 0

        sent_count = 0
        remaining = list(items)

        for ev in items:
            req = Request(
                target_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(ev).encode("utf-8"),
            )
            try:
                with urlopen(req, timeout=timeout) as resp:
                    if resp.status in (200, 201, 202):
                        remaining.pop(0)
                        sent_count += 1
                    else:
                        break
            except Exception:
                # Network still down or server error: keep remaining events in spool
                break

        self._write(remaining)
        return sent_count
