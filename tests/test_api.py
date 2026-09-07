"""API contracts: malformed requests cannot reach actuators or persistence."""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from api import APIRouter, ROUTES
from web import Response, JsonArray, json_fragments


class App:
    def __init__(self):
        self.calls = []
        self.error = None
        self.history_consumed = 0
        self.settings = {"hardware": {"valves": [{"name": "Jardín", "pin": 26}]},
                         "schedules": [], "daily_enabled": True}

    def status(self):
        return {"moisture": [], "valves": {}, "time_synced": False}

    def history(self, hours=None):
        self.calls.append(("history", (hours,), {}))
        def generate():
            for i in range(672):
                self.history_consumed += 1
                yield {"t": i * 900, "readings": [{"name": "Jardín 🌱", "percent": 41.2}]}
        return generate()

    def wifi_status(self):
        return {"ssid": "Garden", "connected": True, "password": "SECRET",
                "wifi_password": "SECRET", "credentials": {"password": "SECRET"}, "ip": "10.0.0.2"}

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.error:
                raise self.error
            return None
        return call


class APIContractTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.router = APIRouter(self.app)

    def call(self, path, query=None, body=None, method="POST"):
        return self.router.dispatch(method, path, query or {}, body)

    def assert_error(self, response, status):
        self.assertIsInstance(response, Response)
        self.assertEqual(response.status, status)
        self.assertFalse(response.body["ok"])

    def test_read_routes_and_callable_transport_contract(self):
        value = self.router("GET", "/api/status", {}, {}, {})
        self.assertNotIn("settings", value)
        self.assertEqual(self.call("/api/settings", method="GET"), self.app.settings)
        self.assertEqual(self.call("/api/valves", method="GET"), self.app.settings["hardware"]["valves"])
        self.assertEqual(self.call("/api/hardware", method="GET"), self.app.settings["hardware"])
        self.assertEqual(self.call("/api/schedules", method="GET"), [])
        for route, method in (("events", "events"), ("zones", "zones"), ("pinmap", "pinmap"),
                              ("calibrate", "calibration"), ("i2c/scan", "scan")):
            self.call("/api/" + route, method="GET")
            self.assertEqual(self.app.calls[-1][0], method)

    def test_history_is_lazy_streaming_with_unicode_and_bounded_fragments(self):
        value = self.call("/api/history", {"hours": "168"}, method="GET")
        self.assertIsInstance(value, JsonArray)
        self.assertTrue(value.history_records)
        self.assertFalse(JsonArray(iter(())).history_records)
        self.assertEqual(self.app.history_consumed, 0)
        chunks = list(json_fragments(value))
        self.assertLessEqual(max(map(len, chunks)), 512)
        decoded = json.loads(b"".join(chunks))
        self.assertEqual(len(decoded), 672)
        self.assertEqual(decoded[0]["readings"][0]["name"], "Jardín 🌱")
        self.assertEqual(self.app.calls[-1], ("history", (168,), {}))

    def test_history_hours_validated_before_reaching_storage(self):
        for value in ("no", "0", "-3", "9" * 3000, [], {}, True, 2.4):
            with self.subTest(value=value):
                self.assert_error(self.call("/api/history", {"hours": value}, method="GET"), 400)
        self.assertEqual(self.app.calls, [])

    def test_saved_history_range_clamps_to_fourteen_days(self):
        self.call("/api/history", {"hours": "1000"}, method="GET")
        self.assertEqual(self.app.calls[-1], ("history", (336,), {}))

    def test_duration_and_target_validation_prevent_unintended_watering(self):
        for value in ("", "oops", "-1", "0", "90000", True, 5.2, [], {}):
            with self.subTest(value=value):
                self.assert_error(self.call("/api/water/trigger", {"duration": value}), 400)
        self.assert_error(self.call("/api/zone/trigger", {}), 400)
        self.assert_error(self.call("/api/valve", {"state": "maybe"}), 400)
        self.assert_error(self.call("/api/valve", {"state": "open", "valve": []}), 400)
        self.assertEqual(self.app.calls, [])

    def test_watering_routes_preserve_unicode_and_sequential_targets(self):
        self.assertEqual(self.call("/api/zone/trigger", {"zone": "Jardín"}), {"ok": True})
        self.assertEqual(self.app.calls[-1], ("water", (), {"valve": None, "zone": "Jardín", "all_valves": False, "duration": None}))
        self.call("/api/water/all", {"duration": "30"})
        self.assertEqual(self.app.calls[-1][2], {"valve": None, "zone": None, "all_valves": True, "duration": 30})
        self.call("/api/water/trigger", {"duration": "41", "valve": "Jardín"})
        self.assertEqual(self.app.calls[-1][2]["valve"], "Jardín")
        for state, method in (("open", "open_valve"), ("close", "close_valve")):
            self.call("/api/valve", {"state": state, "valve": "Jardín"})
            self.assertEqual(self.app.calls[-1], (method, ("Jardín",), {}))
        self.call("/api/water/stop")
        self.assertEqual(self.app.calls[-1][0], "stop_all")

    def test_methods_are_checked_before_mutations(self):
        for route, allowed in ROUTES.items():
            for method in ("GET", "POST", "DELETE"):
                if method not in allowed.split():
                    response = self.call(route, method=method)
                    self.assert_error(response, 405)
                    self.assertIn("Allow", response.headers)
        self.assertEqual(self.app.calls, [])

    def test_unknown_api_is_404_nonapi_redirects(self):
        self.assert_error(self.call("/api/nope"), 404)
        response = self.call("/somewhere", method="GET")
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_bad_json_and_malformed_config_do_not_mutate(self):
        for path, body in (("settings", "{"), ("settings", []), ("zones", {}),
                           ("zones", {"zones": [None]}), ("zones", {"zones": [{"name": "x"}]}),
                           ("valves", {"valves": [{"pin": 26}]}), ("valves", {"valves": "bad"}),
                           ("schedules", None), ("schedules", [None]),
                           ("hardware", {"hardware": []}), ("config/import", {})):
            with self.subTest(path=path, body=body):
                self.assert_error(self.call("/api/" + path, body=body), 400)
        self.assertEqual(self.app.calls, [])

    def test_full_config_export_downloads_public_settings(self):
        response = self.call("/api/config/export", method="GET")
        self.assertEqual(response.status, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.body, self.app.settings)

    def test_export_ignores_accidentally_attached_credential_fields(self):
        self.app.settings.update({"WIFI_PASSWORD": "SECRET", "wifi": {"password": "SECRET"},
                                  "credentials": {"ssid": "private"}})
        for route in ("/api/config/export", "/api/settings"):
            response = self.call(route, method="GET")
            body = response.body if isinstance(response, Response) else response
            self.assertNotIn("SECRET", json.dumps(body))
            self.assertIn("hardware", body)

    def test_wifi_secrets_cannot_leak_from_facade(self):
        value = self.call("/api/wifi", method="GET")
        self.assertEqual(value, {"ssid": "Garden", "connected": True, "ip": "10.0.0.2"})
        self.assertNotIn("SECRET", json.dumps(value))

    def test_wifi_lengths_are_utf8_bytes_and_failed_save_does_not_succeed(self):
        for body in ({"ssid": ""}, {"ssid": "é" * 17}, {"ssid": "ok", "password": "a" * 65},
                     {"ssid": "ok", "password": []}):
            self.assert_error(self.call("/api/wifi", body=body), 400)
        self.assertEqual(self.app.calls, [])
        self.app.error = OSError("SECRET password")
        result = self.call("/api/wifi", body={"ssid": "Garden", "password": "SECRET"})
        self.assert_error(result, 503)
        self.assertNotIn("SECRET", json.dumps(result.body))
        self.assertEqual([call[0] for call in self.app.calls], ["save_wifi"])

    def test_calibration_is_queued_without_sensor_io(self):
        self.assert_error(self.call("/api/calibrate", body={"zone": "x", "point": "other"}), 400)
        self.assert_error(self.call("/api/calibrate", body={"point": "dry"}), 400)
        result = self.call("/api/calibrate", body={"zone": "Jardín", "point": "wet"})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.app.calls, [("calibration", ({"zone": "Jardín", "point": "wet"},), {})])

    def test_save_and_update_facade_routing(self):
        cases = [
            ("settings", {"daily_enabled": False}, "save_settings"),
            ("config/import", {"hardware": {}}, "save_settings"),
            ("zones", {"zones": [{"name": "x", "channel": 0}]}, "save_zones"),
            ("valves", {"valves": [{"name": "v", "pin": 26}]}, "save_valves"),
            ("hardware", {"hardware": {"i2c_sda_pin": 21}}, "save_hardware"),
            ("schedules", [{"id": 1, "hour": 6, "minute": 0, "duration_sec": 60}], "save_schedules"),
            ("wifi", {"ssid": "Garden", "password": ""}, "save_wifi"),
            ("reboot", {}, "reboot"),
        ]
        for path, body, method in cases:
            with self.subTest(path=path):
                self.assertEqual(self.call("/api/" + path, body=body), {"ok": True})
                self.assertEqual(self.app.calls[-1][0], method)
                if path in ("settings", "config/import"):
                    self.assertEqual(self.app.calls[-1][2], {"replace": path.endswith("import")})
        for action in ("check", "apply"):
            self.call("/api/update/" + action)
            self.assertEqual(self.app.calls[-1], ("update_action", (action,), {}))

    def test_errors_map_to_actionable_http_status(self):
        for error, status in ((ValueError("Unsafe pin"), 400), (RuntimeError("Watering active"), 409),
                              (OSError("write failed"), 503), (KeyError("missing"), 400),
                              (TypeError("invalid"), 400), (Exception("unexpected SECRET"), 500)):
            self.app.error = error
            response = self.call("/api/reboot")
            self.assert_error(response, status)
            self.assertNotIn("SECRET", json.dumps(response.body))

    def test_dashboard_has_one_fetch_site_and_no_dynamic_html_injection(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(source.count("await fetch("), 1)
        self.assertNotIn(".innerHTML", source)
        self.assertNotIn("setInterval(", source)
        self.assertNotIn("<script src=", source)
        self.assertIn("networkTail = job.catch", source)


if __name__ == "__main__":
    unittest.main()
