"""Versioned settings with bounded validation and atomic full replacements."""
from persistence import read_json, atomic_json, exists

SCHEMA_VERSION = 2
MAP_KEYS = ("zone_thresholds", "zone_durations", "zone_wet_targets")
ZONE_MAPS = ("zone_channels", "zone_valves", "zone_calibration")
EDITABLE = ("supplemental_duration_sec", "min_supplemental_interval_sec",
            "post_daily_lockout_sec", "soak_recheck_sec", "max_water_cycles",
            "weather_zip", "tz_offset_min", "daily_enabled",
            "moisture_watering_enabled") + MAP_KEYS

def clone(value):
    if isinstance(value, dict):
        return {key: clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clone(item) for item in value]
    return value

def integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("%s must be an integer from %s to %s" % (label, low, high))
    return value

def name(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 48:
        raise ValueError("Names must contain 1-48 UTF-8 bytes")
    if any(ord(c) < 32 for c in value):
        raise ValueError("Names cannot contain control characters")
    return value

def boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(label + " must be true or false")

def defaults(config):
    def get(key, fallback):
        return getattr(config, key, fallback)
    valves = clone(get("VALVES", []))
    zones = get("ZONES", [])
    hardware = {
        "i2c_scl_pin": get("I2C_SCL_PIN", 22), "i2c_sda_pin": get("I2C_SDA_PIN", 21),
        "ads1115_addresses": [get("ADS1115_ADDRESS", 72)], "valves": valves,
        "zone_channels": {z["name"]: z["channel"] for z in zones},
        "zone_valves": {z["name"]: z.get("valves", [z.get("valve", valves[0]["name"] if valves else "")]) for z in zones},
        "zone_calibration": {z["name"]: {"dry_raw": z.get("dry_raw", 17500), "wet_raw": z.get("wet_raw", 8000)} for z in zones},
        "flow_meter_pins": [], "rain_sensor_pin": None,
    }
    return {
        "schema_version": SCHEMA_VERSION, "hardware": hardware,
        "schedules": [{"id": 1, "hour": get("DAILY_WATER_HOUR", 6), "minute": get("DAILY_WATER_MINUTE", 0),
                       "duration_sec": get("DAILY_WATER_DURATION_SEC", 300), "enabled": True,
                       "valve_names": [v["name"] for v in valves], "zone_names": []}],
        "supplemental_duration_sec": get("SUPPLEMENTAL_WATER_DURATION_SEC", 60),
        "min_supplemental_interval_sec": get("MIN_SUPPLEMENTAL_INTERVAL_SEC", 7200),
        "post_daily_lockout_sec": get("POST_DAILY_LOCKOUT_SEC", 14400),
        "zone_thresholds": {z["name"]: z.get("threshold_percent", 30) for z in zones},
        "zone_durations": {}, "zone_wet_targets": {}, "soak_recheck_sec": 30,
        "max_water_cycles": 3, "weather_zip": "", "tz_offset_min": get("TZ_OFFSET_SEC", -18000) // 60,
        "daily_enabled": True, "moisture_watering_enabled": True,
    }

def migrate(data, config):
    if not isinstance(data, dict):
        raise ValueError("Settings must be an object")
    data = clone(data)
    if any(k.lower() in ("wifi", "wifi_password", "wifi_ssid", "password", "credentials", "ssid") for k in data):
        raise ValueError("WiFi credentials belong only in wifi.json")
    if data.get("schema_version", 1) > SCHEMA_VERSION:
        raise ValueError("Settings require newer firmware; preserved on disk")
    base = defaults(config)
    for key, value in base.items():
        if key not in data and key != "hardware":
            data[key] = value
    hw = data.setdefault("hardware", base["hardware"])
    if not isinstance(hw, dict):
        raise ValueError("hardware must be an object")
    if "valves" not in hw and "valve_pin" in hw:
        hw["valves"] = [{"name": "valve1", "pin": hw.pop("valve_pin"), "active_high": hw.pop("valve_active_high", True)}]
    if "ads1115_addresses" not in hw:
        hw["ads1115_addresses"] = [hw.pop("ads1115_address", getattr(config, "ADS1115_ADDRESS", 72))]
    for key, value in base["hardware"].items():
        hw.setdefault(key, value)
    names = [v["name"] for v in hw["valves"]]
    for v in hw["valves"]:
        v.setdefault("active_high", True)
        v.setdefault("flow_meter_pin", None)
        v.setdefault("watering_mode", "duration")
        v.setdefault("target_volume_l", None)
    for zone in hw["zone_channels"]:
        mappings = hw["zone_valves"].get(zone, names[:1])
        if isinstance(mappings, str):
            mappings = [mappings]
        hw["zone_valves"][zone] = [v for v in mappings if v in names]
        hw["zone_calibration"].setdefault(zone, {"dry_raw": 17500, "wet_raw": 8000})
        data["zone_thresholds"].setdefault(zone, 30)
    for key in MAP_KEYS:
        data[key] = {z: value for z, value in data[key].items() if z in hw["zone_channels"]}
    for key in ("zone_valves", "zone_calibration"):
        hw[key] = {z: value for z, value in hw[key].items() if z in hw["zone_channels"]}
    if "daily_water_hour" in data or "daily_hour" in data:
        data["schedules"] = [{"id": 1, "hour": data.pop("daily_water_hour", data.pop("daily_hour", 6)),
                              "minute": data.pop("daily_water_minute", data.pop("daily_minute", 0)),
                              "duration_sec": data.pop("daily_water_duration_sec", data.pop("daily_duration_sec", 300)),
                              "enabled": True, "valve_names": names, "zone_names": []}]
    for schedule in data["schedules"]:
        schedule.setdefault("valve_names", names[:])
        schedule.setdefault("zone_names", [])
        schedule["valve_names"] = [v for v in schedule["valve_names"] if v in names]
        schedule["zone_names"] = [z for z in schedule["zone_names"] if z in hw["zone_channels"]]
    data["schema_version"] = SCHEMA_VERSION
    return data

def pin_rules(board="esp32"):
    if board == "esp32s3":
        outputs = [1, 2] + list(range(4, 19)) + [21, 38, 39, 40, 41, 42, 47, 48]
        return outputs, outputs, 49
    outputs = [4, 5, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33]
    return outputs, outputs + [34, 35, 36, 39], 40

def validate(data, config):
    if not isinstance(data, dict):
        raise ValueError("Settings must be an object")
    allowed = ("schema_version", "hardware", "schedules") + EDITABLE
    if any(key not in allowed for key in data):
        raise ValueError("Unknown settings field")
    hw = data["hardware"]
    if not isinstance(hw, dict) or any(key not in ("i2c_scl_pin", "i2c_sda_pin", "ads1115_addresses", "valves", "zone_channels", "zone_valves", "zone_calibration", "flow_meter_pins", "rain_sensor_pin") for key in hw):
        raise ValueError("Unknown hardware field")
    outputs, inputs, _ = pin_rules(getattr(config, "BOARD", "esp32"))
    used = {}
    led = getattr(config, "STATUS_LED_PIN", None)
    if led is not None:
        used[led] = "status LED"
    def pin(value, role, output=True, optional=False):
        if optional and value is None:
            return
        integer(value, 0, 48, role)
        if value not in (outputs if output else inputs):
            raise ValueError("Unsafe GPIO %s for %s on this board" % (value, role))
        if value in used:
            raise ValueError("GPIO %s conflicts with %s" % (value, used[value]))
        used[value] = role
    pin(hw["i2c_scl_pin"], "I2C SCL")
    pin(hw["i2c_sda_pin"], "I2C SDA")
    addresses = hw["ads1115_addresses"]
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 4 or len(set(addresses)) != len(addresses):
        raise ValueError("Choose 1-4 unique ADS1115 addresses")
    for address in addresses:
        integer(address, 72, 75, "ADS address")
    valves = hw["valves"]
    if not isinstance(valves, list) or len(valves) > 8:
        raise ValueError("At most 8 valves are supported")
    names = []
    for valve in valves:
        n = name(valve["name"])
        if n in names:
            raise ValueError("Duplicate valve name")
        names.append(n)
        pin(valve["pin"], n)
        boolean(valve["active_high"], "active_high")
        pin(valve.get("flow_meter_pin"), n + " flow", False, True)
        if valve.get("watering_mode", "duration") not in ("duration", "volume"):
            raise ValueError("Invalid watering_mode")
        volume = valve.get("target_volume_l")
        if volume is not None and (isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 < volume <= 10000):
            raise ValueError("Invalid target volume")
    if not isinstance(hw["flow_meter_pins"], list) or len(hw["flow_meter_pins"]) > 8:
        raise ValueError("At most 8 standalone flow pins")
    for value in hw["flow_meter_pins"]:
        pin(value, "flow meter", False)
    pin(hw["rain_sensor_pin"], "rain sensor", False, True)
    channels = hw["zone_channels"]
    if not isinstance(channels, dict) or len(channels) > 16:
        raise ValueError("At most 16 zones are supported")
    for key in ("zone_valves", "zone_calibration"):
        if not isinstance(hw[key], dict) or set(hw[key]) != set(channels):
            raise ValueError("Zone map does not match configured zones: " + key)
    maximum = getattr(config, "MAX_VALVE_OPEN_SEC", 600)
    for zone, channel in channels.items():
        name(zone)
        integer(channel, 0, len(addresses) * 4 - 1, "ADS channel")
        mappings = hw["zone_valves"][zone]
        if not isinstance(mappings, list) or len(mappings) > 8 or len(set(mappings)) != len(mappings) or any(v not in names for v in mappings):
            raise ValueError("Invalid zone valve mapping")
        cal = hw["zone_calibration"][zone]
        for point in ("dry_raw", "wet_raw"):
            integer(cal[point], 0, 32767, point)
        if abs(cal["dry_raw"] - cal["wet_raw"]) < 100:
            raise ValueError("Dry and wet calibration must differ by at least 100")
        threshold = data["zone_thresholds"].get(zone, 30)
        integer(threshold, 0, 99, "Dry threshold")
        integer(data["zone_wet_targets"].get(zone, min(100, threshold + 10)), threshold + 1, 100, "Wet target")
        integer(data["zone_durations"].get(zone, data["supplemental_duration_sec"]), 1, maximum, "Zone duration")
    for key in MAP_KEYS:
        if not isinstance(data[key], dict) or any(z not in channels for z in data[key]):
            raise ValueError("Unknown zone in " + key)
    for key, low, high in (("supplemental_duration_sec", 1, maximum),
                           ("min_supplemental_interval_sec", 0, 604800),
                           ("post_daily_lockout_sec", 0, 604800),
                           ("soak_recheck_sec", 1, 86400), ("max_water_cycles", 1, 10),
                           ("tz_offset_min", -720, 840)):
        integer(data[key], low, high, key)
    for key in ("daily_enabled", "moisture_watering_enabled"):
        boolean(data[key], key)
    if not isinstance(data["weather_zip"], str) or len(data["weather_zip"]) > 32:
        raise ValueError("weather_zip is too long")
    schedules = data["schedules"]
    if not isinstance(schedules, list) or len(schedules) > 20:
        raise ValueError("At most 20 schedules are supported")
    ids = []
    for s in schedules:
        integer(s["id"], 1, 1000000, "Schedule id")
        if s["id"] in ids:
            raise ValueError("Duplicate schedule id")
        ids.append(s["id"])
        integer(s["hour"], 0, 23, "Hour")
        integer(s["minute"], 0, 59, "Minute")
        integer(s["duration_sec"], 1, maximum, "Duration")
        boolean(s["enabled"], "enabled")
        for key, allowed in (("valve_names", names), ("zone_names", channels)):
            if not isinstance(s[key], list) or len(s[key]) > len(allowed) or any(n not in allowed for n in s[key]) or len(set(s[key])) != len(s[key]):
                raise ValueError("Invalid schedule targets")
    return data

class SettingsStore:
    def __init__(self, config, path="settings.json"):
        self.config, self.path = config, path
        saved = read_json(path)
        if saved is None and (exists(path) or exists(path + ".prev")):
            raise ValueError("Settings damaged; outputs held closed. Restore configuration over USB.")
        self.data = validate(migrate(saved if saved is not None else {}, config), config)
        if saved != self.data:
            atomic_json(path, self.data)

    def save(self, data):
        return self.save_owned(clone(data))

    def save_owned(self, candidate):
        """Commit a private candidate; caller transfers its mutable references."""
        validate(candidate, self.config)
        atomic_json(self.path, candidate)
        self.data = candidate
        return candidate

    def zones(self):
        data, hw = self.data, self.data["hardware"]
        return [dict(name=z, channel=ch, valves=hw["zone_valves"][z],
                     threshold=data["zone_thresholds"].get(z, 30),
                     wet_target=data["zone_wet_targets"].get(z, min(100, data["zone_thresholds"].get(z, 30) + 10)),
                     water_duration_sec=data["zone_durations"].get(z, data["supplemental_duration_sec"]),
                     dry_raw=hw["zone_calibration"][z]["dry_raw"], wet_raw=hw["zone_calibration"][z]["wet_raw"])
                for z, ch in hw["zone_channels"].items()]
