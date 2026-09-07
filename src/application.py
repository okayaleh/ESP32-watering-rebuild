"""Dashboard facade. Validation precedes atomic writes and hardware changes."""
from compat import ticks_ms, ticks_diff, epoch
from settings_store import clone, validate, migrate, EDITABLE, MAP_KEYS, ZONE_MAPS, pin_rules

class Application:
    def __init__(self, config, store, controller, state):
        self.config, self.store, self.controller, self.state = config, store, controller, state
        self.settings = store.data
        self.sensors = self.environment = self.wifi = self.ntp = self.updater = self.server = None
        self.boot_ms = ticks_ms()
        self.uptime_ms = 0
        self.reboot_at = None
        self.mem = {}
        self.cpu_percent = 0
        self.loop_max_ms = 0
        self.scan_pending = False
        self.calibration_before = None

    def status(self):
        now = ticks_ms()
        network = self.wifi_status()
        readings = list(self.sensors.readings.values()) if self.sensors else []
        for reading in readings:
            reading["threshold"] = self.settings["zone_thresholds"].get(reading.get("name"), 30)
        result = {
            "moisture": readings,
            "valves": {n: {"open": v.is_open, "seconds_open": max(0, ticks_diff(now, v.opened_ms)) // 1000 if v.is_open else 0,
                           "last_close_reason": v.last_close_reason, "cooldowns_ms": self.controller.cooldowns[n]}
                       for n, v in self.controller.valves.items()},
            "uptime_sec": self.uptime_ms // 1000, "now": epoch(),
            "startup_grace_left": (self.controller.grace_ms + 999) // 1000,
            "time_synced": self.controller.synced, "wifi_connected": bool(network.get("connected", False)),
            "lan_ok": network.get("lan_ok"), "wifi": network,
            "env": self.environment.readings if self.environment else {},
            "update": self.updater.status if self.updater else {"error": "Updater unavailable"},
            "cpu_percent": self.cpu_percent, "loop_max_ms": self.loop_max_ms,
            "safety_fault": self.controller.inhibited, "queued": len(self.controller.queue),
            "session": self.controller.session,
            "web": self.server.health() if self.server else {},
        }
        result.update(self.mem)
        return result

    def history(self, hours=None):
        return self.state.history(hours, epoch(), ticks_ms())

    def events(self):
        return self.state.events_ring

    def _save(self, candidate, reboot=False):
        if self.sensors and getattr(self.sensors, "calibration", None):
            raise RuntimeError("Finish calibration before changing settings")
        validate(candidate, self.config)
        # Configuration changes cancel a pending moisture session. Hardware
        # changes also close the OLD pin objects before saving new pin numbers.
        if not self.controller.stop_all("configuration changed"):
            raise RuntimeError("Cannot change settings while an output cannot close")
        self.controller._persist()
        if reboot:
            self.controller.paused = True
        self.settings = self.store.save_owned(candidate)
        self.controller.settings = self.settings
        if self.sensors and not reboot:
            self.sensors.configure(self.settings)
        if reboot:
            self.reboot()
        return {"ok": True, "reboot": reboot}

    def save_settings(self, body, replace=False):
        if replace:
            return self._save(migrate(body, self.config), True)
        if any(key not in EDITABLE for key in body):
            raise ValueError("Unknown watering setting")
        candidate = clone(self.settings)
        candidate.update(clone(body))
        return self._save(candidate)

    def zones(self):
        return self.store.zones()

    def save_zones(self, body):
        zones = body.get("zones")
        if not isinstance(zones, list) or len(zones) > 16:
            raise ValueError("Expected up to 16 zones")
        renames = body.get("renames", {})
        if not isinstance(renames, dict) or len(set(renames.values())) != len(renames):
            raise ValueError("Invalid renames")
        old = self.settings
        renames = dict(renames)
        for zone in zones:
            if zone.get("old_name") and zone["old_name"] != zone["name"]:
                if zone["old_name"] in renames and renames[zone["old_name"]] != zone["name"]:
                    raise ValueError("Conflicting zone renames")
                renames[zone["old_name"]] = zone["name"]
        if len(set(renames.values())) != len(renames):
            raise ValueError("Conflicting zone renames")
        old_hw = old["hardware"]
        candidate = clone(old)
        hw = candidate["hardware"]
        for key in MAP_KEYS:
            candidate[key] = {}
        for key in ZONE_MAPS:
            hw[key] = {}
        for zone in zones:
            n = zone["name"]
            if n in hw["zone_channels"]:
                raise ValueError("Duplicate zone name")
            previous = zone.get("old_name", next((key for key, value in renames.items() if value == n), n))
            if previous != n and previous not in old_hw["zone_channels"]:
                raise ValueError("Unknown zone being renamed")
            hw["zone_channels"][n] = zone["channel"]
            hw["zone_valves"][n] = clone(zone.get("valves", old_hw["zone_valves"].get(previous, [])))
            calibration = clone(old_hw["zone_calibration"].get(previous, {"dry_raw": 17500, "wet_raw": 8000}))
            for key in ("dry_raw", "wet_raw"):
                if key in zone:
                    calibration[key] = zone[key]
            hw["zone_calibration"][n] = calibration
            for key, field in (("zone_thresholds", "threshold"), ("zone_durations", "water_duration_sec"), ("zone_wet_targets", "wet_target")):
                value = zone.get(field, old[key].get(previous))
                if value is not None:
                    candidate[key][n] = value
        for s in candidate["schedules"]:
            s["zone_names"] = [renames.get(z, z) for z in s["zone_names"] if renames.get(z, z) in hw["zone_channels"]]
        return self._save(candidate)

    def save_valves(self, body):
        candidate = clone(self.settings)
        valves = clone(body["valves"])
        if not isinstance(valves, list) or len(valves) > 8:
            raise ValueError("Expected up to 8 valves")
        renames = body.get("renames", {})
        if not isinstance(renames, dict) or len(set(renames.values())) != len(renames):
            raise ValueError("Invalid valve renames")
        names = [v["name"] for v in valves]
        for v in valves:
            v.setdefault("active_high", True)
            v.setdefault("flow_meter_pin", None)
            v.setdefault("watering_mode", "duration")
            v.setdefault("target_volume_l", None)
        candidate["hardware"]["valves"] = valves
        if "flow_meter_pins" in body:
            candidate["hardware"]["flow_meter_pins"] = clone(body["flow_meter_pins"])
        for z, items in candidate["hardware"]["zone_valves"].items():
            candidate["hardware"]["zone_valves"][z] = [renames.get(n, n) for n in items if renames.get(n, n) in names]
        for s in candidate["schedules"]:
            s["valve_names"] = [renames.get(n, n) for n in s["valve_names"] if renames.get(n, n) in names]
        validate(candidate, self.config)
        if not self.controller.stop_all("valve configuration changed"):
            raise RuntimeError("Output closure failed")
        # Duplicate, rather than move, cooldown records before changing settings.
        # A power cut between these two writes leaves BOTH names protected.
        journal = self.controller.ledger["valves"]
        for before, after in renames.items():
            if before not in self.controller.valves or after not in names:
                raise ValueError("Invalid valve rename")
            if before in journal:
                previous = journal.get(after, {})
                for reason, stamp in journal[before].items():
                    other = previous.get(reason)
                    previous[reason] = max(stamp, other) if isinstance(stamp, int) and isinstance(other, int) else None
                journal[after] = previous
        self.controller._persist()
        return self._save(candidate, True)

    def save_hardware(self, body):
        candidate = clone(self.settings)
        hw = body.get("hardware", body)
        if any(k not in candidate["hardware"] for k in hw):
            raise ValueError("Unknown hardware setting")
        candidate["hardware"].update(clone(hw))
        return self._save(candidate, True)

    def save_schedules(self, body):
        candidate = clone(self.settings)
        candidate["schedules"] = clone(body if isinstance(body, list) else body["schedules"])
        return self._save(candidate)

    def pinmap(self):
        hw = self.settings["hardware"]
        outputs, inputs, count = pin_rules(getattr(self.config, "BOARD", "esp32"))
        roles = {hw["i2c_scl_pin"]: "I2C SCL", hw["i2c_sda_pin"]: "I2C SDA", getattr(self.config, "STATUS_LED_PIN", None): "Status LED"}
        for v in hw["valves"]:
            roles[v["pin"]] = "Valve: " + v["name"]
            if v.get("flow_meter_pin") is not None:
                roles[v["flow_meter_pin"]] = "Flow: " + v["name"]
        for p in hw["flow_meter_pins"]:
            roles[p] = "Flow meter"
        if hw["rain_sensor_pin"] is not None:
            roles[hw["rain_sensor_pin"]] = "Rain sensor"
        return {"board": getattr(self.config, "BOARD", "esp32"),
                "pins": [{"pin": p, "role": roles.get(p, ""), "output_safe": p in outputs,
                          "input_safe": p in inputs, "reserved": p not in inputs} for p in range(count)],
                "channels": [{"zone": n, "channel": c, "address": hw["ads1115_addresses"][c // 4], "input": c % 4} for n, c in hw["zone_channels"].items()]}

    def water(self, valve=None, zone=None, all_valves=False, duration=None):
        if zone is not None:
            if zone not in self.settings["hardware"]["zone_valves"]:
                raise ValueError("Unknown zone")
            names = self.settings["hardware"]["zone_valves"][zone]
            if duration is None:
                duration = self.settings["zone_durations"].get(zone)
        elif all_valves:
            names = list(self.controller.valves)
        else:
            names = [valve] if valve else list(self.controller.valves)[:1]
        return self.controller.enqueue(names, duration if duration is not None else self.settings["supplemental_duration_sec"])

    def open_valve(self, name):
        return self.water(valve=name, duration=getattr(self.config, "MAX_VALVE_OPEN_SEC", 600))

    def close_valve(self, name):
        self.controller.close(name)
        return {"ok": True}

    def stop_all(self):
        if not self.controller.stop_all("emergency stop"):
            raise RuntimeError("Output closure failed")
        self.controller._persist()
        return {"ok": True}

    def scan(self):
        if not self.sensors:
            raise RuntimeError("I2C unavailable")
        if self.scan_pending and not self.sensors.scan_requested:
            self.scan_pending = False
            return {"found": self.sensors.found, "busy": False, "at": epoch()}
        if not self.scan_pending:
            self.scan_pending = True
            self.sensors.request_scan()
        return {"found": self.sensors.found, "busy": True}

    def calibration(self, body=None):
        if not self.sensors:
            raise RuntimeError("I2C unavailable")
        if body is not None:
            if not self.controller.idle():
                raise RuntimeError("Wait for watering to finish before calibration")
            self.sensors.start_calibration(body["zone"], body["point"], ticks_ms())
            self.calibration_before = (body["zone"], clone(self.settings["hardware"]["zone_calibration"][body["zone"]]))
            self.controller.paused = True
        return self.sensors.calibration_status()

    def commit_calibration(self):
        candidate = clone(self.settings)
        if self.calibration_before:
            zone, previous = self.calibration_before
            self.settings["hardware"]["zone_calibration"][zone] = previous
        self.calibration_before = None
        try:
            self._save(candidate)
        except Exception:
            if self.sensors.calibration_result is not None:
                self.sensors.calibration_result["error"] = "Calibration could not be saved; previous values retained"
            raise

    def wifi_status(self):
        return self.wifi.status() if self.wifi else {"connected": False, "ssid": "", "error": "WiFi unavailable"}

    def save_wifi(self, body):
        if not self.wifi:
            raise RuntimeError("WiFi unavailable")
        if not self.controller.stop_all("WiFi configuration"):
            raise RuntimeError("Output closure failed")
        self.controller._persist()
        self.wifi.save_credentials(body["ssid"], body["password"])
        self.reboot()
        return {"ok": True, "reboot": True}

    def update_action(self, action):
        if not self.updater:
            raise RuntimeError("Updater unavailable")
        if not self.controller.idle():
            raise RuntimeError("Wait for watering to finish before updating")
        self.controller.paused = True
        try:
            if action == "check":
                self.updater.request_check()
            else:
                self.updater.request_install()
        except Exception:
            self.controller.paused = False
            raise
        return {"ok": True, "queued": True}

    def reboot(self):
        if not self.controller.stop_all("reboot"):
            raise RuntimeError("Output closure failed")
        self.controller._persist()
        self.controller.paused = True
        self.reboot_at = ticks_ms()
        return {"ok": True, "reboot": True}
