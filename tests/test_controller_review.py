"""Independent fault-injection and maximum-capacity controller regressions."""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath('src'))
from controller import Controller
from valve import Valve
from settings_store import defaults, migrate
from persistence import atomic_json


class Pin:
    OUT = 1
    def __init__(self, number, mode, value):
        self.level = value
    def value(self, value):
        self.level = value


class ControllerReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'watering.json')
        self.config = SimpleNamespace(
            VALVES=[{'name': 'v%d' % i, 'pin': 13 + i, 'active_high': True} for i in range(8)],
            ZONES=[{'name': 'bed', 'channel': 0, 'valve': 'v0'}],
            STARTUP_GRACE_SEC=0, MAX_VALVE_OPEN_SEC=600, MOISTURE_CHECK_INTERVAL_SEC=1)
        self.settings = migrate(defaults(self.config), self.config)

    def controller(self):
        valves = {spec['name']: Valve(spec, Pin) for spec in self.config.VALVES}
        return Controller(self.settings, valves, self.config, 0, self.path)

    def test_malformed_nested_journal_keeps_outputs_closed_and_reports_fault(self):
        atomic_json(self.path, {'valves': {'v0': None}, 'fired': {}})
        controller = self.controller()
        self.assertFalse(controller.any_open())
        self.assertTrue(controller.inhibited)
        controller.tick(0, 1900000000, True)
        self.assertFalse(controller.any_open())

    def test_malformed_schedule_journal_never_escapes_safety_tick(self):
        atomic_json(self.path, {'valves': {}, 'fired': {'1': 'broken'}})
        controller = self.controller()
        controller.tick(0, 1900000000, True)
        controller.schedules()
        self.assertFalse(controller.any_open())
        self.assertTrue(controller.inhibited)

    def test_maximum_schedules_same_minute_preserve_every_occurrence(self):
        self.settings['tz_offset_min'] = 0
        self.settings['schedules'] = [
            {'id': i + 1, 'hour': 0, 'minute': 0, 'duration_sec': 600,
             'enabled': True, 'valve_names': ['v%d' % j for j in range(8)], 'zone_names': []}
            for i in range(20)]
        controller = self.controller()
        controller.tick(0, 20000 * 86400, True)
        controller.schedules()
        self.assertEqual(len(controller.ledger['fired']), 20)
        self.assertEqual(len(controller.queue), 160)


if __name__ == '__main__':
    unittest.main()
