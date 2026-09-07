"""Execute the actual supervisor with fake peripherals and an accelerated clock."""
import gc
import importlib
import os
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("src"))
from test_sensors import Bus

class Done(BaseException): pass

class RuntimeTests(unittest.TestCase):
    def test_boot_grace_manual_cutoff_and_web_fault_isolation(self):
        config = types.SimpleNamespace(**{k: v for k, v in runpy.run_path("src/config.example.py").items() if k.isupper()})
        config.STATUS_LED_PIN = None
        clock = [0]
        pins, feeds, observed = {}, [], {}
        class Pin:
            OUT = 1
            def __init__(self, number, mode, value=0):
                self.number = number
                pins[number] = value
            def value(self, value): pins[self.number] = value
        class WDT:
            def __init__(self, **kwargs): pass
            def feed(self): feeds.append(clock[0])
        machine = types.SimpleNamespace(Pin=Pin, WDT=WDT, reset_cause=lambda: 1,
                                       reset=lambda: self.fail("Unexpected reset"))
        class Wifi:
            def __init__(self, *args, **kwargs):
                self_outer.assertTrue(feeds, "Watchdog must cover WiFi initialization")
                self.connected = False
                self.dns = types.SimpleNamespace(resolve=lambda host: None)
                self.portal = types.SimpleNamespace(active=False)
            def poll(self, *args): pass
            def status(self): return {"connected": False}
        class NTP:
            def __init__(self, *args, **kwargs): self.synced = False
            def poll(self, *args): pass
        class Server:
            def __init__(self, handler, **kwargs):
                self.app = handler.app
                observed["app"] = self.app
            def start(self): return True
            def health(self): return {"listening": True, "active_uploads": 0}
            def poll(self, now):
                if now == 59000:
                    with self_outer.assertRaises(RuntimeError): self.app.water(duration=2)
                if now == 60000:
                    self.app.water(duration=2)
                if now == 60500:
                    self_outer.assertTrue(self.app.controller.any_open())
                    raise OSError("injected listener error while watering")
                if now >= 62500:
                    self_outer.assertFalse(self.app.controller.any_open())
        self_outer = self
        def sleep(ms):
            clock[0] += 500
            if clock[0] >= 70000: raise Done()
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"machine": machine, "wifi": types.SimpleNamespace(WifiManager=Wifi, NTPClient=NTP)}):
                import runtime
                import moisture, web
                with patch.object(runtime, "machine", machine), patch.object(runtime, "ticks_ms", lambda: clock[0]), patch.object(runtime, "epoch", lambda: 1900000000 + clock[0] // 1000), patch.object(runtime.time, "sleep_ms", sleep, create=True), patch.object(gc, "mem_free", lambda: 90000, create=True), patch.object(gc, "mem_alloc", lambda: 40000, create=True), patch.object(moisture, "create_bus", return_value=Bus(raw=12000)), patch.object(web, "HTTPServer", Server):
                    os.chdir(directory)
                    try:
                        with self.assertRaises(Done): runtime.run(config)
                        self.assertEqual(pins[26], 0)
                        self.assertGreater(len(feeds), 100)
                        self.assertTrue(any(e["type"] == "web_error" for e in observed["app"].state.events_ring))
                        self.assertIsNone(observed["app"].controller.inhibited)
                    finally:
                        os.chdir(old_cwd)

if __name__ == "__main__": unittest.main()
