"""Flash telemetry must never extend the interval an output is energized."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from controller import Controller
from state import State


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = State(self.temp.name)
        self.readings = {"zone1": {"percent": 42.5, "error": None}}
        self.now = 1900000000

    def lines(self, name):
        return [json.loads(line) for line in (self.root / name).read_text().splitlines()]

    def test_event_callbacks_do_not_open_files_and_flush_one_reference_at_a_time(self):
        with patch("builtins.open", side_effect=AssertionError("File I/O in event callback")), patch("builtins.print"):
            self.state.event("valve_open", "valve1")
            self.state.event("valve_close", "valve1")
        self.assertEqual(len(self.state.pending_events), 2)
        self.assertIs(self.state.pending_events[0], self.state.events_ring[0])
        self.assertFalse((self.root / "events.log").exists())
        self.assertTrue(self.state.flush_events(0))
        self.assertEqual(len(self.state.pending_events), 1)
        self.assertEqual([entry["type"] for entry in self.lines("events.log")], ["valve_open"])
        self.assertTrue(self.state.flush_events(1))
        self.assertFalse(self.state.flush_events(2))
        self.assertEqual([entry["type"] for entry in self.lines("events.log")], ["valve_open", "valve_close"])

    def test_pending_event_queue_and_ram_ring_stay_bounded(self):
        with patch("builtins.open", side_effect=AssertionError("File I/O in event callback")), patch("builtins.print"):
            for index in range(200):
                self.state.event("network", str(index))
        self.assertEqual(len(self.state.pending_events), 64)
        self.assertEqual(len(self.state.events_ring), 64)
        self.assertEqual(self.state.pending_events[0]["message"], "136")
        self.assertEqual(self.state.pending_events[-1]["message"], "199")
        for queued, live in zip(self.state.pending_events, self.state.events_ring):
            self.assertIs(queued, live)

    def test_log_rotation_waits_for_explicit_flush(self):
        previous = b"x" * 32769
        (self.root / "events.log").write_bytes(previous)
        with patch("builtins.open", side_effect=AssertionError("File I/O in event callback")), patch("builtins.print"):
            self.state.event("valve_open", "valve1")
        self.assertEqual((self.root / "events.log").read_bytes(), previous)
        self.assertFalse((self.root / "events.log.prev").exists())
        self.state.flush_events(0)
        self.assertEqual((self.root / "events.log.prev").read_bytes(), previous)
        self.assertEqual(self.lines("events.log")[0]["type"], "valve_open")

    def test_event_write_failure_retains_entry_with_bounded_retry_rate(self):
        with patch("builtins.print"):
            self.state.event("valve_close", "valve1")
        with patch("builtins.open", side_effect=OSError("storage unavailable")) as failed_open:
            self.assertFalse(self.state.flush_events(0))
            self.assertFalse(self.state.flush_events(4999))
            self.assertEqual(failed_open.call_count, 1)
            self.assertFalse(self.state.flush_events(5000))
            self.assertEqual(failed_open.call_count, 2)
        self.assertEqual(len(self.state.pending_events), 1)
        self.assertTrue(self.state.flush_events(10000))
        self.assertEqual(self.lines("events.log")[0]["type"], "valve_close")

    def test_active_history_stays_in_ram_and_latest_point_flushes_before_next_minute(self):
        with patch("builtins.open", side_effect=AssertionError("Flash history while active")):
            self.state.sample(self.readings, 0, self.now, True, allow_flash=False)
            self.readings["zone1"]["percent"] = 51.5
            self.state.sample(self.readings, 60000, self.now + 60, True, allow_flash=False)
        self.assertEqual(len(self.state.live), 2)
        self.assertIs(self.state.pending_flash, self.state.live[-1])
        self.assertIsNone(self.state.last_flash)
        self.assertFalse((self.root / "history.jsonl").exists())
        self.state.sample(self.readings, 60010, self.now + 60, True, allow_flash=True)
        self.assertEqual(len(self.state.live), 2)
        self.assertIsNone(self.state.pending_flash)
        self.assertEqual(self.state.last_flash, 60010)
        saved = self.lines("history.jsonl")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["t"], self.now + 60)
        self.assertEqual(saved[0]["readings"][0]["percent"], 51.5)
        self.state.sample(self.readings, 120010, self.now + 120, True, allow_flash=True)
        self.assertEqual(len(self.lines("history.jsonl")), 1)
        self.state.sample(self.readings, 960010, self.now + 960, True, allow_flash=True)
        self.assertEqual(len(self.lines("history.jsonl")), 2)

    def test_pending_history_waits_for_pruning_then_flushes_immediately(self):
        self.state.prune_job = object()
        with patch("builtins.open", side_effect=AssertionError("History append during prune")):
            self.state.sample(self.readings, 0, self.now, True, allow_flash=True)
        self.assertIsNotNone(self.state.pending_flash)
        self.state.prune_job = None
        self.state.sample(self.readings, 1, self.now, True, allow_flash=True)
        self.assertEqual(self.lines("history.jsonl")[0]["t"], self.now)

    def test_failed_history_write_retains_sample_and_does_not_advance_cadence(self):
        with patch("builtins.open", side_effect=OSError("storage unavailable")) as failed_open:
            with self.assertRaises(OSError):
                self.state.sample(self.readings, 0, self.now, True, allow_flash=True)
            self.state.sample(self.readings, 4999, self.now + 4, True, allow_flash=True)
            self.assertEqual(failed_open.call_count, 1)
        self.assertIsNone(self.state.last_flash)
        self.assertIsNotNone(self.state.pending_flash)
        self.state.sample(self.readings, 5000, self.now + 5, True, allow_flash=True)
        self.assertEqual(self.state.last_flash, 5000)
        self.assertIsNone(self.state.pending_flash)
        self.assertEqual(self.lines("history.jsonl")[0]["t"], self.now)

    def test_long_active_session_has_one_pending_snapshot_and_bounded_live_history(self):
        with patch("builtins.open", side_effect=AssertionError("Flash history while active")):
            for minute in range(200):
                self.state.sample(self.readings, minute * 60000, self.now + minute * 60,
                                  True, allow_flash=False)
        self.assertEqual(len(self.state.live), 180)
        self.assertIs(self.state.pending_flash, self.state.live[-1])

    def test_actual_runtime_passes_idle_state_and_never_flushes_while_open(self):
        # Reuse the established real-supervisor/fake-peripheral scenario. Its
        # manual run stays open for two simulated seconds and injects an event.
        scenario = importlib.import_module("test_runtime").RuntimeTests()
        controllers = []
        calls = {"active_samples": 0, "flushes": 0, "writes": 0}
        violations = []
        init = Controller.__init__
        sample = State.sample
        flush = State.flush_events
        original_open = open
        def observe_controller(controller, *args, **kwargs):
            init(controller, *args, **kwargs)
            controllers.append(controller)
        def observe_sample(state, *args, **kwargs):
            idle = controllers[-1].idle()
            if kwargs.get("allow_flash") != idle:
                violations.append("history allow_flash did not match controller idle")
            self.assertEqual(kwargs.get("allow_flash"), idle)
            if controllers[-1].any_open():
                calls["active_samples"] += 1
            return sample(state, *args, **kwargs)
        def observe_flush(state, *args, **kwargs):
            if not controllers[-1].idle():
                violations.append("events flush while controller active")
            self.assertTrue(controllers[-1].idle(), "Event flush while controller active")
            calls["flushes"] += 1
            return flush(state, *args, **kwargs)
        def observe_open(path, mode="r", *args, **kwargs):
            if (any(char in mode for char in "wax+") and
                    str(path).endswith(("events.log", "history.jsonl", "history.jsonl.tmp"))):
                if controllers[-1].any_open():
                    violations.append("telemetry flash write while output open")
                self.assertFalse(controllers[-1].any_open(), "Telemetry flash write while output open")
                calls["writes"] += 1
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Controller, "__init__", observe_controller), \
                patch.object(State, "sample", observe_sample), \
                patch.object(State, "flush_events", observe_flush), \
                patch("builtins.open", observe_open):
            scenario.test_boot_grace_manual_cutoff_and_web_fault_isolation()
        self.assertGreater(calls["active_samples"], 0)
        self.assertGreater(calls["flushes"], 0)
        self.assertGreater(calls["writes"], 0)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
