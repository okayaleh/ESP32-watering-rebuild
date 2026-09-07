"""Execute the USB entry point and verify every exit closes all outputs."""
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


MAIN = Path(__file__).resolve().parents[1] / "src" / "main.py"


class ResetCalled(BaseException):
    pass


class MainMaintenanceTests(unittest.TestCase):
    def execute(self, failure, failing_close=None):
        pins = {}
        closes = []
        exiting = [False]
        resets = []

        class Pin:
            OUT = 1

            def __init__(self, number, mode, value):
                if exiting[0]:
                    closes.append((number, value))
                    if number == failing_close:
                        raise OSError("Injected output closure failure")
                pins[number] = value

        def run(config):
            # Simulate live outputs using both relay polarities.
            pins.update({26: 1, 27: 0, 33: 1})
            exiting[0] = True
            if failure is not None:
                raise failure

        def reset():
            resets.append(True)
            raise ResetCalled()

        def watchdog(**kwargs):
            self.fail("USB maintenance must leave the existing watchdog untouched")

        machine = types.SimpleNamespace(Pin=Pin, reset=reset,
                                        reset_cause=lambda: 1, WDT=watchdog)
        config = types.SimpleNamespace(VALVES=[
            {"pin": 26}, {"pin": 27, "active_high": False}, {"pin": 33}])
        previous_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"machine": machine, "config": config,
                                          "runtime": types.SimpleNamespace(run=run)}):
                with patch("builtins.print") as printed:
                    os.chdir(directory)
                    try:
                        try:
                            runpy.run_path(str(MAIN))
                        except ResetCalled:
                            pass
                    finally:
                        os.chdir(previous_cwd)
        return pins, closes, resets, printed

    def test_ctrl_c_closes_every_output_and_allows_repl_without_reset(self):
        pins, closes, resets, printed = self.execute(KeyboardInterrupt())
        self.assertEqual(pins, {26: 0, 27: 1, 33: 0})
        self.assertEqual(closes, [(26, 0), (27, 1), (33, 0)])
        self.assertFalse(resets)
        printed.assert_any_call(
            "USB maintenance: outputs closed; watchdog timeout still applies.")

    def test_runtime_error_closes_every_output_then_resets(self):
        pins, closes, resets, printed = self.execute(RuntimeError("Injected runtime failure"))
        self.assertEqual(pins, {26: 0, 27: 1, 33: 0})
        self.assertEqual(closes, [(26, 0), (27, 1), (33, 0)])
        self.assertEqual(resets, [True])
        self.assertFalse(any("USB maintenance" in str(call) for call in printed.call_args_list))

    def test_ctrl_c_closure_failure_still_attempts_later_outputs_and_resets(self):
        pins, closes, resets, printed = self.execute(KeyboardInterrupt(), failing_close=26)
        self.assertEqual(closes, [(26, 0), (27, 1), (33, 0)])
        self.assertEqual(pins[27], 1)
        self.assertEqual(pins[33], 0)
        self.assertEqual(resets, [True])
        self.assertFalse(any("USB maintenance" in str(call) for call in printed.call_args_list))

    def test_normal_return_closes_every_output_then_resets(self):
        pins, closes, resets, printed = self.execute(None)
        self.assertEqual(pins, {26: 0, 27: 1, 33: 0})
        self.assertEqual(closes, [(26, 0), (27, 1), (33, 0)])
        self.assertEqual(resets, [True])


if __name__ == "__main__":
    unittest.main()
