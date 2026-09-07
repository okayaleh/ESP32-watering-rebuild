"""Deterministic watering engine, independent of sockets, sensors and wall time.

Only this module may energize valves. Intent is durable before energizing.
An output or storage fault inhibits starts and attempts every close separately.
"""
import time
from compat import ticks_diff
from persistence import read_json, atomic_json, exists

class Controller:
    def __init__(self, settings, valves, config, now_ms=0, ledger_path="watering_state.json", event=None, clock=None):
        self.settings, self.valves, self.config = settings, valves, config
        self.ledger_path = ledger_path
        self.event = event or (lambda *args: None)
        self.clock = clock
        self.maximum = getattr(config, "MAX_VALVE_OPEN_SEC", 600)
        self.grace_ms = getattr(config, "STARTUP_GRACE_SEC", 60) * 1000
        self.sensor_interval_ms = getattr(config, "MOISTURE_CHECK_INTERVAL_SEC", 15) * 1000
        self.last_tick = now_ms
        self.queue, self.session = [], None
        self.inhibited = None
        self.paused = False
        self.close_fault = False
        self.synced = False
        self.now_epoch = 0
        self.ledger = read_json(ledger_path, {"valves": {}, "fired": {}})
        if isinstance(self.ledger, dict) and "valves" not in self.ledger and any(k in self.ledger for k in ("daily", "supplemental")):
            legacy = self.ledger
            self.ledger = {"valves": {}, "fired": {}}
            for old_key, new_key in (("daily", "schedule"), ("supplemental", "moisture")):
                records = legacy.get(old_key, {})
                if isinstance(records, dict):
                    for n, stamp in records.items():
                        if isinstance(stamp, int) and 600000000 <= stamp < 946684800:
                            stamp += 946684800
                        self.ledger["valves"].setdefault(n, {})[new_key] = stamp
        valid_ledger = isinstance(self.ledger, dict) and isinstance(self.ledger.get("valves"), dict) and isinstance(self.ledger.get("fired"), dict)
        if valid_ledger:
            for n, record in self.ledger["valves"].items():
                if not isinstance(n, str) or not isinstance(record, dict) or any(k not in ("schedule", "moisture") or (stamp is not None and (isinstance(stamp, bool) or not isinstance(stamp, int) or not 0 <= stamp <= 4102444800)) for k, stamp in record.items()):
                    valid_ledger = False
            for key, stamp in self.ledger["fired"].items():
                if not isinstance(key, str) or isinstance(stamp, bool) or not isinstance(stamp, int) or not 0 <= stamp <= 70000000:
                    valid_ledger = False
        if not valid_ledger:
            self.ledger = {"valves": {}, "fired": {}}
            self.inhibited = "Invalid watering journal"
        # No valid copy of a pre-existing journal is not a fresh install.
        if read_json(ledger_path) is None and (exists(ledger_path) or exists(ledger_path + ".prev")):
            self.inhibited = "Damaged watering journal"
        self.cooldowns = {}
        for n in valves:
            record = self.ledger["valves"].get(n, {})
            self.cooldowns[n] = {
                "schedule": self._interval("schedule") if "schedule" in record else 0,
                "moisture": self._interval("moisture") if "moisture" in record else 0,
            }
        self._restored_clock = False

    def _interval(self, reason):
        return self.settings["post_daily_lockout_sec" if reason == "schedule" else "min_supplemental_interval_sec"] * 1000

    def _persist(self):
        try:
            atomic_json(self.ledger_path, self.ledger)
        except Exception:
            self.inhibited = "Watering journal could not be saved"
            self.stop_all("storage fault")
            raise

    def _record(self, name, reason):
        category = "schedule" if reason == "schedule" else "moisture"
        self.cooldowns[name][category] = self._interval(category)
        self.ledger["valves"].setdefault(name, {})[category] = self.now_epoch if self.synced else None
        self._persist()

    def any_open(self):
        return any(v.is_open for v in self.valves.values())

    def idle(self):
        return not self.any_open() and not self.queue and self.session is None

    def stop_all(self, reason="stopped"):
        self.queue = []
        self.session = None
        success = True
        for name, valve in self.valves.items():
            was_open, source = valve.is_open, valve.reason
            try:
                valve.close(reason)
                if was_open:
                    category = "schedule" if source == "schedule" else "moisture"
                    self.cooldowns[name][category] = self._interval(category)
                    self.ledger["valves"].setdefault(name, {})[category] = self.now_epoch if self.synced else None
                    self.event("valve_close", name + ": " + reason)
            except Exception as exc:
                success = False
                self.inhibited = "Valve close failed: " + name
                self.event("safety_fault", name + ": " + str(exc))
        self.close_fault = not success
        return success

    def close(self, name):
        if name not in self.valves:
            raise ValueError("Unknown valve")
        # Stop the entire batch/session: a canceled valve must not be reopened
        # by a queued duplicate or a later soak cycle.
        if not self.stop_all("manual stop"):
            raise RuntimeError(self.inhibited)
        self._persist()

    def enqueue(self, names, duration, reason="manual", internal=False):
        if self.inhibited or self.paused or self.grace_ms > 0:
            raise RuntimeError(self.inhibited or "Watering held during startup or maintenance")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 1 <= duration <= self.maximum:
            raise ValueError("Duration must be 1-%s seconds" % self.maximum)
        unique = []
        for name in names:
            if name not in self.valves:
                raise ValueError("Unknown valve: " + str(name))
            if name not in unique:
                unique.append(name)
        if not unique:
            raise ValueError("No valves selected")
        if len(self.queue) + len(unique) > 192:
            raise RuntimeError("Watering queue is full")
        if not internal and self.session:
            # Explicit runs cancel further moisture cycles; running valve
            # completes normally, then manual/scheduled queue proceeds.
            self.session = None
        self.queue.extend((n, duration, reason) for n in unique)
        return {"ok": True, "queued": unique}

    def _restore_clock(self):
        if not self.synced or self._restored_clock:
            return
        self._restored_clock = True
        for name in self.valves:
            for reason, stamp in self.ledger["valves"].get(name, {}).items():
                if reason not in self.cooldowns[name]:
                    continue
                if isinstance(stamp, int) and 0 <= self.now_epoch - stamp <= 604800:
                    self.cooldowns[name][reason] = max(0, self._interval(reason) - (self.now_epoch - stamp) * 1000)
                # Unknown/pre-NTP/future stamps retain conservative countdown.

    def tick(self, now_ms, now_epoch=0, synced=False):
        delta = max(0, ticks_diff(now_ms, self.last_tick))
        self.last_tick = now_ms
        self.now_epoch, self.synced = now_epoch, synced
        self.grace_ms = max(0, self.grace_ms - delta)
        for value in self.cooldowns.values():
            for key in value:
                value[key] = max(0, value[key] - delta)
        self._restore_clock()
        # Check every output even if another output's driver raises.
        for name, valve in self.valves.items():
            try:
                if valve.expired(now_ms, self.maximum):
                    reason = valve.reason
                    valve.close("duration complete")
                    self._record(name, reason)
                    self.event("valve_close", name + ": " + str(reason))
            except Exception as exc:
                self.inhibited = "Safety fault: " + str(exc)
                self.stop_all("safety fault")
        if self.inhibited or self.paused or self.grace_ms:
            return not self.close_fault
        if not self.any_open() and self.queue:
            name, duration, reason = self.queue.pop(0)
            try:
                self._record(name, reason)  # Durable BEFORE GPIO goes active.
                # A real LittleFS journal commit can take hundreds of ms.
                # Begin the requested interval when GPIO is about to energize.
                opened_ms = self.clock() if self.clock else now_ms
                self.valves[name].open(opened_ms, duration, reason)
                self.event("valve_open", name + ": " + reason)
            except Exception as exc:
                self.inhibited = "Start failed: " + str(exc)
                self.stop_all("start fault")
        if self.session and not self.any_open() and not self.queue:
            if self.session["closed_ms"] is None:
                self.session["closed_ms"] = now_ms
        return not self.close_fault

    def schedules(self):
        if not self.synced or not self.settings["daily_enabled"] or self.grace_ms or self.paused or self.inhibited:
            return
        local = self.now_epoch + self.settings["tz_offset_min"] * 60
        hour, minute_of_hour = (local // 3600) % 24, (local // 60) % 60
        minute = local // 60
        for schedule in self.settings["schedules"]:
            key = str(schedule["id"])
            if not schedule["enabled"] or (schedule["hour"], schedule["minute"]) != (hour, minute_of_hour):
                continue
            # Monotonically increasing occurrence key handles backward NTP
            # corrections and reboot during the same scheduled minute.
            previous = self.ledger["fired"].get(key, -1)
            if minute <= previous:
                continue
            names = list(schedule["valve_names"])
            for zone in schedule["zone_names"]:
                for name in self.settings["hardware"]["zone_valves"].get(zone, []):
                    if name not in names:
                        names.append(name)
            if len(self.queue) + len(names) > 192:
                continue
            self.ledger["fired"][key] = minute
            # Bound tombstones to current schedules.
            valid = [str(s["id"]) for s in self.settings["schedules"]]
            self.ledger["fired"] = {k: v for k, v in self.ledger["fired"].items() if k in valid}
            # Flash commits can stall for seconds. During an existing run,
            # queue this occurrence in RAM; the closing/next-start record
            # durably saves the entire ledger before this batch energizes.
            if not self.any_open():
                self._persist()
            if names:
                self.enqueue(names, schedule["duration_sec"], "schedule")

    def moisture(self, readings, now_ms):
        if self.inhibited or self.paused or self.grace_ms or not self.settings["moisture_watering_enabled"]:
            self.session = None
            return
        if self.any_open() or self.queue:
            return
        if self.session:
            session = self.session
            if session["cycles"] >= self.settings["max_water_cycles"]:
                self.session = None
                return
            delay = max(self.sensor_interval_ms, self.settings["soak_recheck_sec"] * 1000)
            closed = session["closed_ms"]
            if closed is None or ticks_diff(now_ms, closed) < delay:
                return
            value = readings.get(session["zone"])
            if not self._fresh(value, now_ms) or ticks_diff(value["updated_ms"], closed) < delay:
                # No stale reading can authorize another cycle. A failed or
                # unplugged sensor terminates the session rather than looping.
                if not self._fresh(value, now_ms):
                    self.session = None
                return
            threshold = self.settings["zone_thresholds"].get(session["zone"], 30)
            target = self.settings["zone_wet_targets"].get(session["zone"], min(100, threshold + 10))
            if value["percent"] >= target:
                self.session = None
                return
            session["cycles"] += 1
            session["closed_ms"] = None
            self.enqueue(session["valves"], session["duration"], "moisture", True)
            return
        for zone, names in self.settings["hardware"]["zone_valves"].items():
            value = readings.get(zone)
            if not self._fresh(value, now_ms) or value["percent"] >= self.settings["zone_thresholds"].get(zone, 30):
                continue
            runnable = [n for n in names if not any(self.cooldowns[n].values())]
            if not runnable:
                continue
            duration = self.settings["zone_durations"].get(zone, self.settings["supplemental_duration_sec"])
            self.session = {"zone": zone, "valves": runnable, "duration": duration, "cycles": 1, "closed_ms": None}
            self.enqueue(runnable, duration, "moisture", True)
            return

    def _fresh(self, value, now_ms):
        return bool(value and value.get("percent") is not None and not value.get("error")
                    and isinstance(value.get("updated_ms"), int)
                    and 0 <= ticks_diff(now_ms, value["updated_ms"]) <= max(60000, self.sensor_interval_ms * 2))
