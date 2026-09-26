"""AUTO-GENERATED from contracts/event-v1.schema.json and event-v2.schema.json.
DO NOT EDIT MANUALLY. Run: python scripts/generate_contracts.py
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

EventType = Literal[
    "WorkOrderReceived",
    "ItemReceived",
    "IncomingInspectionCompleted",
    "OperationStarted",
    "OperationPaused",
    "OperationResumed",
    "OperationFinished",
    "ReworkStarted",
    "InspectionReported",
    "OperatorActionObserved",
    "MachineStateChanged",
    "MachineWarning",
    "MachineStopped",
    "AssemblyImported"
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
