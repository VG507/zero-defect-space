"""Event-sourced quality-control core. No production-system compatibility is implied."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qc.crypto import DEFAULT_PROFILE, HYBRID_PQ_PROFILE, KeyRing


EVENT_TYPES = {
    "WorkOrderReceived", "ItemReceived", "IncomingInspectionCompleted",
    "OperationStarted", "OperationPaused", "OperationResumed", "OperationFinished",
    "ReworkStarted", "InspectionReported", "OperatorActionObserved",
    "MachineStateChanged", "MachineWarning", "MachineStopped", "AssemblyImported",
}
INSPECTION_TYPES = {"InspectionReported", "IncomingInspectionCompleted"}
RESULTS = {"signs_detected", "no_signs_detected", "unable_to_assess"}
QUALITIES = {"good", "poor", "unknown"}
DECISIONS = {"confirmed", "rejected", "needs_more_inspection"}
OPERATION_TYPES = {"OperationStarted", "OperationPaused", "OperationResumed", "OperationFinished", "ReworkStarted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def parse_time(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("occurred_at must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("occurred_at must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def project_runs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute intervals from already ordered events; never invent a missing boundary."""
    runs: dict[str, dict[str, Any]] = {}
    for record in events:
        event = record["event"]
        kind = event["event_type"]
        if kind not in OPERATION_TYPES or record["state"] != "applied":
            continue
        payload = event["payload"]
        run_id = payload["operation_run_id"]
        run = runs.setdefault(run_id, {"operation_run_id": run_id, "start": None, "finish": None,
                                       "station_id": None, "operator_alias": None, "operation": None,
                                       "previous_run_id": None, "pauses": [], "active_seconds": None,
                                       "elapsed_seconds": None, "incomplete_reason": None})
        timestamp = parse_time(event["occurred_at"])
        if kind in {"OperationStarted", "ReworkStarted"}:
            run["start"] = run["start"] or timestamp
            run["station_id"] = event.get("station_id") or run["station_id"]
            run["operator_alias"] = event.get("operator_alias") or run["operator_alias"]
            run["operation"] = payload.get("operation") or run["operation"]
            run["previous_run_id"] = payload.get("previous_run_id") or run["previous_run_id"]
        elif kind == "OperationPaused":
            run["pauses"].append([timestamp, None])
        elif kind == "OperationResumed":
            if run["pauses"] and run["pauses"][-1][1] is None:
                run["pauses"][-1][1] = timestamp
        elif kind == "OperationFinished":
            run["finish"] = timestamp
    for run in runs.values():
        if not run["start"] or not run["finish"]:
            run["incomplete_reason"] = "missing start or finish"
            continue
        start = datetime.fromisoformat(run["start"])
        finish = datetime.fromisoformat(run["finish"])
        elapsed = (finish - start).total_seconds()
        if elapsed < 0:
            run["incomplete_reason"] = "finish before start"
            continue
        paused = 0.0
        invalid = False
        for pause_start, pause_end in run["pauses"]:
            if pause_end is None:
                invalid = True
                break
            a, b = datetime.fromisoformat(pause_start), datetime.fromisoformat(pause_end)
            if a < start or b > finish or b < a:
                invalid = True
                break
            paused += (b - a).total_seconds()
        if invalid or paused > elapsed:
            run["incomplete_reason"] = "invalid or unfinished pause"
            continue
        run["elapsed_seconds"] = elapsed
        run["active_seconds"] = elapsed - paused
    return sorted(runs.values(), key=lambda item: (item["start"] or "", item["operation_run_id"]))


class _ProfiledConnection(sqlite3.Connection):
    timings: dict[str, list[float]]
    profile_phase: str | None

    def execute(self, sql: str, parameters=(), /):
        started = time.perf_counter()
        try:
            return super().execute(sql, parameters)
        finally:
            if self.profile_phase not in {"anchor_verify", "anchor_write"}:
                self.timings.setdefault("sqlite", []).append((time.perf_counter() - started) * 1000)


class EventStore:
    def __init__(self, db_path: str | Path, key_b64: str | KeyRing,
                 timings: dict[str, list[float]] | None = None):
        if isinstance(key_b64, KeyRing):
            self.key_ring = key_b64
        else:
            self.key_ring = KeyRing.from_single_key(key_b64)
        self.cipher = self.key_ring.get_cipher(self.key_ring.primary_key_id)
        self.timings = timings
        self.anchor_path = None if str(db_path) == ":memory:" else Path(str(db_path) + ".audit-anchor")
        self.db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None,
                                  factory=_ProfiledConnection if timings is not None else sqlite3.Connection)
        if timings is not None:
            self.db.timings = timings
            self.db.profile_phase = None
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._transaction_depth = 0
        old_table = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_events'").fetchone()
        if old_table and self.anchor_path is not None and not self.anchor_path.exists():
            if self.db.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]:
                self.db.close()
                raise ValueError("audit anchor missing for nonempty database; explicit migration required")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self._schema()
        self._memory_anchor: dict[str, Any] | None = None
        if self.anchor_path is None or not self.anchor_path.exists():
            if self.db.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]:
                raise ValueError("audit anchor missing for nonempty database; explicit migration required")
            self._write_anchor()

    def _schema(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS raw_events (
                ingestion_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                item_id TEXT,
                event_type TEXT,
                occurred_at TEXT,
                received_at TEXT NOT NULL,
                digest TEXT NOT NULL,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL,
                key_id TEXT NOT NULL DEFAULT 'k1',
                crypto_profile_id TEXT NOT NULL DEFAULT 'AES-256-GCM-v1',
                prev_hash TEXT NOT NULL DEFAULT 'GENESIS',
                block_hash TEXT NOT NULL DEFAULT '',
                hash_version INTEGER NOT NULL DEFAULT 1,
                UNIQUE(source_id, event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_raw_item ON raw_events(item_id, occurred_at);
            CREATE TABLE IF NOT EXISTS processing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ingestion_id TEXT NOT NULL REFERENCES raw_events(ingestion_id),
                state TEXT NOT NULL,
                reason TEXT,
                at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ingestion_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                existing_ingestion_id TEXT NOT NULL REFERENCES raw_events(ingestion_id),
                submitted_digest TEXT NOT NULL,
                at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                defect_type TEXT NOT NULL,
                area TEXT NOT NULL,
                first_ingestion_id TEXT NOT NULL REFERENCES raw_events(ingestion_id),
                version INTEGER NOT NULL DEFAULT 1,
                review_required INTEGER NOT NULL DEFAULT 0,
                UNIQUE(item_id, defect_type, area)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(case_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                message_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                UNIQUE(decision_id)
            );
            CREATE TABLE IF NOT EXISTS checkpoint_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                state TEXT NOT NULL,
                at TEXT NOT NULL,
                evidence_ingestion_id TEXT,
                FOREIGN KEY(evidence_ingestion_id) REFERENCES raw_events(ingestion_id)
            );
        """)
        if "hash_version" not in {row[1] for row in self.db.execute("PRAGMA table_info(raw_events)")}:
            self.db.execute("ALTER TABLE raw_events ADD COLUMN hash_version INTEGER NOT NULL DEFAULT 1")

    @staticmethod
    def _row_hash(row: dict[str, Any]) -> str:
        fields = {key: row[key] for key in (
            "ingestion_id", "source_id", "event_id", "item_id", "event_type", "occurred_at",
            "received_at", "digest", "key_id", "crypto_profile_id", "prev_hash", "hash_version"
        )}
        fields["nonce"] = base64.b64encode(row["nonce"]).decode("ascii")
        fields["ciphertext"] = base64.b64encode(row["ciphertext"]).decode("ascii")
        return hashlib.sha256(canonical(fields)).hexdigest()

    def _anchor_state(self) -> dict[str, Any]:
        row = self.db.execute("SELECT block_hash FROM raw_events ORDER BY rowid DESC LIMIT 1").fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        state = {"count": count, "head": row["block_hash"] if row else "GENESIS", "key_id": "k1"}
        key = self.key_ring.keys[state["key_id"]]
        state["mac"] = hmac.new(key, canonical(state), hashlib.sha256).hexdigest()
        return state

    def _read_anchor(self) -> dict[str, Any]:
        if self.anchor_path is None:
            return self._memory_anchor or {}
        if not self.anchor_path.exists():
            raise ValueError("audit anchor missing")
        return json.loads(self.anchor_path.read_text(encoding="utf-8"))

    def _check_anchor(self) -> None:
        expected = self._anchor_state()
        if not hmac.compare_digest(canonical(self._read_anchor()), canonical(expected)):
            raise ValueError("audit anchor mismatch: database or anchor was changed")

    def _write_anchor(self) -> None:
        state = self._anchor_state()
        if self.anchor_path is None:
            self._memory_anchor = state
            return
        temporary = self.anchor_path.with_name(self.anchor_path.name + ".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.anchor_path)

    @contextmanager
    def _profile(self, phase: str):
        if self.timings is None:
            yield
            return
        previous = self.db.profile_phase
        self.db.profile_phase = phase
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings.setdefault(phase, []).append((time.perf_counter() - started) * 1000)
            self.db.profile_phase = previous

    @contextmanager
    def transaction(self):
        with self.lock:
            if self._transaction_depth:
                savepoint = f"qc_nested_{self._transaction_depth}"
                self.db.execute(f"SAVEPOINT {savepoint}")
                self._transaction_depth += 1
                try:
                    yield
                except BaseException:
                    self.db.execute(f"ROLLBACK TO {savepoint}")
                    self.db.execute(f"RELEASE {savepoint}")
                    raise
                else:
                    self.db.execute(f"RELEASE {savepoint}")
                finally:
                    self._transaction_depth -= 1
                return
            self.db.execute("BEGIN IMMEDIATE")
            self._transaction_depth = 1
            try:
                with self._profile("anchor_verify"):
                    self._check_anchor()
                    before = self._anchor_state()
                yield
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            else:
                self.db.execute("COMMIT")
                if self._anchor_state() != before:
                    with self._profile("anchor_write"):
                        self._write_anchor()
            finally:
                self._transaction_depth = 0

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        aad = canonical({"source_id": row["source_id"], "event_id": row["event_id"]})
        key_id = row["key_id"] if "key_id" in row.keys() else "k1"
        profile_id = row["crypto_profile_id"] if "crypto_profile_id" in row.keys() else DEFAULT_PROFILE
        raw = self.key_ring.decrypt_envelope(key_id, profile_id, row["nonce"], row["ciphertext"], aad)
        if hashlib.sha256(raw).hexdigest() != row["digest"]:
            raise ValueError(f"integrity failure for {row['ingestion_id']}")
        return json.loads(raw)

    @staticmethod
    def _validate(event: dict[str, Any]) -> None:
        version = event.get("schema_version")
        if type(version) is not int or version not in (1, 2):
            raise ValueError("unsupported schema_version")
        schema_path = Path(__file__).parent.parent / "contracts" / f"event-v{version}.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        missing = set(schema["required"]) - set(event)
        if missing:
            raise ValueError(f"missing contract fields: {sorted(missing)}")
        extra = set(event) - set(schema["properties"])
        if extra and not schema.get("additionalProperties", True):
            raise ValueError(f"unexpected contract fields: {sorted(extra)}")
        for name, value in event.items():
            spec = schema["properties"].get(name, {})
            expected_type = spec.get("type")
            if expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"{name} must be string")
            if expected_type == "object" and not isinstance(value, dict):
                raise ValueError(f"{name} must be object")
            if expected_type == "integer" and type(value) is not int:
                raise ValueError(f"{name} must be integer")
            if "minLength" in spec and len(value) < spec["minLength"]:
                raise ValueError(f"{name} too short")
            if "const" in spec and value != spec["const"]:
                raise ValueError(f"{name} differs from schema const")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError(f"{name} outside schema enum")
        kind = event.get("event_type")
        if not isinstance(kind, str) or kind not in EVENT_TYPES:
            raise ValueError("unsupported event_type")
        for field in ("item_id", "source_id", "event_id"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                raise ValueError(f"{field} must be nonempty string")
        parse_time(event.get("occurred_at"))
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
        if kind == "WorkOrderReceived":
            if (not isinstance(payload.get("external_order_id"), str)
                    or not payload["external_order_id"].strip()
                    or type(payload.get("version")) is not int
                    or payload["version"] < 1
                    or payload.get("item_id") != event["item_id"]
                    or not isinstance(payload.get("route"), list)
                    or not all(isinstance(stop, str) and stop.strip() for stop in payload["route"])):
                raise ValueError("invalid work order contract")
            checkpoints = payload.get("checkpoints", [])
            if not isinstance(checkpoints, list):
                raise ValueError("checkpoints must be array")
            seen = set()
            for checkpoint in checkpoints:
                if not isinstance(checkpoint, dict):
                    raise ValueError("invalid checkpoint")
                checkpoint_id = checkpoint.get("id")
                if (not isinstance(checkpoint_id, str) or not checkpoint_id.strip()
                        or checkpoint_id in seen
                        or not isinstance(checkpoint.get("station_id"), str)
                        or not checkpoint["station_id"].strip()
                        or not isinstance(checkpoint.get("event_type"), str)
                        or checkpoint["event_type"] not in INSPECTION_TYPES):
                    raise ValueError("invalid checkpoint")
                parse_time(checkpoint.get("due_at"))
                seen.add(checkpoint_id)
        if kind in OPERATION_TYPES:
            if not isinstance(payload.get("operation_run_id"), str) or not payload["operation_run_id"].strip():
                raise ValueError("operation_run_id required")
            if kind == "ReworkStarted" and (not isinstance(payload.get("previous_run_id"), str)
                                             or not payload["previous_run_id"].strip()):
                raise ValueError("rework requires previous_run_id")
        if kind in INSPECTION_TYPES:
            evidence = payload.get("evidence_image")
            if evidence is not None:
                if not isinstance(evidence, dict) or set(evidence) != {"mime_type", "data_base64"}:
                    raise ValueError("evidence_image must contain mime_type and data_base64")
                mime = evidence["mime_type"]
                encoded = evidence["data_base64"]
                if mime not in ("image/jpeg", "image/png") or not isinstance(encoded, str) or len(encoded) > 350000:
                    raise ValueError("unsupported or oversized evidence image")
                try:
                    image = base64.b64decode(encoded, validate=True)
                except (ValueError, base64.binascii.Error) as exc:
                    raise ValueError("invalid evidence image encoding") from exc
                if not 0 < len(image) <= 256 * 1024 or not image.startswith(
                    b"\xff\xd8\xff" if mime == "image/jpeg" else b"\x89PNG\r\n\x1a\n"
                ):
                    raise ValueError("invalid evidence image content")
            if payload.get("inspection_result") not in RESULTS:
                raise ValueError("invalid inspection_result")
            if payload.get("observation_quality", "unknown") not in QUALITIES:
                raise ValueError("invalid observation_quality")
            defects = payload.get("defects", [])
            if not isinstance(defects, list):
                raise ValueError("defects must be array")
            if payload["inspection_result"] == "signs_detected" and not defects:
                raise ValueError("signs_detected requires defects")
            if payload["inspection_result"] != "signs_detected" and defects:
                raise ValueError("defects require signs_detected")
            for defect in defects:
                if not isinstance(defect, dict) or not isinstance(defect.get("type"), str) or not defect["type"].strip():
                    raise ValueError("each defect requires type")
                if "area" in defect and not isinstance(defect["area"], str):
                    raise ValueError("defect area must be string")

    def ingest(self, event: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise ValueError("event must be object")
        for field in ("source_id", "event_id"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                raise ValueError(f"{field} is required for journal identity")
        raw = canonical(event)
        digest = hashlib.sha256(raw).hexdigest()
        with self.transaction():
            old = self.db.execute(
                "SELECT ingestion_id,digest FROM raw_events WHERE source_id=? AND event_id=?",
                (event["source_id"], event["event_id"]),
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    self.db.execute(
                        "INSERT INTO ingestion_conflicts(source_id,event_id,existing_ingestion_id,submitted_digest,at) "
                        "VALUES (?,?,?,?,?)",
                        (event["source_id"], event["event_id"], old["ingestion_id"], digest, utc_now()),
                    )
                    return {"ingestion_id": old["ingestion_id"], "state": "conflict",
                            "reason": "same source_id/event_id, different payload"}
                return {"ingestion_id": old["ingestion_id"], "state": "duplicate"}
            ingestion_id = str(uuid.uuid4())
            aad = canonical({"source_id": event["source_id"], "event_id": event["event_id"]})
            with self._profile("crypto"):
                envelope = self.key_ring.encrypt_envelope(raw, aad, profile_id=profile_id)
            nonce = envelope["nonce"]
            ciphertext = envelope["ciphertext"]
            key_id = envelope["key_id"]
            crypto_profile_id = envelope["crypto_profile_id"]
            try:
                self._validate(event)
                state, reason = "applied", None
                occurred_at = parse_time(event["occurred_at"])
            except ValueError as exc:
                state, reason, occurred_at = "quarantined", str(exc), None
            last_row = self.db.execute("SELECT block_hash FROM raw_events ORDER BY rowid DESC LIMIT 1").fetchone()
            prev_hash = last_row["block_hash"] if last_row and last_row["block_hash"] else "GENESIS"
            received_at = utc_now()
            row_data = {"ingestion_id": ingestion_id, "source_id": event["source_id"],
                        "event_id": event["event_id"],
                        "item_id": event.get("item_id") if isinstance(event.get("item_id"), str) else None,
                        "event_type": event.get("event_type") if isinstance(event.get("event_type"), str) else None,
                        "occurred_at": occurred_at, "received_at": received_at, "digest": digest,
                        "nonce": nonce, "ciphertext": ciphertext, "key_id": key_id,
                        "crypto_profile_id": crypto_profile_id, "prev_hash": prev_hash, "hash_version": 2}
            block_hash = self._row_hash(row_data)

            self.db.execute(
                "INSERT INTO raw_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ingestion_id, event["source_id"], event["event_id"],
                 row_data["item_id"], row_data["event_type"],
                 occurred_at, received_at, digest, nonce, ciphertext, key_id, crypto_profile_id,
                 prev_hash, block_hash, 2),
            )
            self.db.execute(
                "INSERT INTO processing_events(ingestion_id,state,reason,at) VALUES (?,?,?,?)",
                (ingestion_id, state, reason, utc_now()),
            )
            if state == "applied" and event["event_type"] in INSPECTION_TYPES:
                payload = event["payload"]
                if payload["inspection_result"] == "signs_detected" and payload.get("observation_quality", "unknown") == "good":
                    for defect in payload["defects"]:
                        area = defect.get("area") or "unspecified"
                        existing = self.db.execute(
                            "SELECT case_id FROM cases WHERE item_id=? AND defect_type=? AND area=?",
                            (event["item_id"], defect["type"], area),
                        ).fetchone()
                        if not existing:
                            self.db.execute(
                                "INSERT INTO cases(case_id,item_id,defect_type,area,first_ingestion_id) VALUES (?,?,?,?,?)",
                                (str(uuid.uuid4()), event["item_id"], defect["type"], area, ingestion_id),
                            )
            if state == "applied":
                self.db.execute(
                    "UPDATE cases SET review_required=1 WHERE item_id=? AND case_id IN "
                    "(SELECT case_id FROM decisions) AND ? < (SELECT MAX(at) FROM decisions "
                    "WHERE decisions.case_id=cases.case_id)",
                    (event["item_id"], occurred_at),
                )
            return {"ingestion_id": ingestion_id, "state": state, "reason": reason}

    def ingest_many(self, events: list[dict[str, Any]], profile_id: str | None = None) -> list[dict[str, Any]]:
        """Commit a batch atomically; verify once and advance the anchor at most once."""
        if not isinstance(events, list):
            raise ValueError("events must be a list")
        if not events:
            return []
        with self.transaction():
            return [self.ingest(event, profile_id=profile_id) for event in events]

    def history(self, item_id: str) -> dict[str, Any]:
        with self.lock:
            rows = self.db.execute(
                "SELECT r.*,p.state,p.reason FROM raw_events r JOIN processing_events p "
                "ON p.ingestion_id=r.ingestion_id WHERE r.item_id=? ORDER BY r.occurred_at,r.event_id",
                (item_id,),
            ).fetchall()
            events = []
            for row in rows:
                event = self._decode(row)
                events.append({"ingestion_id": row["ingestion_id"], "state": row["state"],
                               "reason": row["reason"], "received_at": row["received_at"],
                               "event": event})
            return {"item_id": item_id, "events": events, "operation_runs": project_runs(events),
                    "checkpoints": self.checkpoints(item_id),
                    "cases": [self._case(r) for r in self.db.execute("SELECT * FROM cases WHERE item_id=?", (item_id,))]}

    def checkpoints(self, item_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
        cutoff = parse_time(as_of) if as_of else utc_now()
        with self.lock:
            order_rows = self.db.execute(
                "SELECT r.* FROM raw_events r JOIN processing_events p ON p.ingestion_id=r.ingestion_id "
                "WHERE r.item_id=? AND r.event_type='WorkOrderReceived' AND p.state='applied' "
                "ORDER BY r.occurred_at DESC,r.event_id DESC",
                (item_id,),
            ).fetchall()
            if not order_rows:
                return []
            expected = self._decode(order_rows[0])["payload"].get("checkpoints", [])
            observed_rows = self.db.execute(
                "SELECT r.* FROM raw_events r JOIN processing_events p ON p.ingestion_id=r.ingestion_id "
                "WHERE r.item_id=? AND p.state='applied' AND r.event_type IN "
                "('InspectionReported','IncomingInspectionCompleted')",
                (item_id,),
            ).fetchall()
            observations = [(row, self._decode(row)) for row in observed_rows]
            result = []
            for checkpoint in expected:
                valid = [(r, e) for r, e in observations
                         if e["event_type"] == checkpoint["event_type"]
                         and e.get("station_id") == checkpoint["station_id"]
                         and e["payload"]["inspection_result"] != "unable_to_assess"
                         and e["payload"].get("observation_quality", "unknown") == "good"]
                evidence = min(valid, key=lambda pair: (pair[0]["occurred_at"], pair[0]["event_id"]))[0] if valid else None
                state = "observed" if evidence else "missing" if parse_time(checkpoint["due_at"]) < cutoff else "awaiting"
                alerts = [dict(row) for row in self.db.execute(
                    "SELECT state,at,evidence_ingestion_id FROM checkpoint_alerts "
                    "WHERE item_id=? AND checkpoint_id=? ORDER BY id",
                    (item_id, checkpoint["id"]),
                )]
                result.append({"id": checkpoint["id"], "station_id": checkpoint["station_id"],
                               "event_type": checkpoint["event_type"], "due_at": checkpoint["due_at"],
                               "state": state, "evidence_ingestion_id": evidence["ingestion_id"] if evidence else None,
                               "alert_history": alerts})
            return result

    def scan_checkpoints(self, as_of: str | None = None) -> list[dict[str, Any]]:
        cutoff = parse_time(as_of) if as_of else utc_now()
        changes = []
        with self.transaction():
            for item in self.items():
                for checkpoint in self.checkpoints(item["item_id"], cutoff):
                    previous = checkpoint["alert_history"][-1]["state"] if checkpoint["alert_history"] else None
                    new = "missing" if checkpoint["state"] == "missing" else "resolved" if checkpoint["state"] == "observed" and previous == "missing" else None
                    if new and new != previous:
                        self.db.execute(
                            "INSERT INTO checkpoint_alerts(item_id,checkpoint_id,state,at,evidence_ingestion_id) "
                            "VALUES (?,?,?,?,?)",
                            (item["item_id"], checkpoint["id"], new, utc_now(), checkpoint["evidence_ingestion_id"]),
                        )
                        changes.append({"item_id": item["item_id"], "checkpoint_id": checkpoint["id"], "state": new})
        return changes

    def _case(self, row: sqlite3.Row) -> dict[str, Any]:
        decisions = [dict(r) for r in self.db.execute(
            "SELECT * FROM decisions WHERE case_id=? ORDER BY rowid", (row["case_id"],)
        )]
        evidence_rows = self.db.execute(
            "SELECT r.* FROM raw_events r JOIN processing_events p ON p.ingestion_id=r.ingestion_id "
            "WHERE r.item_id=? AND p.state='applied' ORDER BY r.occurred_at,r.event_id",
            (row["item_id"],),
        ).fetchall()
        evidence_events = [(r, self._decode(r)) for r in evidence_rows]
        matching = [(r, e) for r, e in evidence_events if e["event_type"] in INSPECTION_TYPES
                    and any(d["type"] == row["defect_type"] and (d.get("area") or "unspecified") == row["area"]
                            for d in e["payload"].get("defects", []))]
        detection_row, detection = matching[0]
        before = [(r, e) for r, e in evidence_events if r["occurred_at"] < detection_row["occurred_at"]
                  and e["event_type"] in INSPECTION_TYPES
                  and e["payload"]["inspection_result"] == "no_signs_detected"
                  and e["payload"].get("observation_quality", "unknown") == "good"]
        last_good = before[-1][0] if before else None
        operation_starts = [r for r, e in evidence_events if r["occurred_at"] < detection_row["occurred_at"]
                            and e["event_type"] in {"OperationStarted", "ReworkStarted"}]
        last_start = operation_starts[-1] if operation_starts else None
        window = max([r["occurred_at"] for r in (last_good, last_start) if r], default=None)
        nearby = [(r, e) for r, e in evidence_events if r["occurred_at"] <= detection_row["occurred_at"]
                  and (window is None or r["occurred_at"] >= window)]
        context = {
            "classification": "incoming_signal" if detection["event_type"] == "IncomingInspectionCompleted"
            else "new_after_last_good_observation" if last_good else "time_of_occurrence_unknown",
            "detection_ingestion_id": detection_row["ingestion_id"],
            "last_good_ingestion_id": last_good["ingestion_id"] if last_good else None,
            "operation_start_ingestion_id": last_start["ingestion_id"] if last_start else None,
            "machine_ingestion_ids": [r["ingestion_id"] for r, e in nearby
                                      if e["event_type"] in {"MachineWarning", "MachineStopped"}],
            "operator_ingestion_ids": [r["ingestion_id"] for r, e in nearby
                                       if e["event_type"] == "OperatorActionObserved"],
        }
        return {"case_id": row["case_id"], "item_id": row["item_id"],
                "defect_type": row["defect_type"], "area": row["area"],
                "first_ingestion_id": row["first_ingestion_id"], "version": row["version"],
                "review_required": bool(row["review_required"]),
                "status": decisions[-1]["action"] if decisions else "awaiting_review",
                "cause_status": "unknown", "context": context, "decisions": decisions}

    def cases(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self._case(r) for r in self.db.execute("SELECT * FROM cases ORDER BY item_id,case_id")]

    def items(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT item_id,COUNT(*) AS event_count,MAX(received_at) AS last_received_at "
                                   "FROM raw_events WHERE item_id IS NOT NULL GROUP BY item_id ORDER BY item_id").fetchall()
            return [{**dict(row), "case_count": self.db.execute(
                "SELECT COUNT(*) FROM cases WHERE item_id=?", (row["item_id"],)).fetchone()[0]}
                    for row in rows]

    def line_overview(self) -> list[dict[str, Any]]:
        with self.lock:
            result = []
            for item in self.items():
                history = self.history(item["item_id"])
                applied = [record["event"] for record in history["events"] if record["state"] == "applied"]
                inspections = [event for event in applied if event["event_type"] in INSPECTION_TYPES]
                latest = inspections[-1] if inspections else None
                confirmed = any(case["status"] == "confirmed" for case in history["cases"])
                awaiting = any(case["review_required"] or case["status"] in {"awaiting_review", "needs_more_inspection"}
                               for case in history["cases"])
                missing = any(point["state"] == "missing" for point in history["checkpoints"])
                if missing:
                    state = "missing_control"
                elif awaiting:
                    state = "review_required"
                elif confirmed:
                    state = "confirmed_nonconformance"
                elif not latest or latest["payload"].get("observation_quality", "unknown") != "good" or latest["payload"]["inspection_result"] == "unable_to_assess":
                    state = "insufficient_observation"
                else:
                    state = "no_confirmed_nonconformance"
                stations = [event.get("station_id") for event in applied if event.get("station_id")]
                result.append({"item_id": item["item_id"], "state": state,
                               "station_id": stations[-1] if stations else None,
                               "inspection_result": latest["payload"]["inspection_result"] if latest else None,
                               "case_count": len(history["cases"]),
                               "missing_checkpoints": sum(point["state"] == "missing" for point in history["checkpoints"])})
            return result

    def decide(self, case_id: str, action: str, actor: str, reason: str,
               expected_version: int, idempotency_key: str) -> dict[str, Any]:
        if action not in DECISIONS:
            raise ValueError("invalid decision")
        if not actor.strip() or not reason.strip() or not idempotency_key.strip():
            raise ValueError("actor, reason and idempotency_key are required")
        with self.transaction():
            row = self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if not row:
                raise KeyError("case not found")
            old = self.db.execute("SELECT * FROM decisions WHERE case_id=? AND idempotency_key=?",
                                  (case_id, idempotency_key)).fetchone()
            if old:
                if old["action"] != action or old["actor"] != actor or old["reason"] != reason:
                    raise ValueError("decision idempotency conflict")
                return self._case(row)
            if row["version"] != expected_version:
                raise ValueError("stale case version")
            decision_id = str(uuid.uuid4())
            at = utc_now()
            self.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                            (decision_id, case_id, action, actor, reason, at, idempotency_key))
            self.db.execute("UPDATE cases SET version=version+1,review_required=0 WHERE case_id=?", (case_id,))
            payload = {"message_id": str(uuid.uuid4()), "schema_version": 1,
                       "item_id": row["item_id"], "case_id": case_id,
                       "decision_id": decision_id, "status": action, "at": at}
            work_orders = self.db.execute(
                "SELECT * FROM raw_events WHERE item_id=? AND event_type='WorkOrderReceived' ORDER BY occurred_at DESC",
                (row["item_id"],),
            ).fetchall()
            if work_orders:
                order = self._decode(work_orders[0])["payload"]
                payload["external_order_id"] = order.get("external_order_id")
                payload["external_order_version"] = order.get("version")
            self.db.execute("INSERT INTO outbox(message_id,case_id,decision_id,payload) VALUES (?,?,?,?)",
                            (payload["message_id"], case_id, decision_id, json.dumps(payload)))
            return self._case(self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone())

    def metrics(self) -> dict[str, Any]:
        with self.lock:
            rows = self.db.execute("SELECT r.* FROM raw_events r JOIN processing_events p "
                                   "ON p.ingestion_id=r.ingestion_id WHERE p.state='applied'").fetchall()
            checked, unable = set(), set()
            defect_evidence: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
            for row in rows:
                event = self._decode(row)
                if event["event_type"] not in INSPECTION_TYPES:
                    continue
                result = event["payload"]["inspection_result"]
                quality = event["payload"].get("observation_quality", "unknown")
                if result == "unable_to_assess" or quality != "good":
                    unable.add(event["item_id"])
                else:
                    checked.add(event["item_id"])
                if quality != "good":
                    continue
                for defect in event["payload"].get("defects", []):
                    key = (event["item_id"], defect["type"], defect.get("area") or "unspecified")
                    defect_evidence.setdefault(key, []).append((row["occurred_at"], event["event_type"]))
            confirmed = [c for c in self.cases() if c["status"] == "confirmed"]
            incoming = sum(
                min(defect_evidence[(case["item_id"], case["defect_type"], case["area"])])[1]
                == "IncomingInspectionCompleted" for case in confirmed
            )
            runs = [run for item in self.items() for run in self.history(item["item_id"])["operation_runs"]]
            return {"checked_items": len(checked), "items_without_usable_inspection": len(unable - checked),
                    "items_with_confirmed_defect": len({c["item_id"] for c in confirmed}),
                    "confirmed_defect_cases": len(confirmed), "confirmed_incoming_cases": incoming,
                    "confirmed_post_operation_cases": len(confirmed) - incoming,
                    "pending_cases": sum(c["status"] == "awaiting_review" for c in self.cases()),
                    "completed_operation_runs": sum(run["active_seconds"] is not None for run in runs),
                    "rework_runs": sum(bool(run["previous_run_id"]) for run in runs),
                    "rule_version": 1}

    def outbox(self) -> list[dict[str, Any]]:
        with self.lock:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in
                    self.db.execute("SELECT * FROM outbox ORDER BY rowid")]

    def record_delivery(self, message_id: str, acknowledged: bool, error: str | None = None) -> None:
        with self.transaction():
            changed = self.db.execute(
                "UPDATE outbox SET state=?,attempts=attempts+1,last_error=? WHERE message_id=? AND state!='acknowledged'",
                ("acknowledged" if acknowledged else "error", error, message_id),
            ).rowcount
            if changed != 1:
                raise ValueError("outbox message absent or already acknowledged")

    def verify_integrity(self) -> dict[str, Any]:
        with self.lock:
            self._check_anchor()
            rows = self.db.execute("SELECT * FROM raw_events ORDER BY rowid ASC").fetchall()
            expected_prev = "GENESIS"
            for row in rows:
                self._decode(row)
                if "block_hash" in row.keys() and row["block_hash"]:
                    if row["prev_hash"] != expected_prev:
                        raise ValueError(f"hash chain broken at {row['ingestion_id']}: expected prev {expected_prev}, got {row['prev_hash']}")
                    if row["hash_version"] == 2:
                        actual_hash = self._row_hash(dict(row))
                    elif row["hash_version"] == 1:
                        block_data = f"{expected_prev}:{row['ingestion_id']}:{row['source_id']}:{row['event_id']}:{row['digest']}"
                        actual_hash = hashlib.sha256(block_data.encode("utf-8")).hexdigest()
                    else:
                        raise ValueError(f"unsupported hash version {row['hash_version']}")
                    if actual_hash != row["block_hash"]:
                        raise ValueError(f"block hash mismatch at {row['ingestion_id']}")
                    expected_prev = actual_hash
            if expected_prev != self._anchor_state()["head"]:
                raise ValueError("audit head mismatch")
            return {"checked_events": len(rows), "valid": True, "hash_chain_verified": True}

    def close(self) -> None:
        self.db.close()
