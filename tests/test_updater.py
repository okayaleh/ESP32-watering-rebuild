import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
import select
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import updater
from updater import Updater, HTTPDownload, validate_filename, mark_stable
from boot import boot_guard, close_boot_valves
from settings_store import SettingsStore


class FakeSocket:
    def __init__(self, response, trickle=False):
        self.response = response
        self.sent = bytearray()
        self.sizes = []
        self.blocking = None
        self.closed = False
        self.trickle = trickle

    def setblocking(self, blocking):
        self.blocking = blocking

    def connect(self, address):
        self.address = address
        raise OSError(115)

    def send(self, data):
        self.sizes.append(len(data))
        count = min(len(data), 7)  # Force partial writes, including headers.
        self.sent.extend(data[:count])
        return count

    def recv(self, size):
        assert size <= 512
        if self.trickle:
            raise OSError(11)
        data, self.response = self.response[:size], self.response[size:]
        return data

    def close(self):
        self.closed = True


class SocketModule:
    def __init__(self, sock):
        self.sock = sock

    def socket(self):
        return self.sock


class FakePoll:
    def register(self, sock, events):
        pass

    def modify(self, sock, events):
        pass

    def poll(self, timeout):
        assert timeout == 0
        return [(1, 1)]


def write(root, name, data):
    with open(os.path.join(root, name), "wb") as stream:
        stream.write(data)


def read(root, name):
    with open(os.path.join(root, name), "rb") as stream:
        return stream.read()


def upload(device, name, data):
    sink = device.begin_upload(name)
    for start in range(0, len(data), 512):
        sink.write(data[start:start + 512])
    sink.finish()


def drain(device):
    for now in range(10000):
        device.poll(now)
        if not device.status["busy"]:
            return
    raise AssertionError("Updater did not finish")


class PowerCut(BaseException):
    pass


class UpdateTests(unittest.TestCase):
    def test_upload_install_rollback_new_deleted_and_shadow_files(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py", b"old-source")
            write(root, "app.mpy", b"old-bytecode")
            write(root, "obsolete.css", b"old-style")
            write(root, "settings.json", b'{"keep":true}')
            closed = []
            device = Updater(root, close_valves=lambda: closed.append(True), idle=lambda: True)
            upload(device, "app.mpy", b"M\x06\x00\x1fnew-bytecode")
            upload(device, "fresh.py", b"new-module")
            device.request_install()
            device.deletions = ["obsolete.css"]
            drain(device)
            self.assertTrue(device.reboot_required, device.status)
            self.assertEqual(read(root, "app.mpy"), b"M\x06\x00\x1fnew-bytecode")
            self.assertFalse(os.path.exists(os.path.join(root, "app.py")))
            self.assertFalse(os.path.exists(os.path.join(root, "obsolete.css")))
            self.assertTrue(closed)
            self.assertEqual(boot_guard(root), "trial")
            self.assertEqual(boot_guard(root), "trial")
            self.assertEqual(boot_guard(root), "rolled_back")
            self.assertEqual(read(root, "app.py"), b"old-source")
            self.assertEqual(read(root, "app.mpy"), b"old-bytecode")
            self.assertEqual(read(root, "obsolete.css"), b"old-style")
            self.assertFalse(os.path.exists(os.path.join(root, "fresh.py")))
            self.assertEqual(read(root, "settings.json"), b'{"keep":true}')

    def test_power_cuts_at_each_commit_filesystem_mutation_recover(self):
        real_remove, real_rename = os.remove, os.rename
        for cut_at in range(14):
            with self.subTest(cut_at=cut_at), tempfile.TemporaryDirectory() as root:
                write(root, "app.py", b"original")
                write(root, "gone.css", b"retain-on-rollback")
                device = Updater(root, close_valves=lambda: None, idle=lambda: True)
                upload(device, "app.py", b"replacement")
                upload(device, "fresh.py", b"new")
                device.request_install()
                device.deletions = ["gone.css"]
                mutations = [0]

                def mutate(operation, *args):
                    if mutations[0] == cut_at:
                        raise PowerCut()
                    mutations[0] += 1
                    return operation(*args)

                with patch.object(os, "remove", lambda *args: mutate(real_remove, *args)), \
                     patch.object(os, "rename", lambda *args: mutate(real_rename, *args)):
                    try:
                        drain(device)
                    except PowerCut:
                        pass
                device._close_files()
                boot_guard(root, limit=1)
                self.assertEqual(read(root, "app.py"), b"original")
                self.assertEqual(read(root, "gone.css"), b"retain-on-rollback")
                self.assertFalse(os.path.exists(os.path.join(root, "fresh.py")))

    def test_rollback_itself_is_repeatable_after_power_cut(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py", b"original")
            device = Updater(root, close_valves=lambda: None, idle=lambda: True)
            upload(device, "app.py", b"replacement")
            device.request_install()
            drain(device)
            real_rename = os.rename

            def interrupted(source, destination):
                if source.endswith(".restore"):
                    raise PowerCut()
                return real_rename(source, destination)

            with patch.object(os, "rename", interrupted), self.assertRaises(PowerCut):
                boot_guard(root, limit=1)
            self.assertEqual(boot_guard(root), "rolled_back")
            self.assertEqual(read(root, "app.py"), b"original")

    def test_successful_boot_is_not_rolled_back(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py", b"old")
            device = Updater(root, close_valves=lambda: None, idle=lambda: True)
            upload(device, "app.py", b"new")
            device.request_install()
            drain(device)
            self.assertEqual(boot_guard(root), "trial")
            self.assertTrue(mark_stable(root))
            for _ in range(5):
                self.assertEqual(boot_guard(root), "stable")
            self.assertEqual(read(root, "app.py"), b"new")

    def test_install_requires_confirmed_closed_valves(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py", b"old")
            device = Updater(root, close_valves=lambda: None, idle=lambda: False)
            upload(device, "app.py", b"new")
            device.request_install()
            drain(device)
            self.assertEqual(device.status["state"], "error")
            self.assertEqual(read(root, "app.py"), b"old")
            self.assertFalse(device.reboot_required)

    def test_corrupt_staged_upload_fails_readback_before_modifying_live_files(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "app.py", b"old")
            device = Updater(root, close_valves=lambda: None, idle=lambda: True)
            upload(device, "app.py", b"new")
            write(root, ".ota/new-app.py", b"bad")
            device.request_install()
            drain(device)
            self.assertIn("readback", device.status["error"])
            self.assertEqual(read(root, "app.py"), b"old")

    def test_protected_files_and_paths_rejected(self):
        for filename in ("config.py", "wifi.json", "settings.json", "watering_state.json",
                         "boot.py", "romboot.py", "romboot.mpy", "_rom_state.json", "../app.py", "dir/app.py", ".ota.json", "STATE.JSON",
                         "settings.json.prev"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                validate_filename(filename)

    def test_boot_closes_active_low_and_uses_previous_settings(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "settings.json", b"{")
            write(root, "settings.json.prev", json.dumps({"hardware": {"valves": [
                {"pin": 26, "active_high": True}, {"pin": 27, "active_high": False}]}}).encode())
            closed = []

            class Pin:
                OUT = 1
                def __init__(self, number, mode, value):
                    closed.append((number, value))

            self.assertTrue(close_boot_valves(root, Pin))
            self.assertEqual(closed, [(26, 0), (27, 1)])

    def test_boot_close_fault_attempts_every_output_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "settings.json", json.dumps({"hardware": {"valves": [
                {"pin": 26}, {"pin": 27}]}}).encode())
            attempted = []
            class Pin:
                OUT = 1
                def __init__(self, number, mode, value):
                    attempted.append(number)
                    if number == 26: raise OSError("failed output")
            self.assertFalse(close_boot_valves(root, Pin))
            self.assertEqual(attempted, [26, 27])

    def test_manifest_per_entry_paths_and_hash_comparison(self):
        with tempfile.TemporaryDirectory() as root:
            data = b"existing"
            write(root, "app.py", data)
            device = Updater(root)
            manifest = {"version": "v2", "files": [{"name": "app.py", "path": "relocated/app.py",
                        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)},
                        {"name": "new.py", "path": "elsewhere/new.py",
                        "sha256": hashlib.sha256(b"new").hexdigest(), "size": 3}]}
            write(root, ".ota/manifest", json.dumps(manifest).encode())
            device._parse_manifest()
            device.phase = "hash"
            device.status["busy"] = True
            drain(device)
            self.assertEqual([item["path"] for item in device.changed], ["elsewhere/new.py"])

    def test_older_http_release_preserves_compatible_settings_wifi_and_intent(self):
        # Versions are deliberately ordered newest -> older. This exercises the
        # complete download/install path with the existing settings contract;
        # it does not certify arbitrary older code or reverse schema migration.
        newer = "2.0.0-rebuild.3"
        older = "2.0.0-rebuild.2"
        old_app = b"M\x06\x00\x1folder-bytecode"
        shared = b"M\x06\x00\x1funchanged-bytecode"
        release = {"app.mpy": old_app, "shared.mpy": shared,
                   "restored.py": b"VALUE = 'present in older release'\n",
                   "version.json": json.dumps({"version": older}).encode()}
        manifest = {"version": older, "files": [
            {"name": name, "path": "build/" + name, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in release.items()], "delete": ["removed.css"]}
        responses = {"build/manifest.json": json.dumps(manifest).encode()}
        responses.update({"build/" + name: data for name, data in release.items()})
        requested = []
        base = "http://192.0.2.1/older-tag/"

        def download(url, *args, **kwargs):
            self.assertTrue(url.startswith(base))
            path = url[len(base):]
            requested.append(path)
            body = responses[path]
            response = (b"HTTP/1.0 200 OK\r\nContent-Length: " +
                        str(len(body)).encode() + b"\r\n\r\n" + body)
            return HTTPDownload(url, *args, socket_module=SocketModule(FakeSocket(response)),
                                poll_factory=FakePoll, **kwargs)

        with tempfile.TemporaryDirectory() as root:
            config = SimpleNamespace(VALVES=[{"name": "valve1", "pin": 26,
                                             "active_high": False}],
                                     ZONES=[{"name": "zone1", "channel": 0}])
            settings_path = root + "/settings.json"
            settings = SettingsStore(config, settings_path)
            settings.data["daily_enabled"] = False
            settings.data["zone_thresholds"]["zone1"] = 37
            settings.save(settings.data)
            protected = {"settings.json": read(root, "settings.json"),
                         "wifi.json": b'{"ssid":"test-network","password":"test-only-password"}',
                         "watering_state.json": b'{"valves":{"valve1":{"schedule":1780000000}},"fired":{"1":20000}}',
                         "config.py": b"# local board configuration\n"}
            for name, data in protected.items():
                write(root, name, data)
            write(root, "app.mpy", b"M\x06\x00\x1fnewer-bytecode")
            write(root, "shared.mpy", shared)
            write(root, "shared.py", b"# stale source shadows identical bytecode\n")
            write(root, "new_only.py", b"# newer module absent from older manifest\n")
            write(root, "removed.css", b"newer-release-style")
            write(root, "version.json", json.dumps({"version": newer}).encode())
            write(root, ".ota-journal.json", b'{"phase":"stable","entries":[]}')
            closed = []
            device = Updater(root, base, close_valves=lambda: closed.append(True),
                             idle=lambda: True)
            with patch.object(updater, "HTTPDownload", download):
                device.request_check()
                drain(device)
                self.assertIsNone(device.status["error"])
                self.assertEqual(device.status["installed_version"], newer)
                self.assertEqual(device.status["available_version"], older)
                self.assertTrue(device.status["available"])
                self.assertEqual({entry["name"] for entry in device.changed},
                                 {"app.mpy", "restored.py", "version.json"})
                self.assertEqual(set(device.deletions), {"removed.css", "shared.py"})
                device.request_install()
                drain(device)
            self.assertIsNone(device.status["error"])
            self.assertTrue(device.reboot_required)
            self.assertTrue(closed)
            self.assertNotIn("build/shared.mpy", requested)
            for name, data in release.items():
                self.assertEqual(read(root, name), data)
            for name in ("removed.css", "shared.py"):
                self.assertFalse(os.path.exists(root + "/" + name))
            # Unknown newer modules require an explicit deletion; an older
            # manifest does not imply replacing the whole application directory.
            self.assertTrue(os.path.exists(root + "/new_only.py"))
            self.assertEqual(read(root, ".ota/old-app.mpy"), b"M\x06\x00\x1fnewer-bytecode")
            self.assertEqual(boot_guard(root), "trial")
            self.assertEqual(SettingsStore(config, settings_path).data, settings.data)
            self.assertTrue(mark_stable(root))
            self.assertEqual(boot_guard(root), "stable")
            self.assertEqual(Updater(root).status["installed_version"], older)
            for name, data in protected.items():
                self.assertEqual(read(root, name), data)

    def test_plain_http_transfer_handles_partial_writes_and_bounded_chunks(self):
        data = ("Jardín " * 200).encode()
        response = b"HTTP/1.0 200 OK\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data
        with tempfile.TemporaryDirectory() as root:
            sock = FakeSocket(response)
            download = HTTPDownload("http://192.0.2.1/a.py", root + "/stage", 5000, 0,
                                    socket_module=SocketModule(sock), poll_factory=FakePoll)
            for now in range(1000):
                download.poll(now)
                if download.done or download.error:
                    break
            self.assertTrue(download.done, download.error)
            self.assertFalse(sock.blocking)
            self.assertLessEqual(max(sock.sizes), 512)
            self.assertIn(b"GET /a.py HTTP/1.0\r\n", sock.sent)
            self.assertEqual(read(root, "stage"), data)
            self.assertEqual(updater._hex(download.hash), hashlib.sha256(data).hexdigest())

    def test_trickling_transfer_hits_absolute_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            sock = FakeSocket(b"", trickle=True)
            download = HTTPDownload("http://192.0.2.1/a.py", root + "/stage", 5000, 0,
                timeout_ms=100, socket_module=SocketModule(sock), poll_factory=FakePoll)
            for now in range(120):
                download.poll(now)
            self.assertIn("deadline", download.error)
            self.assertTrue(sock.closed)

    def test_oversized_and_chunked_responses_rejected(self):
        for headers in (b"Content-Length: 6000", b"Transfer-Encoding: chunked",
                        b"Content-Length: 5\r\nContent-Length: 5"):
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as root:
                sock = FakeSocket(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n\r\nhello")
                download = HTTPDownload("http://192.0.2.1/a.py", root + "/stage", 5000, 0,
                    socket_module=SocketModule(sock), poll_factory=FakePoll)
                for now in range(1000):
                    download.poll(now)
                    if download.error:
                        break
                self.assertTrue(download.error)
                self.assertFalse(os.path.exists(root + "/stage"))

    def test_https_rejected_before_any_socket_creation(self):
        with self.assertRaises(ValueError):
            HTTPDownload("https://example.com/a.py", "unused", 100, 0)

    def test_incompatible_mpy_upload_rejected_before_commit(self):
        with tempfile.TemporaryDirectory() as root:
            device = Updater(root, close_valves=lambda: None, idle=lambda: True)
            upload(device, "app.mpy", b"M\x05\x00\x1fold-format")
            device.request_install()
            drain(device)
            self.assertIn("Incompatible", device.status["error"])
            self.assertFalse(os.path.exists(root + "/app.mpy"))

    def test_unchanged_bytecode_still_removes_shadow_source(self):
        with tempfile.TemporaryDirectory() as root:
            data = b"M\x06\x00\x1fcompiled"
            write(root, "app.mpy", data)
            write(root, "app.py", b"stale-source")
            device = Updater(root)
            manifest = {"files": [{"name": "app.mpy", "path": "build/app.mpy",
                        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}]}
            write(root, ".ota/manifest", json.dumps(manifest).encode())
            device._parse_manifest()
            device.phase = "hash"
            device.status["busy"] = True
            drain(device)
            self.assertTrue(device.status["available"])
            self.assertFalse(device.changed)
            self.assertEqual(device.deletions, ["app.py"])

    def test_real_http_checks_downloads_generated_manifest_and_rolls_back(self):
        project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest_path = os.path.join(project, "build", "manifest.json")
        if not os.path.exists(manifest_path):
            self.skipTest("Run tools/build.py for release-artifact integration")
        with open(manifest_path, encoding="utf-8") as stream:
            manifest = json.load(stream)
        served = []

        class Mirror(BaseHTTPRequestHandler):
            def do_GET(self):
                path = os.path.join(project, self.path.lstrip("/"))
                with open(path, "rb") as stream:
                    body = stream.read()
                served.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class RealPoll:
            def register(self, sock, events):
                self.sock, self.events = sock, events

            modify = register

            def poll(self, timeout):
                ready = select.select([self.sock] if self.events & 1 else [],
                    [self.sock] if self.events & 4 else [], [self.sock], 0)
                return [(1, 1)] if any(ready) else []

        original_download = HTTPDownload

        def download(*args, **kwargs):
            kwargs["poll_factory"] = RealPoll
            return original_download(*args, **kwargs)

        server = HTTPServer(("127.0.0.1", 0), Mirror)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as root, patch.object(updater, "HTTPDownload", download):
                write(root, "main.py", b"old-main")
                write(root, "settings.json", b'{"protected":true}')
                device = Updater(root, "http://127.0.0.1:%s" % server.server_port,
                    close_valves=lambda: None, idle=lambda: True)

                def run_network():
                    start = time.monotonic()
                    while device.status["busy"]:
                        self.assertLess(time.monotonic() - start, 20)
                        device.poll(int(time.monotonic() * 1000) % (1 << 30))
                        time.sleep(0.0001)  # Yield to real server thread.
                    self.assertIsNone(device.status["error"])

                device.request_check()
                run_network()
                self.assertEqual(device.status["available_version"], manifest["version"])
                self.assertEqual(len(device.changed), len(manifest["files"]))
                device.request_install()
                run_network()
                self.assertTrue(device.reboot_required)
                for entry in manifest["files"]:
                    content = read(root, entry["name"])
                    self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"])
                    self.assertIn("/" + entry["path"], served)
                self.assertEqual(boot_guard(root, limit=1), "rolled_back")
                self.assertEqual(read(root, "main.py"), b"old-main")
                self.assertEqual(read(root, "settings.json"), b'{"protected":true}')
                for entry in manifest["files"]:
                    if entry["name"] != "main.py":
                        self.assertFalse(os.path.exists(root + "/" + entry["name"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
