"""A channel must advertise only immutable, verified release bytes in order."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import publish_channel as channel

REPO = "okayaleh/ESP32-watering-rebuild"


def entry(number):
    return {"version": "2.0.0-rebuild.%s" % number, "commit": "%040x" % number,
            "manifest_sha256": "%064x" % number, "manifest_size": 2048,
            "published_at": "2026-09-%02dT12:00:00Z" % number}


def payload(version="2.0.0-rebuild.4"):
    files = {"build/main.py": b"print('planter')\n",
             "build/version.json": json.dumps({"version": version}).encode()}
    manifest = {"version": version, "mpy": "6.3", "platform": channel.PLATFORM,
                "native_sha256": channel.NATIVE_SHA256, "files": [
        {"name": Path(path).name, "path": path, "size": len(data),
         "sha256": hashlib.sha256(data).hexdigest()} for path, data in files.items()]}
    files["build/manifest.json"] = json.dumps(manifest, indent=2).encode() + b"\n"
    return files


class ChannelPolicyTests(unittest.TestCase):
    def test_channel_keeps_current_and_two_prior_without_mutating_input(self):
        previous = None
        for number in range(1, 5):
            previous = channel.prepare_channel(REPO, entry(number), previous)
        self.assertEqual([item["version"] for item in previous["releases"]],
                         [entry(n)["version"] for n in (4, 3, 2)])
        self.assertEqual(previous["schema"], 1)
        self.assertEqual(previous["repository"], REPO)
        unchanged = json.loads(json.dumps(previous))
        self.assertEqual(channel.prepare_channel(REPO, entry(4), previous), previous)
        self.assertEqual(previous, unchanged)
        self.assertLess(len(channel.channel_bytes(previous)), channel.MAX_CHANNEL)

    def test_old_or_equal_publication_and_rewritten_versions_cannot_move_channel(self):
        previous = channel.prepare_channel(REPO, entry(4))
        candidates = [entry(3), dict(entry(5), published_at=entry(4)["published_at"]),
                      dict(entry(4), commit="a" * 40), dict(entry(4), manifest_sha256="f" * 64)]
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                channel.prepare_channel(REPO, candidate, previous)
        self.assertEqual(previous["releases"], [entry(4)])

    def test_channel_rejects_wrong_repo_corrupt_entries_and_bad_chronology(self):
        valid = channel.prepare_channel(REPO, entry(4))
        for previous in (dict(valid, repository="other/repo"), dict(valid, schema=True),
                         dict(valid, releases=[]), dict(valid, releases=[entry(3), entry(4)]),
                         dict(valid, releases=[entry(4), entry(4)])):
            with self.subTest(previous=previous), self.assertRaises(ValueError):
                channel.prepare_channel(REPO, entry(5), previous)
        for values in ({"commit": "main"}, {"manifest_sha256": "F" * 64},
                       {"manifest_size": True}, {"manifest_size": channel.MAX_MANIFEST + 1},
                       {"published_at": "2026-02-31T12:00:00Z"}, {"version": "../unsafe"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                channel.prepare_channel(REPO, dict(entry(4), **values))


class BuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        channel.git(self.root, "init", "--quiet")
        (self.root / "build").mkdir()
        (self.root / "firmware").mkdir()
        self.files = payload()
        for path, data in self.files.items():
            (self.root / path).write_bytes(data)
        native = (channel.ROOT / "firmware" / channel.NATIVE_IMAGE).read_bytes()
        (self.root / "firmware" / channel.NATIVE_IMAGE).write_bytes(native)
        (self.root / "firmware/manifest.json").write_text(json.dumps({"artifacts": [
            {"file": channel.NATIVE_IMAGE, "bytes": len(native), "flash_offset": "0x10000",
             "sha256": channel.NATIVE_SHA256}]}))
        channel.git(self.root, "config", "core.autocrlf", "false")
        channel.git(self.root, "add", "build", "firmware")
        channel.git(self.root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--quiet", "-m", "Test release")
        channel.git(self.root, "tag", "v2.0.0-rebuild.4")

    def test_identical_tagged_artifacts_pass(self):
        commit, data, manifest = channel.verify_build(self.root, "v2.0.0-rebuild.4")
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertEqual(data, self.files["build/manifest.json"])
        self.assertEqual(manifest["version"], "2.0.0-rebuild.4")

    def test_uncommitted_build_changes_fail_even_when_rehashed(self):
        (self.root / "build/main.py").write_bytes(b"changed build\n")
        with self.assertRaisesRegex(ValueError, "differs from the release commit"):
            channel.verify_build(self.root, "v2.0.0-rebuild.4")
        manifest = json.loads(self.files["build/manifest.json"])
        data = (self.root / "build/main.py").read_bytes()
        manifest["files"][0].update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        (self.root / "build/manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "differs from the release commit"):
            channel.verify_build(self.root, "v2.0.0-rebuild.4")

    def test_channel_rejects_old_or_incompatible_platform_metadata(self):
        for field, value in (("platform", None), ("platform", "other-board"),
                             ("native_sha256", None), ("native_sha256", "a" * 64)):
            manifest = json.loads(self.files["build/manifest.json"])
            if value is None:
                del manifest[field]
            else:
                manifest[field] = value
            (self.root / "build/manifest.json").write_text(json.dumps(manifest))
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "supported ROMFS"):
                channel.verify_build(self.root, "v2.0.0-rebuild.4")

    def test_native_metadata_and_image_must_match_channel_and_tag(self):
        path = self.root / "firmware/manifest.json"
        original = path.read_bytes()
        metadata = json.loads(original)
        metadata["artifacts"][0]["sha256"] = "0" * 64
        path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "Native firmware metadata"):
            channel.verify_build(self.root, "v2.0.0-rebuild.4")
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "differs from the release commit"):
            channel.verify_build(self.root, "v2.0.0-rebuild.4")
        path.write_bytes(original)
        (self.root / "firmware" / channel.NATIVE_IMAGE).write_bytes(b"corrupt native image")
        with self.assertRaisesRegex(ValueError, "Native firmware image failed"):
            channel.verify_build(self.root, "v2.0.0-rebuild.4")


class ImmutableReleaseTests(unittest.TestCase):
    def fixture(self, immutable=True, mutate=None):
        files = payload()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, data in files.items():
                archive.writestr(path, data)
        data = buffer.getvalue()
        tag = "v2.0.0-rebuild.4"
        name = "planter-2.0.0-rebuild.4.zip"
        url = "https://github.com/%s/releases/download/%s/" % (REPO, tag)
        release = {"tag_name": tag, "draft": False, "immutable": immutable,
                   "published_at": entry(4)["published_at"],
                   "assets": [{"name": asset, "browser_download_url": url + asset}
                              for asset in (name, name + ".sha256")]}
        responses = {"https://api.github.com/repos/%s/releases/tags/%s" % (REPO, tag): json.dumps(release).encode(),
                     "https://api.github.com/repos/%s/git/ref/tags/%s" % (REPO, tag):
                         json.dumps({"object": {"type": "commit", "sha": entry(4)["commit"]}}).encode(),
                     url + name: data,
                     url + name + ".sha256": (hashlib.sha256(data).hexdigest() + "  " + name + "\n").encode()}
        if mutate:
            mutate(responses, url + name)
        def fetch(url, limit, destination=None):
            data = responses[url]
            self.assertLessEqual(len(data), limit)
            if destination is not None:
                Path(destination).write_bytes(data)
            else:
                return data
        return files["build/manifest.json"], fetch

    def test_published_immutable_archive_must_match_committed_manifest(self):
        data, fetch = self.fixture()
        self.assertEqual(channel.verify_release(REPO, "v2.0.0-rebuild.4", entry(4)["commit"], data, fetch),
                         entry(4)["published_at"])
        with self.assertRaisesRegex(ValueError, "differs from the release commit"):
            channel.verify_release(REPO, "v2.0.0-rebuild.4", entry(4)["commit"], data + b" ", fetch)

    def test_mutable_or_corrupt_release_never_becomes_a_candidate(self):
        data, fetch = self.fixture(immutable=False)
        with self.assertRaisesRegex(ValueError, "immutable"):
            channel.verify_release(REPO, "v2.0.0-rebuild.4", entry(4)["commit"], data, fetch)
        data, fetch = self.fixture(mutate=lambda responses, url: responses.__setitem__(url, b"corruption"))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            channel.verify_release(REPO, "v2.0.0-rebuild.4", entry(4)["commit"], data, fetch)

    def test_remote_tag_must_resolve_to_the_verified_local_commit(self):
        data, fetch = self.fixture()
        with self.assertRaisesRegex(ValueError, "does not match"):
            channel.verify_release(REPO, "v2.0.0-rebuild.4", "a" * 40, data, fetch)
        responses = {"https://api.github.com/repos/%s/git/ref/tags/v2.0.0-rebuild.4" % REPO:
                         {"object": {"type": "tag", "sha": "b" * 40}},
                     "https://api.github.com/repos/%s/git/tags/%s" % (REPO, "b" * 40):
                         {"object": {"type": "commit", "sha": entry(4)["commit"]}}}
        fetch = lambda url, limit: json.dumps(responses[url]).encode()
        channel.verify_remote_tag(REPO, "v2.0.0-rebuild.4", entry(4)["commit"], fetch)
        responses["https://api.github.com/repos/%s/git/tags/%s" % (REPO, "b" * 40)] = {
            "object": {"type": "tag", "sha": "b" * 40}}
        with self.assertRaisesRegex(ValueError, "nesting exceeds"):
            channel.verify_remote_tag(REPO, "v2.0.0-rebuild.4", entry(4)["commit"], fetch)


class ChannelPublicationTests(unittest.TestCase):
    def test_dry_run_and_fast_forward_publication_only_touch_channel_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            remote = Path(temporary) / "remote.git"
            remote.mkdir()
            real_git = channel.git
            real_git(remote, "init", "--bare", "--quiet")
            calls = []
            def local_git(directory, *arguments):
                calls.append(arguments)
                if arguments[:3] == ("remote", "add", "origin"):
                    arguments = arguments[:3] + (str(remote),)
                return real_git(directory, *arguments)
            # Local bare repository proves the real Git mutation path without network.
            with patch.object(channel, "git", local_git):
                channel.write_channel(REPO, entry(4), publish=False)
                self.assertEqual(real_git(remote, "for-each-ref", "--format=%(refname)").strip(), b"")
                channel.write_channel(REPO, entry(4), publish=True)
                channel.write_channel(REPO, entry(5), publish=True)
                with self.assertRaisesRegex(ValueError, "older or equal"):
                    channel.write_channel(REPO, entry(3), publish=True)
            refs = real_git(remote, "for-each-ref", "--format=%(refname)").decode().splitlines()
            self.assertEqual(refs, ["refs/heads/updates"])
            published = json.loads(real_git(remote, "show", "updates:channel.json"))
            self.assertEqual([item["version"] for item in published["releases"]],
                             [entry(5)["version"], entry(4)["version"]])
            self.assertEqual(real_git(remote, "ls-tree", "--name-only", "updates"), b"channel.json\n")
            pushes = [args for args in calls if args[0] == "push"]
            self.assertEqual(pushes, [("push", "origin", "HEAD:refs/heads/updates")] * 2)


if __name__ == "__main__":
    unittest.main()
