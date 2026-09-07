"""Cooperative supervisor: safety runs before AND after every work slice.

No subsystem feeds the watchdog. Only successful output safety checks can.
Network clients, DNS, NTP, downloads and ADC conversions advance incrementally.
"""
import gc
import time
import machine
from compat import ticks_ms, ticks_diff, epoch
from settings_store import SettingsStore
from controller import Controller
from valve import Valve
from state import State
from application import Application

def run(config):
    state = State()
    store = SettingsStore(config)
    valves = {}
    for spec in store.data["hardware"]["valves"]:
        valves[spec["name"]] = Valve(spec)
    controller = Controller(store.data, valves, config, ticks_ms(), event=state.event, clock=ticks_ms)
    app = Application(config, store, controller, state)
    wdt = None
    watchdog_seconds = getattr(config, "WATCHDOG_TIMEOUT_SEC", 120)
    if watchdog_seconds:
        try:
            wdt = machine.WDT(timeout=int(watchdog_seconds * 1000))
            # ESP32 task-WDT needs its first feed to reliably arm the timer.
            # Validate output safety before it protects subsystem startup too.
            if not controller.tick(ticks_ms(), epoch(), False):
                machine.reset()
            wdt.feed()
        except Exception as exc:
            controller.inhibited = "Hardware watchdog unavailable"
            state.event("safety_fault", str(exc))
    state.event("boot", "Reset cause %s" % machine.reset_cause())
    # Establish WiFi driver before importing the larger application modules.
    from wifi import WifiManager, NTPClient
    try:
        app.wifi = WifiManager(config, event=state.event)
        # active(True), not WLAN construction alone, reserves radio resources.
        # Do this while outputs are closed and before loading larger modules.
        app.wifi.poll(ticks_ms(), False)
        app.ntp = NTPClient(config, event=state.event, resolver=app.wifi.dns)
    except Exception as exc:
        state.event("network_error", str(exc))
    from moisture import create_bus, MoistureManager
    from env_sensors import EnvSensors
    from updater import Updater, UpdateSchedule, mark_stable
    from web import HTTPServer
    from api import APIRouter
    from status_led import StatusLED
    bus = None
    def init_sensors():
        nonlocal bus
        hw = app.settings["hardware"]
        bus = create_bus(hw["i2c_scl_pin"], hw["i2c_sda_pin"],
                         getattr(config, "I2C_TIMEOUT_US", 50000), getattr(config, "I2C_BUS_RECOVERY", True))
        app.sensors = MoistureManager(bus, app.settings, ticks_ms(),
            getattr(config, "MOISTURE_CHECK_INTERVAL_SEC", 15) * 1000, getattr(config, "ADS_PROBE_SEC", 60) * 1000)
        app.environment = EnvSensors(bus, lambda: app.sensors.found, rain_pin=hw["rain_sensor_pin"])
    try:
        init_sensors()
    except Exception as exc:
        state.event("sensor_error", str(exc))
    try:
        app.updater = Updater(base_url=getattr(config, "UPDATE_BASE_URL", ""),
            manifest_path=getattr(config, "UPDATE_MANIFEST_PATH", "build/manifest.json"),
            close_valves=lambda: controller.stop_all("firmware update"), idle=controller.idle,
            resolver=app.wifi.dns.resolve if app.wifi else None,
            timeout_ms=max(15000, getattr(config, "UPDATE_TIMEOUT_SEC", 60) * 1000),
            github_repo=getattr(config, "UPDATE_GITHUB_REPO", "okayaleh/ESP32-watering-rebuild"))
        app.updater.status["auto_install"] = bool(getattr(config, "UPDATE_AUTO_INSTALL", False))
        app.updater.status["check_hour"] = getattr(config, "UPDATE_CHECK_HOUR", 4)
    except Exception as exc:
        state.event("update_error", str(exc))
    def upload_factory(filename):
        if not app.updater or not controller.idle():
            raise RuntimeError("Uploads require an idle controller")
        was_paused = controller.paused
        controller.paused = True
        try:
            return app.updater.begin_upload(filename)
        except Exception:
            controller.paused = was_paused
            raise
    app.server = HTTPServer(APIRouter(app), event=state.event,
        upload_factory=upload_factory, redirect=lambda: bool(app.wifi and app.wifi.portal.active),
        load_shed=lambda: app.mem.get("idf_largest", 100000) < 2048)
    app.server.start()  # Failure is observable and retried by HTTPServer.poll.
    try:
        led = StatusLED(config)
    except Exception as exc:
        led = None
        state.event("led_error", str(exc))
    last_metrics = last_gc = last_retry = last_maintenance = ticks_ms()
    last_tick = ticks_ms()
    busy_ms = total_ms = 0
    stable = False
    reboot_day = None
    update_schedule = UpdateSchedule()
    errors = {}
    def guarded(label, function):
        success = True
        try:
            function()
        except Exception as exc:
            success = False
            stamp = ticks_ms()
            if label not in errors or ticks_diff(stamp, errors[label]) >= 60000:
                state.event(label + "_error", str(exc))
                errors[label] = stamp
        # A peripheral call which exceeded its nominal deadline cannot defer
        # closure until the next loop iteration.
        if not controller.tick(ticks_ms(), epoch(), bool(app.ntp and app.ntp.synced)):
            machine.reset()
        return success
    def finish_boot_trial():
        nonlocal stable
        stable = mark_stable()
    while True:
        begin = ticks_ms()
        elapsed = max(0, ticks_diff(begin, last_tick))
        last_tick = begin
        app.uptime_ms += elapsed
        total_ms += elapsed
        if not controller.tick(begin, epoch(), bool(app.ntp and app.ntp.synced)):
            machine.reset()
        # Feed only here, after all previous slices returned and safety passed.
        if wdt:
            wdt.feed()
        guarded("schedules", controller.schedules)
        if app.sensors:
            if not controller.any_open():
                guarded("sensors", lambda: app.sensors.poll(ticks_ms()))
            if app.sensors.calibration_changed:
                app.sensors.calibration_changed = False
                guarded("calibration_save", app.commit_calibration)
            guarded("moisture", lambda: controller.moisture(app.sensors.readings, ticks_ms()))
            if app.environment and not controller.any_open():
                guarded("environment", lambda: app.environment.poll(ticks_ms()))
            guarded("history", lambda: state.sample(app.sensors.readings, ticks_ms(), epoch(),
                controller.synced, allow_flash=controller.idle()))
        if app.wifi:
            guarded("wifi", lambda: app.wifi.poll(ticks_ms(), controller.any_open()))
        if app.ntp and app.wifi:
            guarded("ntp", lambda: app.ntp.poll(ticks_ms(), app.wifi.connected, controller.any_open()))
        guarded("web", lambda: app.server.poll(ticks_ms()))
        if app.updater and controller.idle():
            guarded("update", lambda: app.updater.poll(ticks_ms()))
            if app.updater.reboot_required:
                controller.stop_all("update complete")
                machine.reset()
        if controller.paused and app.reboot_at is None and not (app.updater and app.updater.status["busy"]) and not (app.sensors and app.sensors.calibration) and not app.server.health().get("active_uploads", 0):
            controller.paused = False
        if led:
            guarded("led", lambda: led.poll(ticks_ms(), watering=controller.any_open(),
                grace=controller.grace_ms > 0, wifi=bool(app.wifi and app.wifi.connected),
                web=app.server.health()["listening"],
                updating=bool(app.updater and app.updater.status["busy"]),
                hotspot=bool(app.wifi and app.wifi.portal.active)))
        if app.reboot_at is not None and ticks_diff(ticks_ms(), app.reboot_at) >= 1000:
            controller.stop_all("reboot")
            machine.reset()
        if not stable and controller.idle() and app.uptime_ms >= 60000 and not controller.inhibited and not (app.updater and app.updater.status["busy"]):
            guarded("boot_trial", finish_boot_trial)
        if stable and controller.idle():
            guarded("auto_update", lambda: update_schedule.poll(app, ticks_ms()))
        now = ticks_ms()
        if ticks_diff(now, last_metrics) >= 5000:
            app.cpu_percent = min(100, round(100 * busy_ms / max(1, total_ms), 1))
            busy_ms = total_ms = 0
            last_metrics = now
            app.mem = {"mem_free": gc.mem_free(), "mem_alloc": gc.mem_alloc()}
            try:
                import esp32
                heaps = esp32.idf_heap_info(esp32.HEAP_DATA)
                app.mem["idf_free"] = sum(h[1] for h in heaps)
                app.mem["idf_largest"] = max(h[2] for h in heaps)
            except (ImportError, AttributeError, ValueError):
                pass
        if ticks_diff(now, last_gc) >= 1000:
            gc.collect()
            last_gc = now
        if controller.idle() and ticks_diff(now, last_retry) >= 60000:
            last_retry = now
            if app.sensors is None:
                guarded("sensor_recovery", init_sensors)
            if app.wifi is None:
                try:
                    app.wifi = WifiManager(config, event=state.event)
                    app.ntp = NTPClient(config, event=state.event, resolver=app.wifi.dns)
                    if app.updater:
                        app.updater.resolver = app.wifi.dns.resolve
                except Exception as exc:
                    state.event("wifi_recovery", str(exc))
        if controller.synced and controller.idle() and ticks_diff(now, last_maintenance) >= 60000:
            last_maintenance = now
            local = epoch() + app.settings["tz_offset_min"] * 60
            hour, day = (local // 3600) % 24, local // 86400
            if app.uptime_ms >= 3600000 and hour == getattr(config, "DAILY_REBOOT_HOUR", None) and day != reboot_day:
                reboot_day = day
                app.reboot()
        if controller.idle():
            guarded("events_flush", state.flush_events)
        if controller.synced and controller.idle():
            guarded("history_prune", lambda: state.prune(epoch()))
        duration = max(0, ticks_diff(ticks_ms(), begin))
        busy_ms += duration
        app.loop_max_ms = max(app.loop_max_ms, duration)
        time.sleep_ms(10)
