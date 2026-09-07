"""Early closure must use previous custom pins before ROM/OTA flash work."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from boot import close_boot_valves


class BootValveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.closed = []
        calls = self.closed
        class Pin:
            OUT = 1
            def __init__(self, number, mode, value):
                calls.append((number, value))
        self.pin = Pin
        self.config = types.SimpleNamespace(VALVES=[{"pin": 26, "active_high": True}])
        self.patch_config = patch.dict(sys.modules, {"config": self.config})
        self.patch_config.start()
        self.addCleanup(self.patch_config.stop)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def close(self):
        return close_boot_valves(str(self.root), self.pin)

    def test_parseable_but_malformed_current_uses_previous_custom_pins(self):
        self.write("settings.json.prev", {"hardware": {"valves": [
            {"pin": 32, "active_high": False}, {"pin": 33, "active_high": True}]}})
        for invalid in (None, [], {}, {"hardware": None}, {"hardware": []},
                        {"hardware": {}}, {"hardware": {"valves": None}},
                        {"hardware": {"valves": {"pin": 27}}},
                        {"hardware": {"valves": "unknown"}}, {"hardware": {"valves": []}}):
            with self.subTest(invalid=invalid):
                self.closed.clear()
                self.write("settings.json", invalid)
                self.assertTrue(self.close())
                self.assertEqual(self.closed, [(32, 1), (33, 0)])

    def test_current_custom_pins_take_precedence_without_changing_old_pins(self):
        self.write("settings.json", {"hardware": {"valves": [{"pin": 27, "active_high": False}]}})
        self.write("settings.json.prev", {"hardware": {"valves": [{"pin": 32}]}})
        self.assertTrue(self.close())
        self.assertEqual(self.closed, [(27, 1)])

    def test_bad_generations_fall_back_to_explicit_configured_valves(self):
        self.config.VALVES = [{"pin": 25, "active_high": False}]
        self.write("settings.json", {"hardware": "bad"})
        self.write("settings.json.prev", None)
        self.assertTrue(self.close())
        self.assertEqual(self.closed, [(25, 1)])

    def test_malformed_entries_never_guess_gpio_or_skip_later_known_outputs(self):
        invalid = [None, [], "unknown", {}, {"pin": True}, {"pin": "26"},
                   {"pin": -1}, {"pin": 99}, {"pin": 25, "active_high": "false"}]
        self.write("settings.json", {"hardware": {"valves": invalid + [
            {"pin": 27, "active_high": False}, {"pin": 33}]}})
        with patch("builtins.print"):
            self.assertFalse(self.close())
        self.assertEqual(self.closed, [(27, 1), (33, 0)])

    def test_malformed_config_container_reports_failure_without_gpio_guess(self):
        self.config.VALVES = {"pin": 26}
        with patch("builtins.print"):
            self.assertFalse(self.close())
        self.assertEqual(self.closed, [])


if __name__ == "__main__":
    unittest.main()
