import base64
import os
import tempfile
import unittest

from qc.adapters.opc_ua import OpcUaMachineAdapter
from qc.core import EventStore


class OpcUaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "opc_test.db")
        self.key_b64 = base64.b64encode(os.urandom(32)).decode()
        self.store = EventStore(self.db_path, self.key_b64)
        self.adapter = OpcUaMachineAdapter(machine_id="CNC-MILL-04", line_id="L-1", station_id="ST-MILL")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_opc_ua_telemetry_ingested_without_core_modification(self):
        # 1. Translate cycle start
        start_event = self.adapter.translate_cycle_start(
            cycle_id="9021",
            timestamp_iso="2026-09-25T10:00:00+03:00",
            item_id="ITEM-AEROSPACE-99",
            operator_code="op-ivanov",
        )
        res_start = self.store.ingest(start_event)
        self.assertEqual(res_start["state"], "applied")

        # 2. Translate spindle vibration alarm during operation
        alarm_event = self.adapter.translate_spindle_warning(
            node_id="ns=2;s=Spindle.VibrationRMS",
            timestamp_iso="2026-09-25T10:02:15+03:00",
            vibration_rms=14.8,
            threshold=10.0,
            item_id="ITEM-AEROSPACE-99",
            operation_run_id="cnc-run-9021",
        )
        res_alarm = self.store.ingest(alarm_event)
        self.assertEqual(res_alarm["state"], "applied")

        # 3. Verify history in core reflects new source correctly
        hist = self.store.history("ITEM-AEROSPACE-99")
        self.assertEqual(len(hist["events"]), 2)
        sources = {ev["event"]["source_id"] for ev in hist["events"]}
        self.assertIn("opc-ua:CNC-MILL-04", sources)

        # 4. Decrypt and verify payload
        alarm_in_hist = [ev for ev in hist["events"] if ev["event"]["event_type"] == "MachineWarning"][0]
        self.assertEqual(alarm_in_hist["event"]["payload"]["code"], "WARN_EXCESS_VIBRATION")
        self.assertIn("14.80", alarm_in_hist["event"]["payload"]["detail"])


if __name__ == "__main__":
    unittest.main()
