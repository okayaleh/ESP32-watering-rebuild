"""Pinned GitHub release transactions, recovery policy, and unattended scheduling."""
import hashlib
import gc
import json
import os
import runpy
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("src"))
import updater
from application import Application
from boot import boot_guard
from test_updater import write, read, drain, upload

REPO = "okayaleh/ESP32-watering-rebuild"
RAW = "https://raw.githubusercontent.com/" + REPO + "/"
CHANNEL = RAW + "updates/channel.json"
NATIVE = "9369e9e2eba45a9828d3d8c929c2b39ddeb41987318d8aa092aa130908780073"


def release(version="2.0.0-rebuild.5", commit="a" * 40):
    files = {"app.py": b"VALUE = 'new'\n", "version.json": json.dumps({"version": version}).encode()}
    manifest = {"version": version, "platform": "planter-esp32-romfs-mpy6", "native_sha256": NATIVE,
                "files": [{"name": name, "path": "build/" + name, "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()]}
    encoded = json.dumps(manifest).encode()
    entry = {"version": version, "commit": commit, "manifest_size": len(encoded),
             "manifest_sha256": hashlib.sha256(encoded).hexdigest()}
    responses = {RAW + commit + "/build/manifest.json": encoded}
    responses.update({RAW + commit + "/build/" + name: data for name, data in files.items()})
    return entry, responses, files


def channel_response(entries):
    return json.dumps({"schema": 1, "repository": REPO, "releases": entries}).encode()


class MemoryDownload:
    responses = None
    requested = None
    active = 0

    def __init__(self, url, destination, limit, now_ms, *args):
        self.url, self.destination, self.limit = url, destination, limit
        self.done, self.error, self.closed = False, None, False
        self.size = 0
        self.hash = hashlib.sha256()
        self.requested.append(url)
        type(self).active += 1
        if self.active != 1:
            raise AssertionError("Previous TLS transfer was not released")

    def poll(self, now_ms):
        data = self.responses[self.url]
        if len(data) > self.limit:
            self.error = "Download exceeds allowed size"
            return
        with open(self.destination, "wb") as stream:
            stream.write(data)
        self.hash.update(data)
        self.size = len(data)
        self.done = True

    def close(self):
        if not self.closed:
            self.closed = True
            type(self).active -= 1

    def abort(self):
        self.close()
        if os.path.exists(self.destination):
            os.remove(self.destination)


class GitHubUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.entry, self.responses, self.files = release()
        self.responses[CHANNEL] = channel_response([self.entry])
        MemoryDownload.responses = self.responses
        MemoryDownload.requested = []
        MemoryDownload.active = 0
        self.patcher = patch.object(updater, "HTTPDownload", MemoryDownload)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.temp.cleanup)
        self.device = updater.Updater(self.root, close_valves=lambda: True, idle=lambda: True)

    def check(self, **kwargs):
        self.device.request_check(**kwargs)
        drain(self.device)

    def test_channel_manifest_and_files_stay_pinned_between_check_and_install(self):
        protected = {"config.py": b"LOCAL = True\n", "wifi.json": b'{"ssid":"private"}',
                     "settings.json": b'{"threshold":37}', "watering_state.json": b'{"fired":123}'}
        for name, content in protected.items():
            write(self.root, name, content)
        self.check(automatic=True)
        self.assertIsNone(self.device.status["error"])
        self.assertTrue(self.device.auto_eligible)
        self.responses[CHANNEL] = b"changed after check: must never be consulted during install"
        self.device.request_install()
        drain(self.device)
        self.assertTrue(self.device.reboot_required, self.device.status)
        self.assertEqual(MemoryDownload.requested.count(CHANNEL), 1)
        self.assertTrue(all(url == CHANNEL or "/" + "a" * 40 + "/" in url for url in MemoryDownload.requested))
        for name, content in dict(self.files, **protected).items():
            self.assertEqual(read(self.root, name), content)
        self.assertEqual(MemoryDownload.active, 0)

    def test_manifest_integrity_failure_clears_stale_available_state(self):
        self.check()
        self.assertTrue(self.device.status["available"])
        url = RAW + "a" * 40 + "/build/manifest.json"
        self.responses[url] = self.responses[url].replace(b"app.py", b"bad.py")
        self.check(automatic=True)
        self.assertIn("SHA-256", self.device.status["error"])
        self.assertFalse(self.device.status["available"])
        self.assertFalse(self.device.auto_eligible)
        with self.assertRaises(ValueError):
            self.device.request_install()
        self.assertEqual(MemoryDownload.active, 0)

    def test_channel_rejects_unpinned_or_foreign_or_oversized_metadata(self):
        bad_channels = [dict(schema=1, repository="someone/else", releases=[self.entry]),
                        dict(schema=True, repository=REPO, releases=[self.entry]),
                        dict(schema=1, repository=REPO, releases=[dict(self.entry, commit="main")]),
                        dict(schema=1, repository=REPO, releases=[dict(self.entry, manifest_size=True)]),
                        dict(schema=1, repository=REPO, releases=[self.entry] * 4)]
        for channel in bad_channels:
            with self.subTest(channel=channel):
                self.responses[CHANNEL] = json.dumps(channel).encode()
                self.check()
                self.assertEqual(self.device.status["state"], "error")
                self.assertFalse(self.device.status["available"])
        self.responses[CHANNEL] = b" " * 4097
        self.check()
        self.assertIn("allowed size", self.device.status["error"])
        self.assertEqual(MemoryDownload.active, 0)

    def test_manifest_native_platform_version_and_path_are_checked(self):
        url = RAW + "a" * 40 + "/build/manifest.json"
        original = json.loads(self.responses[url])
        for key, value in (("native_sha256", "0" * 64), ("platform", "other"), ("version", "wrong"), ("path", "../secret.py")):
            with self.subTest(key=key):
                manifest = json.loads(json.dumps(original))
                if key == "path":
                    manifest["files"][0]["path"] = value
                else:
                    manifest[key] = value
                data = json.dumps(manifest).encode()
                self.responses[url] = data
                self.responses[CHANNEL] = channel_response([dict(self.entry, manifest_size=len(data), manifest_sha256=hashlib.sha256(data).hexdigest())])
                self.check()
                self.assertEqual(self.device.status["state"], "error")
                self.assertFalse(os.path.exists(self.root + "/app.py"))

    def test_file_hash_failure_never_replaces_live_file_and_cannot_retry_apply(self):
        write(self.root, "app.py", b"old")
        self.check(automatic=True)
        self.responses[RAW + "a" * 40 + "/build/app.py"] = b"VALUE = 'bad'\n"
        self.device.request_install()
        drain(self.device)
        self.assertIn("SHA-256", self.device.status["error"])
        self.assertEqual(read(self.root, "app.py"), b"old")
        self.assertFalse(self.device.reboot_required)
        self.assertFalse(self.device.status["available"])
        self.assertFalse(self.device.auto_eligible)

    def test_failed_boot_is_quarantined_across_reboots_and_stable_marking(self):
        self.check(automatic=True)
        self.device.request_install()
        drain(self.device)
        self.assertEqual(boot_guard(self.root, limit=1), "rolled_back")
        self.device = updater.Updater(self.root)
        updater.mark_stable(self.root)
        self.device = updater.Updater(self.root)
        self.check(automatic=True)
        self.assertEqual(self.device.status["blocked_version"], self.entry["version"])
        self.assertIn("boot trial", self.device.status["error"])
        self.assertFalse(self.device.auto_eligible)
        self.assertEqual(json.loads(read(self.root, ".ota-journal.json"))["phase"], "rolled_back")

    def test_boot_acknowledgement_distinguishes_fresh_from_damaged_or_interrupted(self):
        self.assertTrue(updater.mark_stable(self.root))
        self.assertFalse(os.path.exists(self.root + "/.ota-journal.json"))
        for journal in (b"{", b"[]", b'{"phase":"unknown"}'):
            write(self.root, ".ota-journal.json", journal)
            with self.assertRaises(ValueError):
                updater.mark_stable(self.root)
        for phase in ("committing", "rolling_back"):
            write(self.root, ".ota-journal.json", json.dumps({"phase": phase}).encode())
            self.assertFalse(updater.mark_stable(self.root))

    def test_explicit_retained_rollback_persists_automatic_hold(self):
        older, responses, files = release("2.0.0-rebuild.4", "b" * 40)
        self.responses.update(responses)
        self.responses[CHANNEL] = channel_response([self.entry, older])
        self.check(version=older["version"])
        self.assertFalse(self.device.auto_eligible)
        self.device.request_install()
        drain(self.device)
        updater.mark_stable(self.root)
        self.device = updater.Updater(self.root, close_valves=lambda: True, idle=lambda: True)
        self.assertTrue(self.device.status["automatic_paused"])
        self.assertEqual(self.device.status["held_version"], older["version"])
        self.check()
        self.device.request_install()
        drain(self.device)
        updater.mark_stable(self.root)
        self.assertFalse(updater.Updater(self.root).status["automatic_paused"])

    def test_unknown_retained_selection_does_not_fall_back_to_latest(self):
        self.check(version="missing")
        self.assertIn("no longer", self.device.status["error"])
        self.assertEqual(MemoryDownload.requested, [CHANNEL])

    def test_unchanged_release_with_absent_delete_does_not_reinstall_forever(self):
        for name, data in self.files.items():
            write(self.root, name, data)
        url = RAW + "a" * 40 + "/build/manifest.json"
        manifest = json.loads(self.responses[url])
        manifest["delete"] = ["obsolete.py"]
        data = json.dumps(manifest).encode()
        self.responses[url] = data
        self.responses[CHANNEL] = channel_response([dict(self.entry, manifest_size=len(data), manifest_sha256=hashlib.sha256(data).hexdigest())])
        self.check(automatic=True)
        self.assertEqual(self.device.status["state"], "checked")
        self.assertFalse(self.device.status["available"])
        self.assertFalse(self.device.auto_eligible)

    def test_upload_never_inherits_automatic_install_eligibility(self):
        self.check(automatic=True)
        upload(self.device, "manual.py", b"MANUAL = True\n")
        self.assertFalse(self.device.auto_eligible)
        self.assertIsNone(self.device.release)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.schedule = updater.UpdateSchedule()
        self.clock = [1900000000]
        self.patch = patch.object(updater, "epoch", lambda: self.clock[0])
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.updater = SimpleNamespace(status={"busy": False, "state": "idle", "available": False},
                                       auto_eligible=False, reboot_required=False, uploads={})
        self.app = SimpleNamespace(updater=self.updater, config=SimpleNamespace(UPDATE_AUTO_INSTALL=True, UPDATE_CHECK_HOUR=4),
            uptime_ms=60000, controller=SimpleNamespace(synced=True, idle=lambda: True, paused=False, inhibited=None),
            wifi=SimpleNamespace(connected=True), reboot_at=None, sensors=None, server=None,
            settings={"tz_offset_min": 0}, update_action=self.action)

    def action(self, action, automatic=False):
        self.actions.append((action, automatic))
        self.updater.status.update(busy=True, state="checking" if action == "check" else "downloading")

    def finish(self, state="checked", available=False, error=None):
        self.updater.status.update(busy=False, state=state, available=available, error=error)
        self.updater.auto_eligible = available and not error

    def test_startup_catches_up_then_checks_at_next_local_four_am(self):
        self.app.uptime_ms = 59999
        self.schedule.poll(self.app, 0)
        self.assertFalse(self.actions)
        self.app.uptime_ms = 60000
        self.schedule.poll(self.app, 1)
        self.finish()
        self.schedule.poll(self.app, 2)
        self.schedule.poll(self.app, 300000)
        self.assertEqual(self.actions, [("check", True)])
        self.clock[0] += 86400
        self.schedule.poll(self.app, 86400000)
        self.assertEqual(self.actions, [("check", True), ("check", True)])

    def test_failed_checks_retry_after_five_minutes_then_thirty(self):
        self.schedule.poll(self.app, 0)
        self.finish("error", error="offline")
        self.schedule.poll(self.app, 1000)
        self.schedule.poll(self.app, 300999)
        self.assertEqual(len(self.actions), 1)
        self.schedule.poll(self.app, 301000)
        self.assertEqual(len(self.actions), 2)
        self.finish("error", error="TLS unavailable")
        self.schedule.poll(self.app, 302000)
        self.schedule.poll(self.app, 2101999)
        self.assertEqual(len(self.actions), 2)
        self.schedule.poll(self.app, 2102000)
        self.assertEqual(len(self.actions), 3)

    def test_automatic_apply_defers_while_watering_and_never_applies_manual_upload(self):
        self.schedule.poll(self.app, 0)
        self.finish(available=True)
        self.app.controller.idle = lambda: False
        self.schedule.poll(self.app, 1)
        self.assertEqual(self.actions, [("check", True)])
        self.app.controller.idle = lambda: True
        self.schedule.poll(self.app, 2)
        self.assertEqual(self.actions, [("check", True), ("apply", True)])
        self.finish("uploaded", available=True)
        self.updater.auto_eligible = False
        self.updater.uploads = {"manual.py": {}}
        self.schedule.poll(self.app, 3)
        self.schedule.poll(self.app, 3000000)
        self.assertEqual(len(self.actions), 2)

    def test_offline_unsynced_paused_calibration_and_rollback_hold_defer_checks(self):
        cases = [(self.app.wifi, "connected", False), (self.app.controller, "synced", False),
                 (self.app.controller, "paused", True), (self.app, "sensors", SimpleNamespace(calibration=True))]
        for target, key, value in cases:
            original = getattr(target, key)
            setattr(target, key, value)
            self.schedule.poll(self.app, 0)
            setattr(target, key, original)
        self.updater.status["automatic_paused"] = True
        self.schedule.poll(self.app, 0)
        self.assertFalse(self.actions)


class ApplicationUpdateTests(unittest.TestCase):
    def test_busy_update_rejection_preserves_existing_watering_pause(self):
        controller = SimpleNamespace(idle=lambda: True, paused=True, stop_all=lambda reason: True)
        app = Application(SimpleNamespace(), SimpleNamespace(data={}), controller, None)
        def busy():
            raise ValueError("Updater is already busy")
        app.updater = SimpleNamespace(request_check=busy)
        with self.assertRaisesRegex(ValueError, "already busy"):
            app.update_action("check")
        self.assertTrue(controller.paused)


class FreshRuntimeUpdateTests(unittest.TestCase):
    def test_fresh_usb_boot_checks_automatically_only_after_sixty_healthy_seconds(self):
        from settings_store import SettingsStore
        from test_sensors import Bus
        config = SimpleNamespace(**{k: v for k, v in runpy.run_path("src/config.example.py").items() if k.isupper()})
        config.STATUS_LED_PIN = None
        clock, checks, trial_results = [0], [], []
        class Finished(BaseException):
            pass
        class Pin:
            OUT = 1
            def __init__(self, *args, **kwargs):
                pass
            def value(self, *args):
                return 0
        class WDT:
            def __init__(self, **kwargs):
                pass
            def feed(self):
                pass
        machine = SimpleNamespace(Pin=Pin, WDT=WDT, reset_cause=lambda: 1,
                                  reset=lambda: self.fail("Unexpected reset"))
        class Wifi:
            def __init__(self, *args, **kwargs):
                self.connected = True
                self.dns = SimpleNamespace(resolve=lambda host: None)
                self.portal = SimpleNamespace(active=False)
            def poll(self, *args):
                pass
        class NTP:
            def __init__(self, *args, **kwargs):
                self.synced = True
            def poll(self, *args):
                pass
        class Server:
            def __init__(self, *args, **kwargs):
                pass
            def start(self):
                return True
            def poll(self, *args):
                pass
            def health(self):
                return {"listening": True, "active_uploads": 0}
        def check(device, automatic=False, version=None):
            checks.append((clock[0], automatic, version))
            device.status.update(state="checked", busy=False, available=False)
        def sleep(ms):
            clock[0] += 500
            if clock[0] >= 70000:
                raise Finished()
        original_mark_stable = updater.mark_stable
        def acknowledge():
            result = original_mark_stable()
            trial_results.append((clock[0], result))
            return result
        original_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(config, directory + "/settings.json")
            store.data.update(daily_enabled=False, moisture_watering_enabled=False)
            store.save(store.data)
            with patch.dict(sys.modules, {"machine": machine, "wifi": SimpleNamespace(WifiManager=Wifi, NTPClient=NTP)}):
                import runtime
                import moisture
                import web
                with patch.object(runtime, "machine", machine), patch.object(runtime, "ticks_ms", lambda: clock[0]), patch.object(runtime, "epoch", lambda: 1900000000 + clock[0] // 1000), patch.object(runtime.time, "sleep_ms", sleep, create=True), patch.object(gc, "mem_free", lambda: 90000, create=True), patch.object(gc, "mem_alloc", lambda: 40000, create=True), patch.object(moisture, "create_bus", return_value=Bus()), patch.object(web, "HTTPServer", Server), patch.object(updater.Updater, "request_check", check), patch.object(updater, "mark_stable", acknowledge):
                    os.chdir(directory)
                    try:
                        with self.assertRaises(Finished):
                            runtime.run(config)
                        self.assertEqual(trial_results, [(60000, True)])
                        self.assertEqual(checks, [(60000, True, None)])
                        self.assertFalse(os.path.exists(".ota-journal.json"))
                    finally:
                        os.chdir(original_directory)

    def test_version_selection_api_is_explicit_and_rejects_apply_version(self):
        from api import APIRouter
        from test_api import App
        app = App()
        router = APIRouter(app)
        self.assertEqual(router.dispatch("POST", "/api/update/check", {}, {"version": "2.0.0-rebuild.4"}), {"ok": True})
        self.assertEqual(app.calls, [("update_action", ("check",), {"version": "2.0.0-rebuild.4"})])
        for action, value in (("apply", "2.0.0-rebuild.4"), ("check", ""), ("check", 4)):
            response = router.dispatch("POST", "/api/update/" + action, {}, {"version": value})
            self.assertEqual(response.status, 400)
        self.assertEqual(len(app.calls), 1)

    def test_manual_github_check_requires_wifi_time_and_confirmed_closed_outputs(self):
        calls = []
        controller = SimpleNamespace(idle=lambda: True, paused=False, stop_all=lambda reason: calls.append("closed") or True)
        app = Application(SimpleNamespace(), SimpleNamespace(data={}), controller, None)
        app.updater = SimpleNamespace(source="github", uploads={}, request_check=lambda **kw: calls.append(kw))
        with self.assertRaisesRegex(RuntimeError, "home WiFi"):
            app.update_action("check")
        app.wifi = SimpleNamespace(connected=True)
        with self.assertRaisesRegex(RuntimeError, "time synchronization"):
            app.update_action("check")
        self.assertFalse(calls)
        app.ntp = SimpleNamespace(synced=True)
        app.update_action("check", version="2.0.0-rebuild.4")
        self.assertEqual(calls, ["closed", {"automatic": False, "version": "2.0.0-rebuild.4"}])
        self.assertTrue(controller.paused)


if __name__ == "__main__":
    unittest.main()
