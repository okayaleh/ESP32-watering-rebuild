import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("src"))
from settings_store import SettingsStore, defaults, migrate, validate, clone
from persistence import atomic_json, read_json
from application import Application
from controller import Controller
from state import State
from valve import Valve

class Pin:
    OUT = 1
    def __init__(self, *args, **kwargs): pass
    def value(self, value): pass

class SettingsStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = SimpleNamespace(**{k: v for k, v in runpy.run_path("src/config.example.py").items() if k.isupper()})
        self.path = str(Path(self.temp.name) / "settings.json")
        self.store = SettingsStore(self.config, self.path)
        self.state = State(self.temp.name)
        self.controller = Controller(self.store.data, {v["name"]: Valve(v, Pin) for v in self.config.VALVES}, self.config, ledger_path=str(Path(self.temp.name) / "water.json"))
        self.app = Application(self.config, self.store, self.controller, self.state)
    def test_legacy_single_schedule_valve_adc_and_ui_only_zone(self):
        old = {"daily_hour": 8, "daily_minute": 12, "daily_duration_sec": 90,
               "hardware": {"valve_pin": 26, "valve_active_high": False, "ads1115_address": 73,
                            "zone_channels": {"custom": 0}, "zone_valves": {"custom": "valve1"}}}
        value = validate(migrate(old, self.config), self.config)
        self.assertEqual(value["schedules"][0]["hour"], 8)
        self.assertEqual(value["hardware"]["zone_valves"]["custom"], ["valve1"])
        self.assertEqual(value["hardware"]["ads1115_addresses"], [73])
        self.assertIn("custom", value["hardware"]["zone_calibration"])
        self.assertNotIn("zone1", value["zone_thresholds"])
    def test_pin_conflicts_and_reserved_s3_psram_rejected(self):
        value = clone(self.store.data)
        value["hardware"]["valves"][0]["pin"] = 21
        with self.assertRaises(ValueError): validate(value, self.config)
        self.config.BOARD = "esp32s3"
        self.config.STATUS_LED_PIN = 48
        value["hardware"]["i2c_scl_pin"] = 9
        value["hardware"]["i2c_sda_pin"] = 8
        value["hardware"]["valves"][0]["pin"] = 35
        with self.assertRaises(ValueError): validate(value, self.config)
        value["hardware"]["valves"][0]["pin"] = 10
        validate(value, self.config)
    def test_invalid_update_does_not_mutate_saved_or_live_settings(self):
        before = Path(self.path).read_bytes()
        for body in ({"supplemental_duration_sec": True}, {"max_water_cycles": 100}, {"zone_thresholds": {"zone1": 101}}):
            with self.assertRaises(ValueError): self.app.save_settings(body)
            self.assertEqual(Path(self.path).read_bytes(), before)
    def test_public_store_save_keeps_nested_input_aliases_isolated(self):
        value = clone(self.store.data)
        saved = self.store.save(value)
        self.assertIsNot(saved, value)
        before = clone(saved)
        value["hardware"]["valves"][0]["pin"] = 99
        value["hardware"]["zone_valves"]["zone1"].append("unknown")
        value["schedules"][0]["zone_names"].append("unknown")
        self.assertEqual(self.store.data, before)
        self.assertEqual(read_json(self.path), before)
    def test_private_candidate_commits_without_a_second_tree_copy(self):
        candidate = clone(self.store.data)
        candidate["daily_enabled"] = False
        old = self.store.data
        with patch("settings_store.clone", side_effect=AssertionError("Unexpected full copy")), patch.object(self.store, "save", side_effect=AssertionError("Public copy path used")):
            self.app._save(candidate)
        self.assertIs(self.store.data, candidate)
        self.assertIs(self.app.settings, candidate)
        self.assertIs(self.controller.settings, candidate)
        self.assertTrue(old["daily_enabled"])
        self.assertEqual(read_json(self.path), candidate)
    def test_readback_memory_failure_keeps_durable_and_all_live_settings(self):
        old = self.store.data
        before = Path(self.path).read_bytes()
        configured = []
        self.app.sensors = SimpleNamespace(calibration=None, configure=configured.append)
        load = json.load
        def fail_readback(stream):
            if stream.name == self.path + ".tmp":
                raise MemoryError("Injected settings readback allocation failure")
            return load(stream)
        with patch("persistence.json.load", side_effect=fail_readback):
            with self.assertRaises(MemoryError):
                self.app.save_settings({"daily_enabled": False})
        self.assertIs(self.store.data, old)
        self.assertIs(self.app.settings, old)
        self.assertIs(self.controller.settings, old)
        self.assertTrue(old["daily_enabled"])
        self.assertEqual(configured, [])
        self.assertEqual(Path(self.path).read_bytes(), before)
        # The same request can retry without exposing the failed candidate.
        self.app.save_settings({"daily_enabled": False})
        self.assertFalse(self.app.settings["daily_enabled"])
        self.assertEqual(configured, [self.app.settings])
    def test_application_copies_only_inserted_request_subtrees(self):
        thresholds = {"zone1": 35}
        self.app.save_settings({"zone_thresholds": thresholds})
        thresholds["zone1"] = 99
        self.assertEqual(self.app.settings["zone_thresholds"]["zone1"], 35)
        schedules = clone(self.app.settings["schedules"])
        self.app.save_schedules(schedules)
        schedules[0]["valve_names"].append("unknown")
        self.assertEqual(self.app.settings["schedules"][0]["valve_names"], ["valve1"])
        addresses = [72, 73]
        self.app.save_hardware({"ads1115_addresses": addresses})
        addresses.append(74)
        self.assertEqual(self.app.settings["hardware"]["ads1115_addresses"], [72, 73])
        valves = [{"name": "valve1", "pin": 26}]
        flows = [34]
        self.app.save_valves({"valves": valves, "flow_meter_pins": flows})
        self.assertNotIn("active_high", valves[0])
        valves[0]["pin"] = 99
        flows.append(35)
        self.assertEqual(self.app.settings["hardware"]["valves"][0]["pin"], 26)
        self.assertEqual(self.app.settings["hardware"]["flow_meter_pins"], [34])
    def test_zone_save_does_not_share_mapping_with_request_or_old_settings(self):
        old = self.app.settings
        self.app.save_zones({"zones": [{"name": "zone1", "channel": 0}]})
        self.assertIsNot(self.app.settings["hardware"]["zone_valves"]["zone1"],
                         old["hardware"]["zone_valves"]["zone1"])
        mappings = ["valve1"]
        self.app.save_zones({"zones": [{"name": "zone1", "channel": 0, "valves": mappings}]})
        mappings.append("unknown")
        self.assertEqual(self.app.settings["hardware"]["zone_valves"]["zone1"], ["valve1"])
    def test_zone_old_name_rename_preserves_calibration_and_schedule_targets(self):
        self.store.data["schedules"][0]["zone_names"] = ["zone1"]
        self.app.save_zones({"zones": [{"name": "Jardín", "old_name": "zone1", "channel": 0, "valves": ["valve1"], "threshold": 35}]})
        self.assertEqual(self.app.settings["schedules"][0]["zone_names"], ["Jardín"])
        self.assertEqual(self.app.settings["hardware"]["zone_calibration"]["Jardín"]["dry_raw"], 17500)
    def test_hardware_save_does_not_reconfigure_live_sensors(self):
        self.app.sensors = SimpleNamespace(configure=lambda data: self.fail("Reconfigured old hardware"))
        self.app.save_hardware({"hardware": {"i2c_scl_pin": 19}})
        self.assertIsNotNone(self.app.reboot_at)
        self.assertTrue(self.controller.paused)
    def test_renamed_valve_retains_durable_cooldown_on_reboot(self):
        self.controller.ledger["valves"]["valve1"] = {"schedule": 1900000000}
        self.app.save_valves({"valves": [{"name": "new", "pin": 26, "active_high": True}], "renames": {"valve1": "new"}})
        journal = read_json(self.controller.ledger_path)
        self.assertIn("schedule", journal["valves"]["new"])
        self.assertIn("valve1", journal["valves"])
    def test_scan_returns_complete_result_instead_of_rescheduling_forever(self):
        sensor = SimpleNamespace(found=[72], scan_requested=False)
        sensor.request_scan = lambda: setattr(sensor, "scan_requested", True)
        self.app.sensors = sensor
        self.assertTrue(self.app.scan()["busy"])
        self.assertTrue(self.app.scan()["busy"])
        sensor.scan_requested = False
        self.assertFalse(self.app.scan()["busy"])
    def test_failed_calibration_save_retains_prior_live_values(self):
        previous = clone(self.app.settings["hardware"]["zone_calibration"]["zone1"])
        self.app.calibration_before = ("zone1", previous)
        self.app.settings["hardware"]["zone_calibration"]["zone1"]["dry_raw"] = 19000
        sensor = SimpleNamespace(calibration=None, calibration_result={"raw": 19000})
        self.app.sensors = sensor
        with patch.object(self.store, "save_owned", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.app.commit_calibration()
        self.assertEqual(self.app.settings["hardware"]["zone_calibration"]["zone1"], previous)
        self.assertIn("error", sensor.calibration_result)
    def test_unknown_and_credential_fields_rejected(self):
        for key in ("wifi_password", "password", "wifi", "unexpected"):
            value = clone(self.store.data)
            value[key] = "private"
            with self.assertRaises(ValueError): self.app.save_settings(value, replace=True)
    def test_interrupted_atomic_rename_recovers_previous_generation(self):
        path = str(Path(self.temp.name) / "atomic.json")
        atomic_json(path, {"value": 1})
        rename = os.rename
        def interrupted(source, target):
            if source.endswith(".tmp"): raise OSError("power cut")
            rename(source, target)
        with patch("persistence.os.rename", side_effect=interrupted):
            with self.assertRaises(OSError): atomic_json(path, {"value": 2})
        self.assertEqual(read_json(path), {"value": 1})
    def test_corrupt_current_repair_never_overwrites_good_backup(self):
        path = str(Path(self.temp.name) / "repair.json")
        Path(path).write_text("torn{")
        Path(path + ".prev").write_text('{"value":1}')
        rename = os.rename
        def interrupted(source, target):
            if source.endswith(".tmp"): raise OSError("power cut")
            rename(source, target)
        with patch("persistence.os.rename", side_effect=interrupted):
            with self.assertRaises(OSError): atomic_json(path, {"value": 2})
        self.assertEqual(read_json(path), {"value": 1})
    def test_history_skips_cooperatively_and_prunes_incrementally(self):
        path = Path(self.temp.name) / "history.jsonl"
        now = 1900000000
        with path.open("w") as stream:
            for stamp in [now - 700000] * 30 + [now - 30]:
                stream.write(json.dumps({"t": stamp, "readings": []}) + "\n")
            stream.write("torn")
        iterator = self.state.history(1, now)
        self.assertIsNone(next(iterator))
        points = [p for p in iterator if p]
        self.assertEqual(len(points), 1)
        self.state.prune(now)
        self.assertIsNotNone(self.state.prune_job)
        for unused in range(40): self.state.prune(now)
        self.assertIsNone(self.state.prune_job)
        self.assertEqual(len(path.read_text().splitlines()), 1)
    def test_original_csv_history_and_cooldown_migrate(self):
        (Path(self.temp.name) / "history.csv").write_text("839923200,zone1=42.5\n")
        self.assertEqual([p for p in self.state.history(24, 1786608000) if p][0]["readings"][0]["percent"], 42.5)
        atomic_json(self.controller.ledger_path, {"daily": {"valve1": 839923200}, "supplemental": {}})
        c = Controller(self.store.data, {"valve1": Valve(self.config.VALVES[0], Pin)}, self.config, ledger_path=self.controller.ledger_path)
        self.assertIsNone(c.inhibited)
        self.assertEqual(c.ledger["valves"]["valve1"]["schedule"], 1786608000)
    def test_events_survive_restart_with_bounded_ring(self):
        self.state.event("water", "closed")
        self.state.flush_events()
        restored = State(self.temp.name)
        self.assertEqual(restored.events_ring[-1]["message"], "closed")

if __name__ == "__main__": unittest.main()
