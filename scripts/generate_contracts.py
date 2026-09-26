"""Code generator from JSON schema / OpenAPI contracts into Python and TypeScript types.
Reproducible, single command, no heavy external dependencies required.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = ROOT / "contracts"
PY_OUT = ROOT / "qc" / "contracts.py"
TS_OUT = ROOT / "web" / "contracts.d.ts"


def generate_python() -> str:
    schema_v1 = json.loads((CONTRACTS_DIR / "event-v1.schema.json").read_text(encoding="utf-8"))
    schema_v2 = json.loads((CONTRACTS_DIR / "event-v2.schema.json").read_text(encoding="utf-8"))

    event_types = schema_v1["properties"]["event_type"]["enum"]
    event_types_repr = ",\n    ".join(f'"{t}"' for t in event_types)

    content = f'''"""AUTO-GENERATED from contracts/event-v1.schema.json and event-v2.schema.json.
DO NOT EDIT MANUALLY. Run: python scripts/generate_contracts.py
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

EventType = Literal[
    {event_types_repr}
]

InspectionResult = Literal["signs_detected", "no_signs_detected", "unable_to_assess"]
ObservationQuality = Literal["good", "poor", "unknown"]
DecisionAction = Literal["confirmed", "rejected", "needs_more_inspection"]
IngestionState = Literal["applied", "duplicate", "quarantined", "conflict"]


class DefectObservation(TypedDict, total=False):
    type: str
    area: str


class InspectionPayload(TypedDict, total=False):
    inspection_result: InspectionResult
    observation_quality: ObservationQuality
    defects: list[DefectObservation]


class IngestionEventV1(TypedDict, total=False):
    schema_version: Literal[1]
    source_id: str
    event_id: str
    item_id: str
    event_type: EventType
    occurred_at: str
    station_id: str
    operator_alias: str
    payload: dict[str, Any]


class IngestionEventV2(TypedDict, total=False):
    schema_version: Literal[2]
    source_id: str
    event_id: str
    item_id: str
    event_type: EventType
    occurred_at: str
    station_id: str
    operator_alias: str
    source_firmware_version: str
    device_telemetry: dict[str, Any]
    payload: dict[str, Any]


class ControllerDecisionRequest(TypedDict):
    action: DecisionAction
    actor: str
    reason: str
    expected_version: int
    idempotency_key: str


class IngestionResponse(TypedDict, total=False):
    ingestion_id: str
    state: IngestionState
    reason: str | None
'''
    return content


def generate_typescript() -> str:
    schema_v1 = json.loads((CONTRACTS_DIR / "event-v1.schema.json").read_text(encoding="utf-8"))
    event_types = schema_v1["properties"]["event_type"]["enum"]
    event_types_ts = " | ".join(f'"{t}"' for t in event_types)

    content = f'''/**
 * AUTO-GENERATED from contracts/event-v1.schema.json and event-v2.schema.json.
 * DO NOT EDIT MANUALLY. Run: python scripts/generate_contracts.py
 */

export type EventType = {event_types_ts};

export type InspectionResult = "signs_detected" | "no_signs_detected" | "unable_to_assess";
export type ObservationQuality = "good" | "poor" | "unknown";
export type DecisionAction = "confirmed" | "rejected" | "needs_more_inspection";
export type IngestionState = "applied" | "duplicate" | "quarantined" | "conflict";

export interface DefectObservation {{
  type: string;
  area?: string;
}}

export interface InspectionPayload {{
  inspection_result: InspectionResult;
  observation_quality?: ObservationQuality;
  defects?: DefectObservation[];
}}

export interface IngestionEventV1 {{
  schema_version: 1;
  source_id: string;
  event_id: string;
  item_id: string;
  event_type: EventType;
  occurred_at: string;
  station_id?: string;
  operator_alias?: string;
  payload: Record<string, unknown>;
}}

export interface IngestionEventV2 {{
  schema_version: 2;
  source_id: string;
  event_id: string;
  item_id: string;
  event_type: EventType;
  occurred_at: string;
  station_id?: string;
  operator_alias?: string;
  source_firmware_version?: string;
  device_telemetry?: Record<string, unknown>;
  payload: Record<string, unknown>;
}}

export interface ControllerDecisionRequest {{
  action: DecisionAction;
  actor: string;
  reason: string;
  expected_version: number;
  idempotency_key: string;
}}

export interface IngestionResponse {{
  ingestion_id: string;
  state: IngestionState;
  reason?: string | null;
}}
'''
    return content


def main() -> None:
    py_code = generate_python()
    ts_code = generate_typescript()
    PY_OUT.write_text(py_code, encoding="utf-8")
    TS_OUT.write_text(ts_code, encoding="utf-8")
    print(f"Generated Python contracts -> {PY_OUT.relative_to(ROOT)}")
    print(f"Generated TypeScript contracts -> {TS_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
