"""Small, synchronous API router. All hardware work is queued by the app.

The transport owns byte limits, streaming JSON and multipart uploads. This
module owns endpoint contracts only, making them testable without hardware.
"""
try:
    import ujson as json
except ImportError:
    import json

from web import Response, JsonArray

ROUTES = {
    "/api/status": "GET", "/api/history": "GET", "/api/events": "GET",
    "/api/settings": "GET POST", "/api/schedules": "GET POST",
    "/api/zones": "GET POST", "/api/valves": "GET POST",
    "/api/hardware": "GET POST", "/api/pinmap": "GET",
    "/api/i2c/scan": "GET", "/api/calibrate": "GET POST",
    "/api/config/export": "GET", "/api/config/import": "POST",
    "/api/wifi": "GET POST", "/api/valve": "POST",
    "/api/water/trigger": "POST", "/api/zone/trigger": "POST",
    "/api/water/all": "POST", "/api/water/stop": "POST",
    "/api/update/check": "POST", "/api/update/apply": "POST",
    "/api/reboot": "POST",
}
PUBLIC_SETTINGS = ("schema_version", "hardware", "schedules", "supplemental_duration_sec",
                   "min_supplemental_interval_sec", "post_daily_lockout_sec",
                   "zone_thresholds", "zone_durations", "zone_wet_targets",
                   "soak_recheck_sec", "max_water_cycles", "weather_zip",
                   "tz_offset_min", "daily_enabled", "moisture_watering_enabled")


def _public_settings(settings):
    # Only the documented runtime schema belongs in a portable backup.
    # Values remain references so a full configuration is never deep-copied.
    return {key: settings[key] for key in PUBLIC_SETTINGS if key in settings}


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _name(value, required=False):
    if value is None or value == "":
        if required:
            raise ValueError("A name is required")
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Names must be strings of at most 64 characters")
    return value


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(label + " must be an integer")
    if isinstance(value, str) and len(value) > 12:
        raise ValueError(label + " is too long")
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(label + " must be an integer")
    if not minimum <= number <= maximum:
        raise ValueError(label + " is out of range")
    return number


def _entries(value, label, required, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("Expected a " + label + " list with at most " + str(limit) + " entries")
    for entry in value:
        if not isinstance(entry, dict) or any(key not in entry for key in required):
            raise ValueError("Invalid " + label + " entry")
    return value


class APIRouter:
    def __init__(self, app):
        self.app = app

    def __call__(self, method, path, query=None, body=None, headers=None):
        return self.dispatch(method, path, query, body, headers)

    def dispatch(self, method, path, query=None, body=None, headers=None):
        try:
            query = _object(query or {})
            if isinstance(body, (bytes, str)):
                try:
                    body = json.loads(body) if body else {}
                except (ValueError, TypeError):
                    raise ValueError("Invalid JSON body")
            if body is None:
                body = {}
            return self._dispatch(method, path, query, body)
        except ValueError as exc:
            return Response({"ok": False, "error": str(exc)}, status=400)
        except (KeyError, TypeError, AttributeError):
            return Response({"ok": False, "error": "Request contains missing or invalid fields"}, status=400)
        except RuntimeError as exc:
            return Response({"ok": False, "error": str(exc)}, status=409)
        except OSError:
            return Response({"ok": False, "error": "Storage or device unavailable; change was not confirmed"}, status=503)
        except Exception:
            # Never echo input or exception internals: credentials can occur in
            # a failed wifi request. Runtime records unexpected handler errors.
            return Response({"ok": False, "error": "Unable to complete request"}, status=500)

    def _dispatch(self, method, path, query, body):
        app = self.app
        allowed = ROUTES.get(path)
        if allowed is None:
            if not path.startswith("/api/"):
                return Response({}, status=302, headers={"Location": "/"})
            return Response({"ok": False, "error": "Endpoint not found"}, status=404)
        if method not in allowed.split():
            return Response({"ok": False, "error": "Method not allowed"}, status=405,
                            headers={"Allow": allowed.replace(" ", ", ")})
        if method == "GET":
            if path == "/api/status":
                return app.status()
            if path == "/api/history":
                hours = query.get("hours")
                if hours is not None:
                    hours = min(336, _integer(hours, "hours", 1, 1000000))
                return JsonArray(app.history(hours), skip_none=True, history_records=True)
            if path == "/api/events":
                return app.events()
            if path == "/api/settings":
                return _public_settings(app.settings)
            if path == "/api/schedules":
                return app.settings.get("schedules", [])
            if path == "/api/zones":
                return app.zones()
            if path == "/api/valves":
                return app.settings.get("hardware", {}).get("valves", [])
            if path == "/api/hardware":
                return app.settings.get("hardware", {})
            if path == "/api/pinmap":
                return app.pinmap()
            if path == "/api/i2c/scan":
                return app.scan()
            if path == "/api/calibrate":
                return app.calibration()
            if path == "/api/wifi":
                # An additional boundary prevents an accidental facade change
                # from exposing secrets in the public endpoint.
                value = app.wifi_status()
                return {k: v for k, v in value.items()
                        if k in ("ssid", "connected", "ip", "rssi", "lan_ok",
                                 "rescue_ap", "ap_active", "ap_ssid", "status",
                                 "state", "last_error", "gateway_health",
                                 "reconnect_failures", "portal")}
            if path == "/api/config/export":
                return Response(_public_settings(app.settings), headers={
                    "Content-Disposition": 'attachment; filename="planter-settings.json"'})
        result = None
        if path == "/api/schedules":
            schedules = body.get("schedules") if isinstance(body, dict) else body
            _entries(schedules, "schedules", ("id", "hour", "minute", "duration_sec"), 20)
            result = app.save_schedules(body)
        elif path in ("/api/settings", "/api/config/import"):
            if path.endswith("/import") and not isinstance(_object(body).get("hardware"), dict):
                raise ValueError("Configuration backup must contain hardware settings")
            result = app.save_settings(_object(body), replace=path.endswith("/import"))
        elif path == "/api/zones":
            _entries(_object(body).get("zones"), "zones", ("name", "channel"), 16)
            result = app.save_zones(_object(body))
        elif path == "/api/valves":
            _entries(_object(body).get("valves"), "valves", ("name", "pin"), 8)
            result = app.save_valves(_object(body))
        elif path == "/api/hardware":
            value = _object(body)
            if "hardware" in value:
                _object(value["hardware"])
            result = app.save_hardware(_object(body))
        elif path == "/api/wifi":
            value = _object(body)
            ssid = value.get("ssid")
            password = value.get("password", "")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
                raise ValueError("SSID must contain 1 to 32 UTF-8 bytes")
            if not isinstance(password, str) or len(password.encode("utf-8")) > 63:
                raise ValueError("WiFi password is too long")
            result = app.save_wifi({"ssid": ssid, "password": password})
        elif path == "/api/calibrate":
            value = _object(body)
            zone = _name(value.get("zone"), required=True)
            if value.get("point") not in ("dry", "wet"):
                raise ValueError("Calibration point must be dry or wet")
            result = app.calibration({"zone": zone, "point": value["point"]})
        elif path == "/api/valve":
            name = _name(query.get("valve"))
            state = query.get("state")
            if state == "open":
                result = app.open_valve(name)
            elif state == "close":
                result = app.close_valve(name)
            else:
                raise ValueError("state must be open or close")
        elif path in ("/api/water/trigger", "/api/zone/trigger", "/api/water/all"):
            duration = query.get("duration")
            if duration is not None:
                duration = _integer(duration, "duration", 1, 86400)
            zone = _name(query.get("zone"), required=True) if path == "/api/zone/trigger" else None
            valve = _name(query.get("valve")) if path == "/api/water/trigger" else None
            result = app.water(valve=valve, zone=zone,
                               all_valves=path == "/api/water/all", duration=duration)
        elif path == "/api/water/stop":
            result = app.stop_all()
        elif path in ("/api/update/check", "/api/update/apply"):
            result = app.update_action(path.rsplit("/", 1)[1])
        elif path == "/api/reboot":
            result = app.reboot()
        return {"ok": True} if result is None else result
