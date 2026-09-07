"""Release verification must finish before files become available to the mirror."""
import hashlib
from http.server import HTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mirror
import sync_updates as sync


REPO = "supercrossed/ESP32-watering-rebuild"
TAG = "v2.0.0-rebuild.2"
NAME = "planter-2.0.0-rebuild.2.zip"
API = "https://api.github.com/repos/%s/releases/tags/%s" % (REPO, TAG)
ASSETS = "https://github.com/%s/releases/download/%s/" % (REPO, TAG)


def fixture(manifest_edit=None, extra=None, corrupt_file=False):
    files = {"build/main.py": b"print('application')\n",
             "build/version.json": b'{"version":"2.0.0-rebuild.2"}'}
    manifest = {"version": TAG[1:], "mpy": "6.3", "files": [
        {"name": Path(path).name, "path": path, "size": len(data),
         "sha256": hashlib.sha256(data).hexdigest()} for path, data in files.items()]}
    if manifest_edit:
        manifest_edit(manifest)
    if corrupt_file:
        files["build/main.py"] = files["build/main.py"].replace(b"print", b"wrong")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("build/manifest.json", json.dumps(manifest))
        for path, data in files.items():
            archive.writestr(path, data)
        # A release package includes these for USB use; never copy them to OTA.
        for path in ("build/config.py", "build/boot.py", "build/romboot.py", "firmware/platform.bin", "src/main.py"):
            archive.writestr(path, b"not an OTA artifact")
        if extra:
            archive.writestr(*extra)
    archive = output.getvalue()
    checksum = hashlib.sha256(archive).hexdigest()
    release = {"tag_name": TAG, "draft": False, "prerelease": True,
               "assets": [{"name": name, "browser_download_url": ASSETS + name}
                          for name in (NAME, NAME + ".sha256")]}
    responses = {API: json.dumps(release).encode(), ASSETS + NAME: archive,
                 ASSETS + NAME + ".sha256": (checksum + "  " + NAME + "\n").encode()}
    return responses, manifest, checksum


class ReleaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)

    def fetcher(self, responses):
        def fetch(url, limit, destination=None):
            self.assertTrue(url.startswith("https://"))
            data = responses[url]
            self.assertLessEqual(len(data), limit)
            if destination:
                Path(destination).write_bytes(data)
            else:
                return data
        return fetch

    def run_sync(self, responses):
        return sync.sync_release(REPO, TAG, self.cache, self.fetcher(responses))

    def assert_no_committed_cache(self):
        self.assertFalse(any(self.cache.rglob("manifest.json")))
        self.assertFalse(any(self.cache.rglob(".download-*")))

    def test_selected_prerelease_extracts_only_verified_ota_files(self):
        responses, manifest, digest = fixture()
        directory = self.run_sync(responses)
        self.assertEqual(directory, self.cache / REPO / TAG / digest)
        self.assertEqual(sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()),
                         ["build/main.py", "build/manifest.json", "build/version.json"])
        self.assertEqual(sync.read_manifest(directory / "build/manifest.json"), manifest)

    def test_archive_hash_mismatch_never_commits(self):
        responses, _, _ = fixture()
        responses[ASSETS + NAME] += b"corruption"
        with self.assertRaisesRegex(ValueError, "archive failed SHA-256"):
            self.run_sync(responses)
        self.assert_no_committed_cache()

    def test_artifact_hash_mismatch_never_commits(self):
        responses, _, _ = fixture(corrupt_file=True)
        with self.assertRaisesRegex(ValueError, "file failed SHA-256"):
            self.run_sync(responses)
        self.assert_no_committed_cache()

    def test_unsafe_and_protected_manifest_entries_are_rejected(self):
        for name, path in (("../escape.py", "build/../escape.py"),
                           ("main.py", "../main.py"), ("main.py", "src/main.py"),
                           ("config.py", "build/config.py"), ("boot.py", "build/boot.py"),
                           ("romboot.mpy", "build/romboot.mpy"),
                           ("settings.json", "build/settings.json"),
                           ("platform.bin", "build/platform.bin")):
            with self.subTest(name=name, path=path):
                responses, _, _ = fixture(lambda value: value["files"][0].update(name=name, path=path))
                with self.assertRaises(ValueError):
                    self.run_sync(responses)
                self.assert_no_committed_cache()

    def test_archive_traversal_and_duplicate_entries_are_rejected(self):
        for extra in (("../../escape.py", b"unsafe"), ("build/main.py", b"duplicate")):
            with self.subTest(path=extra[0]):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    responses, _, _ = fixture(extra=extra)
                with self.assertRaisesRegex(ValueError, "Unsafe or duplicate"):
                    self.run_sync(responses)
                self.assert_no_committed_cache()

    def test_existing_verified_cache_is_reused_without_rewriting(self):
        responses, _, _ = fixture()
        directory = self.run_sync(responses)
        path = directory / "build/main.py"
        before = path.stat().st_mtime_ns
        self.assertEqual(self.run_sync(responses), directory)
        self.assertEqual(path.stat().st_mtime_ns, before)
        path.write_bytes(b"local tampering")
        with self.assertRaisesRegex(ValueError, "immutable cache failed SHA-256"):
            self.run_sync(responses)
        self.assertEqual(path.read_bytes(), b"local tampering")

    def test_changed_release_asset_gets_new_directory_preserving_old_cache(self):
        responses, _, _ = fixture()
        old = self.run_sync(responses)
        original = (old / "build/manifest.json").read_bytes()
        new_responses, _, _ = fixture(extra=("docs/change.md", b"release metadata"))
        new = self.run_sync(new_responses)
        self.assertNotEqual(new, old)
        self.assertEqual((old / "build/manifest.json").read_bytes(), original)

    def test_release_urls_and_redirects_cannot_leave_github_https(self):
        for url in ("http://github.com/a", "https://example.com/a",
                    "https://github.com.evil.example/a", "https://user@github.com/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                sync.github_https(url)
        sync.github_https("https://release-assets.githubusercontent.com/file?signature=example")
        responses, _, _ = fixture()
        release = json.loads(responses[API])
        release["assets"][0]["browser_download_url"] = "https://github.com/other/repo/releases/download/v1/file.zip"
        responses[API] = json.dumps(release).encode()
        with self.assertRaisesRegex(ValueError, "Unexpected release asset URL"):
            self.run_sync(responses)
        self.assert_no_committed_cache()

    def test_manifest_version_and_protected_deletions_rejected(self):
        for edit in (lambda value: value.update(version="unrelated"),
                     lambda value: value.update(delete=["config.py"]),
                     lambda value: value["files"].append(dict(value["files"][0]))):
            responses, _, _ = fixture(edit)
            with self.assertRaises(ValueError):
                self.run_sync(responses)
            self.assert_no_committed_cache()

    def test_mirror_serves_selected_directory_with_fixed_lengths_and_no_private_paths(self):
        responses, manifest, _ = fixture()
        directory = self.run_sync(responses)
        (directory / "build/config.py").write_text("private configuration")
        class QuietMirror(mirror.Mirror):
            def log_message(self, *args): pass
        server = HTTPServer(("127.0.0.1", 0), QuietMirror)
        server.directory = directory
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = "http://127.0.0.1:%s" % server.server_port
            for path in ("build/manifest.json", "build/main.py"):
                with urlopen(base + "/" + path, timeout=2) as response:
                    data = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(int(response.headers["Content-Length"]), len(data))
                    self.assertEqual(data, (directory / path).read_bytes())
            for path in ("/build/config.py", "/build/boot.py", "/build/romboot.py",
                         "/build/../src/main.py", "/build/%2e%2e/src/main.py", "/build/main.py?extra=1"):
                with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                    urlopen(base + path, timeout=2)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            manifest["files"][0].update(name="config.py", path="build/config.py")
            (directory / "build/manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/build/config.py", timeout=2)
            self.assertEqual(error.exception.code, 503)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
