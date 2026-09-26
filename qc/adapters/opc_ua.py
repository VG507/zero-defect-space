"""Industrial CNC / OPC-UA telemetry adapter for Zero Defect Space.
Translates proprietary machine controller signals and vibration/spindle anomalies
into canonical MachineWarning and OperationStarted events.

Demonstrates adding a second external source adapter without modifying qc/core.py.
"""

from __future__ import annotations

from typing import Any


class OpcUaMachineAdapter:
    """Adapts raw OPC-UA Node variables into Zero Defect Space Event V1 envelope."""

    def __init__(self, machine_id: str, line_id: str, station_id: str):
        self.machine_id = machine_id
        self.line_id = line_id
        self.station_id = station_id

    def translate_spindle_warning(
        self,
        node_id: str,
        timestamp_iso: str,
        vibration_rms: float,
        threshold: float,
        item_id: str,
        operation_run_id: str,
    ) -> dict[str, Any]:
        """Maps OPC-UA threshold breach node into standard MachineWarning event."""
        return {
            "schema_version": 1,
            "source_id": f"opc-ua:{self.machine_id}",
            "event_id": f"opc-alarm-{node_id}-{timestamp_iso}",
            "item_id": item_id,
            "event_type": "MachineWarning",
            "occurred_at": timestamp_iso,
            "station_id": self.station_id,
            "payload": {
                "operation_run_id": operation_run_id,
                "machine_id": self.machine_id,
                "code": "WARN_EXCESS_VIBRATION",
                "detail": f"Spindle vibration RMS={vibration_rms:.2f} exceeded limit {threshold:.2f} mm/s",
                "sensor_node": node_id,
            },
        }

    def translate_cycle_start(
        self,
        cycle_id: str,
        timestamp_iso: str,
        item_id: str,
        operator_code: str,
    ) -> dict[str, Any]:
        """Maps CNC cycle start bit into standard OperationStarted event."""
        return {
            "schema_version": 1,
            "source_id": f"opc-ua:{self.machine_id}",
            "event_id": f"opc-cycle-{cycle_id}",
            "item_id": item_id,
            "event_type": "OperationStarted",
            "occurred_at": timestamp_iso,
            "station_id": self.station_id,
            "operator_alias": operator_code,
            "payload": {
                "operation_run_id": f"cnc-run-{cycle_id}",
                "operation": "milling",
            },
        }
