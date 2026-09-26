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


def _python_fields(schema: dict) -> str:
    required = set(schema["required"])
    lines = []
    for name, spec in schema["properties"].items():
        if "const" in spec:
            kind = f"Literal[{spec['const']}]"
        elif name == "event_type":
            kind = "EventType"
        else:
            kind = {"string": "str", "integer": "int", "object": "dict[str, Any]"}[spec["type"]]
        lines.append(f"    {name}: {'Required' if name in required else 'NotRequired'}[{kind}]")
    return "\n".join(lines)


def _typescript_fields(schema: dict) -> str:
    required = set(schema["required"])
    lines = []
    for name, spec in schema["properties"].items():
        if "const" in spec:
            kind = str(spec["const"])
        elif name == "event_type":
            kind = "EventType"
        else:
            kind = {"string": "string", "integer": "number", "object": "Record<string, unknown>"}[spec["type"]]
        lines.append(f"  {name}{'' if name in required else '?'}: {kind};")
    return "\n".join(lines)


def generate_python() -> str:
    schema_v1 = json.loads((CONTRACTS_DIR / "event-v1.schema.json").read_text(encoding="utf-8"))
    schema_v2 = json.loads((CONTRACTS_DIR / "event-v2.schema.json").read_text(encoding="utf-8"))

    event_types = schema_v1["properties"]["event_type"]["enum"]
    event_types_repr = ",\n    ".join(f'"{t}"' for t in event_types)

    fields_v1, fields_v2 = _python_fields(schema_v1), _python_fields(schema_v2)
    content = f'''"""AUTO-GENERATED from contracts/event-v1.schema.json and event-v2.schema.json.
DO NOT EDIT MANUALLY. Run: python scripts/generate_contracts.py
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Required, TypedDict

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
{fields_v1}


class IngestionEventV2(TypedDict, total=False):
{fields_v2}


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
    schema_v2 = json.loads((CONTRACTS_DIR / "event-v2.schema.json").read_text(encoding="utf-8"))
    event_types = schema_v1["properties"]["event_type"]["enum"]
    event_types_ts = " | ".join(f'"{t}"' for t in event_types)

    fields_v1, fields_v2 = _typescript_fields(schema_v1), _typescript_fields(schema_v2)
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
{fields_v1}
}}

export interface IngestionEventV2 {{
{fields_v2}
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
