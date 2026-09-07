"""Run the real application/API/HTTP transport with fake pins and readings.

This is a desktop UI/integration tool. It never connects to an ESP32.
"""
import argparse
import math
from pathlib import Path
import runpy
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from application import Application
from settings_store import SettingsStore
from controller import Controller
from state import State
from valve import Valve
from updater import Updater
from web import HTTPServer
from api import APIRouter
from compat import ticks_ms, epoch

class FakePin:
    OUT = 1
    def __init__(self, number, mode, value=0):
        self.level = value
    def value(self, value):
        self.level = value

class DemoSensors:
    def __init__(self, settings):
        self.readings, self.found = {}, [72]
        self.scan_requested = False
        self.calibration = None
        self.calibration_changed = False
        self.configure(settings)
    def configure(self, settings):
        self.settings = settings
    def poll(self, now):
        self.scan_requested = False
        self.readings = {name: {"name": name, "raw": 12000 + index * 500,
            "percent": round(55 + index * 7 + math.sin(time.monotonic() / 10) * 2, 1),
            "error": None, "updated_ms": now}
            for index, name in enumerate(self.settings["hardware"]["zone_channels"])}
    def request_scan(self):
        self.scan_requested = True
    def start_calibration(self, zone, point, now):
        raise RuntimeError("Calibration requires a real ESP32 sensor")
    def calibration_status(self):
        return {"busy": False, "result": None, "calibration": self.settings["hardware"]["zone_calibration"]}

class DemoWifi:
    def status(self):
        return {"ssid": "Garden demo", "connected": True, "ip": "127.0.0.1", "rssi": -48, "lan_ok": True,
                "state": "connected", "portal": {"active": False}}
    def save_credentials(self, ssid, password):
        raise RuntimeError("WiFi changes require a real ESP32")

def main(port):
    config = SimpleNamespace(**{k: v for k, v in runpy.run_path(str(ROOT / "src/config.example.py")).items() if k.isupper()})
    config.STARTUP_GRACE_SEC = 0
    config.VALVES = [{"name": "Raised bed", "pin": 26, "active_high": True},
                     {"name": "Herb bed", "pin": 27, "active_high": True}]
    config.ZONES = [{"name": "Tomatoes", "channel": 0, "valve": "Raised bed"},
                    {"name": "Jardín", "channel": 1, "valve": "Herb bed"}]
    directory = ROOT / ".tools/simulation"
    directory.mkdir(parents=True, exist_ok=True)
    html = (ROOT / "src/index.html").read_text(encoding="utf-8")
    html = html.replace("<body>", '<body><div style="padding:10px;text-align:center;background:#ffe6a7;color:#332400">DESKTOP SIMULATOR — no physical valves or sensors connected</div>')
    (directory / "index.html").write_text(html, encoding="utf-8")
    store = SettingsStore(config, str(directory / "settings.json"))
    store.data["daily_enabled"] = False
    store.data["moisture_watering_enabled"] = False
    state = State(str(directory))
    controller = Controller(store.data, {v["name"]: Valve(v, FakePin) for v in store.data["hardware"]["valves"]},
        config, ticks_ms(), str(directory / "watering_state.json"), event=state.event)
    app = Application(config, store, controller, state)
    app.sensors = DemoSensors(app.settings)
    app.wifi = DemoWifi()
    app.environment = SimpleNamespace(readings={"temperature_c": 24.6, "humidity_percent": 57, "pressure_hpa": 1015.2, "rain": False})
    app.updater = Updater(root=str(directory), close_valves=lambda: controller.stop_all("update"), idle=controller.idle)
    app.mem = {"mem_free": 94800, "mem_alloc": 41000, "idf_free": 37000, "idf_largest": 30000}
    app.server = HTTPServer(APIRouter(app), root=str(directory), port=port, event=state.event, upload_factory=app.updater.begin_upload)
    if not app.server.start():
        raise SystemExit("Could not start simulator port")
    names = tuple(store.data["hardware"]["zone_channels"])
    now = ticks_ms()
    for offset in range(180):
        values = bytes(int(110 + index * 12 + 10 * math.sin(offset / 13)) for index in range(len(names)))
        state.live.append((epoch() - (179 - offset) * 60, names, values, now))
    state.event("simulator", "Desktop simulation; no hardware connected")
    print("Simulator: http://127.0.0.1:%s" % port, flush=True)
    start = time.monotonic()
    try:
        while True:
            now = ticks_ms()
            controller.tick(now, epoch(), True)
            app.uptime_ms = int((time.monotonic() - start) * 1000)
            app.sensors.poll(now)
            app.server.poll(now)
            app.updater.poll(now)
            time.sleep(0.005)
    except KeyboardInterrupt:
        app.server.close()
        controller.stop_all("simulator stopped")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    main(parser.parse_args().port)
