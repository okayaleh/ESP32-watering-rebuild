import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("src"))
from controller import Controller
from valve import Valve
from settings_store import defaults, migrate
from compat import ticks_add

class Pin:
    OUT = 1
    def __init__(self, number, mode, value):
        self.number, self.level = number, value
        self.fail = False
    def value(self, value):
        if self.fail:
            raise OSError("broken output")
        self.level = value

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = SimpleNamespace(VALVES=[{"name": "a", "pin": 26, "active_high": True},
                                               {"name": "b", "pin": 27, "active_high": False}],
            ZONES=[{"name": "bed", "channel": 0, "valve": "a"}], STARTUP_GRACE_SEC=0,
            MOISTURE_CHECK_INTERVAL_SEC=1, MAX_VALVE_OPEN_SEC=10)
        self.settings = migrate(defaults(self.config), self.config)
        self.settings["supplemental_duration_sec"] = 1
        self.settings["zone_durations"] = {"bed": 1}
        self.settings["soak_recheck_sec"] = 1
        self.path = os.path.join(self.temp.name, "water.json")
        self.controller = self.make()
    def tearDown(self):
        self.temp.cleanup()
    def make(self, now=0):
        return Controller(self.settings, {v["name"]: Valve(v, Pin) for v in self.config.VALVES}, self.config, now, self.path)
    def reading(self, percent=10, now=0):
        return {"bed": {"percent": percent, "updated_ms": now, "error": None}}
    def test_sequential_and_active_low(self):
        c = self.controller
        c.enqueue(["a", "b", "a"], 1)
        c.tick(0)
        self.assertTrue(c.valves["a"].is_open)
        self.assertFalse(c.valves["b"].is_open)
        self.assertEqual(c.valves["b"].pin.level, 1)
        c.tick(1000)
        self.assertFalse(c.valves["a"].is_open)
        self.assertTrue(c.valves["b"].is_open)
        self.assertEqual(c.valves["b"].pin.level, 0)
        c.tick(2000)
        self.assertFalse(c.any_open())
    def test_cutoff_survives_wrap_and_backward_wall_clock(self):
        start = (1 << 30) - 500
        c = self.make(start)
        c.enqueue(["a"], 2)
        c.tick(start, 1900000000, True)
        c.tick(ticks_add(start, 2000), 100, True)
        self.assertFalse(c.any_open())
    def test_grace_rejects_manual_and_automatic(self):
        c = self.controller
        c.grace_ms = 60000
        with self.assertRaises(RuntimeError):
            c.enqueue(["a"], 1)
        c.moisture(self.reading(), 0)
        self.assertEqual(c.queue, [])
    def test_storage_failure_never_energizes(self):
        c = self.controller
        c.enqueue(["a"], 1)
        with patch("controller.atomic_json", side_effect=OSError("disk full")):
            c.tick(0)
        self.assertFalse(c.any_open())
        self.assertEqual(c.valves["a"].pin.level, 0)
        self.assertIsNotNone(c.inhibited)
    def test_interval_begins_after_slow_durable_intent_write(self):
        c = self.controller
        clock = [0]
        c.clock = lambda: clock[0]
        persist = c._persist
        def slow_persist():
            persist()
            clock[0] += 450
        c._persist = slow_persist
        c.enqueue(["a"], 1)
        c.tick(0)
        self.assertEqual(c.valves["a"].opened_ms, 450)
        c.tick(1449)
        self.assertTrue(c.valves["a"].is_open)
        c.tick(1450)
        self.assertFalse(c.valves["a"].is_open)
    def test_schedule_does_not_commit_flash_during_an_existing_run(self):
        c = self.controller
        c.settings["schedules"] = [{"id": 7, "hour": 0, "minute": 0,
            "duration_sec": 1, "enabled": True, "valve_names": ["b"], "zone_names": []}]
        c.settings["tz_offset_min"] = 0
        c.enqueue(["a"], 1)
        c.tick(0, 1900022400, True)
        with patch.object(c, "_persist", wraps=c._persist) as persist:
            c.schedules()
            self.assertEqual(c.queue, [("b", 1, "schedule")])
            persist.assert_not_called()
            c.tick(1000, 1900022401, True)
            self.assertTrue(c.valves["b"].is_open)
            self.assertGreaterEqual(persist.call_count, 1)
        self.assertIn("7", self.make().ledger["fired"])
    def test_close_failure_attempts_other_valves_and_withholds_watchdog(self):
        c = self.controller
        c.valves["a"].open(0, 1, "manual")
        c.valves["b"].open(0, 1, "manual")
        c.valves["a"].pin.fail = True
        self.assertFalse(c.tick(1000))
        self.assertFalse(c.valves["b"].is_open)
        self.assertIsNotNone(c.inhibited)
    def test_intent_survives_crash_before_close_without_ntp(self):
        c = self.controller
        c.enqueue(["a"], 1)
        c.tick(0)
        rebooted = self.make()
        rebooted.moisture(self.reading(), 0)
        self.assertEqual(rebooted.queue, [])
        self.assertGreater(rebooted.cooldowns["a"]["moisture"], 0)
    def test_cooldowns_do_not_block_unrelated_valve(self):
        c = self.controller
        c.cooldowns["a"]["schedule"] = 100000
        self.settings["hardware"]["zone_valves"]["bed"] = ["a", "b"]
        c.moisture(self.reading(), 0)
        self.assertEqual([item[0] for item in c.queue], ["b"])
    def test_soak_requires_new_reading_and_stops_at_wet_target(self):
        c = self.controller
        c.moisture(self.reading(), 0)
        c.tick(0)
        c.tick(1000)
        c.moisture(self.reading(35, 0), 2000)
        self.assertEqual(c.queue, [])
        c.moisture(self.reading(35, 2100), 2100)
        self.assertEqual(len(c.queue), 1)  # still below default wet=40
        c.tick(2100)
        c.tick(3100)
        c.moisture(self.reading(42, 4200), 4200)
        self.assertIsNone(c.session)
        self.assertFalse(c.any_open())
    def test_failed_sensor_ends_soak_session(self):
        c = self.controller
        c.moisture(self.reading(), 0)
        c.tick(0)
        c.tick(1000)
        c.moisture({"bed": {"percent": None, "error": "absent"}}, 2000)
        self.assertIsNone(c.session)
    def test_max_cycles_caps_permanently_dry_sensor(self):
        c = self.controller
        self.settings["max_water_cycles"] = 2
        c.moisture(self.reading(), 0)
        c.tick(0)
        c.tick(1000)
        c.moisture(self.reading(10, 2100), 2100)
        c.tick(2100)
        c.tick(3100)
        c.moisture(self.reading(10, 4200), 4200)
        self.assertIsNone(c.session)
        self.assertEqual(c.queue, [])
    def test_schedule_no_clock_and_once_across_reboot_and_backward_step(self):
        c = self.controller
        self.settings["schedules"] = [{"id": 1, "hour": 0, "minute": 0, "duration_sec": 1,
                                       "enabled": True, "valve_names": ["a"], "zone_names": []}]
        self.settings["tz_offset_min"] = 0
        stamp = 20000 * 86400
        c.tick(0, stamp, False)
        c.schedules()
        self.assertEqual(c.queue, [])
        c.tick(10, stamp, True)
        c.schedules()
        self.assertEqual(len(c.queue), 1)
        rebooted = self.make()
        rebooted.tick(0, stamp + 20, True)
        rebooted.schedules()
        self.assertEqual(rebooted.queue, [])
        rebooted.tick(10, stamp - 86400, True)
        rebooted.schedules()
        self.assertEqual(rebooted.queue, [])
    def test_manual_stop_cancels_queue_and_soak(self):
        c = self.controller
        c.moisture(self.reading(), 0)
        c.tick(0)
        c.close("a")
        c.moisture(self.reading(), 1000)
        self.assertTrue(c.idle())

if __name__ == "__main__":
    unittest.main()
