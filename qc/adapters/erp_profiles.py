"""Data mapping and payload converters for 1C:ERP, Galaktika:ERP, MES, and KOMPAS-3D."""

from __future__ import annotations

from typing import Any


class ErpProfileMapper:
    """Translates external ERP and CAD documents into Zero Defect Space events."""

    @staticmethod
    def from_1c_order(doc_1c: dict[str, Any], occurred_at: str) -> dict[str, Any]:
        """Maps 1C:ERP ProductionOrder into WorkOrderReceived event."""
        checkpoints = []
        for cp in doc_1c.get("checkpoints", []):
            checkpoints.append({
                "id": cp["checkpoint_id"],
                "station_id": cp["station"],
                "event_type": cp["type"],
                "due_at": cp["due"],
            })

        return {
            "schema_version": 1,
            "source_id": "1c-erp-bridge",
            "event_id": f"1c-wo-{doc_1c['doc_number']}",
            "item_id": doc_1c["item_code"],
            "event_type": "WorkOrderReceived",
            "occurred_at": occurred_at,
            "payload": {
                "external_order_id": doc_1c["doc_number"],
                "version": doc_1c.get("version", 1),
                "item_id": doc_1c["item_code"],
                "route": doc_1c.get("route_points", []),
                "checkpoints": checkpoints,
            },
        }

    @staticmethod
    def from_kompas_bom(bom: dict[str, Any], item_id: str, occurred_at: str) -> dict[str, Any]:
        """Maps KOMPAS-3D bill of materials into AssemblyImported event."""
        return {
            "schema_version": 1,
            "source_id": "kompas-3d-pdm",
            "event_id": f"kompas-{bom['assembly_id']}-v{bom.get('assembly_version', '1')}",
            "item_id": item_id,
            "event_type": "AssemblyImported",
            "occurred_at": occurred_at,
            "payload": {
                "assembly_id": bom["assembly_id"],
                "version": bom.get("assembly_version", "1"),
                "components": bom.get("components", []),
                "geometry_attached": bool(bom.get("geometry_attached", False)),
            },
        }
