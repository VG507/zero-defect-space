"""AUTO-GENERATED from contracts/event-v1.schema.json and event-v2.schema.json.
DO NOT EDIT MANUALLY. Run: python scripts/generate_contracts.py
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Required, TypedDict

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
    "AssemblyImported",
    "ComponentInstalled",
    "ComponentRemoved"
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
    schema_version: Required[Literal[1]]
    source_id: Required[str]
    event_id: Required[str]
    item_id: Required[str]
    event_type: Required[EventType]
    occurred_at: Required[str]
    station_id: NotRequired[str]
    operator_alias: NotRequired[str]
    payload: Required[dict[str, Any]]


class IngestionEventV2(TypedDict, total=False):
    schema_version: Required[Literal[2]]
    source_id: Required[str]
    event_id: Required[str]
    item_id: Required[str]
    event_type: Required[EventType]
    occurred_at: Required[str]
    station_id: NotRequired[str]
    operator_alias: NotRequired[str]
    line_id: NotRequired[str]
    shift_id: NotRequired[str]
    source_firmware_version: NotRequired[str]
    device_telemetry: NotRequired[dict[str, Any]]
    payload: Required[dict[str, Any]]


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
