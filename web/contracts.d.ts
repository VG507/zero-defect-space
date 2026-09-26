/**
 * AUTO-GENERATED from contracts/event-v1.schema.json and event-v2.schema.json.
 * DO NOT EDIT MANUALLY. Run: python scripts/generate_contracts.py
 */

export type EventType = "WorkOrderReceived" | "ItemReceived" | "IncomingInspectionCompleted" | "OperationStarted" | "OperationPaused" | "OperationResumed" | "OperationFinished" | "ReworkStarted" | "InspectionReported" | "OperatorActionObserved" | "MachineStateChanged" | "MachineWarning" | "MachineStopped" | "AssemblyImported";

export type InspectionResult = "signs_detected" | "no_signs_detected" | "unable_to_assess";
export type ObservationQuality = "good" | "poor" | "unknown";
export type DecisionAction = "confirmed" | "rejected" | "needs_more_inspection";
export type IngestionState = "applied" | "duplicate" | "quarantined" | "conflict";

export interface DefectObservation {
  type: string;
  area?: string;
}

export interface InspectionPayload {
  inspection_result: InspectionResult;
  observation_quality?: ObservationQuality;
  defects?: DefectObservation[];
}

export interface IngestionEventV1 {
  schema_version: 1;
  source_id: string;
  event_id: string;
  item_id: string;
  event_type: EventType;
  occurred_at: string;
  station_id?: string;
  operator_alias?: string;
  payload: Record<string, unknown>;
}

export interface IngestionEventV2 {
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
}

export interface ControllerDecisionRequest {
  action: DecisionAction;
  actor: string;
  reason: string;
  expected_version: number;
  idempotency_key: string;
}

export interface IngestionResponse {
  ingestion_id: string;
  state: IngestionState;
  reason?: string | null;
}
