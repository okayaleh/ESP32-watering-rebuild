"""Publish a small GitHub OTA channel after verifying an immutable release.

The channel refers to the exact source commit, never to mutable main files.
Default operation is a dry run; --publish pushes only refs/heads/updates.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from sync_updates import (MAX_ARCHIVE, MAX_MANIFEST, download, extract_verified,
                          file_hash, release_assets, validate_manifest, validate_repo)

ROOT = Path(__file__).resolve().parents[1]
CHANNEL_BRANCH = "updates"
MAX_CHANNEL = 4096
PLATFORM = "planter-esp32-romfs-mpy6"
NATIVE_SHA256 = "9369e9e2eba45a9828d3d8c929c2b39ddeb41987318d8aa092aa130908780073"
NATIVE_IMAGE = "planter-esp32-1.28.0-romfs.app-bin"


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValueError("Channel releases require a UTC publication timestamp")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def validate_entry(entry):
    if (not isinstance(entry, dict) or set(entry) != {
            "version", "commit", "manifest_sha256", "manifest_size", "published_at"}):
        raise ValueError("Invalid channel release fields")
    if (not isinstance(entry["version"], str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", entry["version"]) or
            ".." in entry["version"]):
        raise ValueError("Invalid channel version")
    for name, length in (("commit", 40), ("manifest_sha256", 64)):
        if not isinstance(entry[name], str) or not re.fullmatch("[0-9a-f]{%s}" % length, entry[name]):
            raise ValueError("Invalid channel " + name)
    if type(entry["manifest_size"]) is not int or not 0 < entry["manifest_size"] <= MAX_MANIFEST:
        raise ValueError("Invalid channel manifest size")
    timestamp(entry["published_at"])
    return entry


def prepare_channel(repository, candidate, previous=None):
    """Retain current + two prior entries, rejecting stale or rewritten releases."""
    validate_repo(repository)
    validate_entry(candidate)
    releases = []
    if previous is not None:
        if (not isinstance(previous, dict) or set(previous) != {"schema", "repository", "releases"} or
                type(previous["schema"]) is not int or previous["schema"] != 1 or
                previous["repository"] != repository or not isinstance(previous["releases"], list) or
                not 0 < len(previous["releases"]) <= 3):
            raise ValueError("Invalid existing channel")
        releases = [dict(validate_entry(entry)) for entry in previous["releases"]]
        if len({entry["version"] for entry in releases}) != len(releases):
            raise ValueError("Duplicate channel version")
        if any(left["published_at"] <= right["published_at"] for left, right in zip(releases, releases[1:])):
            raise ValueError("Channel publication timestamps must descend")
        same = next((entry for entry in releases if entry["version"] == candidate["version"]), None)
        if same is not None and same != candidate:
            raise ValueError("Refusing to rewrite an existing channel release")
        if releases[0] == candidate:
            return {"schema": 1, "repository": repository, "releases": releases}
        if candidate["published_at"] <= releases[0]["published_at"]:
            raise ValueError("Refusing an older or equal publication timestamp; channel must advance")
    result = {"schema": 1, "repository": repository,
              "releases": [dict(candidate)] + releases[:2]}
    if len(channel_bytes(result)) > MAX_CHANNEL:
        raise ValueError("Channel exceeds its size limit")
    return result


def channel_bytes(channel):
    return (json.dumps(channel, indent=2) + "\n").encode("utf-8")


def git_executable():
    path = shutil.which("git") or ROOT / ".tools/git/cmd/git.exe"
    if not Path(path).exists():
        raise ValueError("Git is required to verify and publish a channel")
    return str(path)


def git(directory, *arguments):
    result = subprocess.run([git_executable(), "-C", str(directory), *arguments],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    return result.stdout


def verify_build(source, tag):
    """Require all OTA bytes in the rebuilt output to match the immutable tag tree."""
    if not isinstance(tag, str) or not re.fullmatch(r"v[A-Za-z0-9][A-Za-z0-9._-]{0,63}", tag) or ".." in tag:
        raise ValueError("Use an explicit v-prefixed release tag")
    source = Path(source)
    commit = git(source, "rev-parse", "--verify", "refs/tags/" + tag + "^{commit}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Release must resolve to an immutable 40-character commit")
    manifest_path = source / "build/manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Build files must not be symbolic links")
    data = manifest_path.read_bytes()
    if not 0 < len(data) <= MAX_MANIFEST:
        raise ValueError("Invalid manifest size")
    manifest = validate_manifest(json.loads(data), tag[1:])
    if manifest.get("platform") != PLATFORM or manifest.get("native_sha256") != NATIVE_SHA256:
        raise ValueError("The GitHub channel requires the supported ROMFS platform and native image")
    native_manifest = json.loads((source / "firmware/manifest.json").read_bytes())
    artifacts = native_manifest.get("artifacts") if isinstance(native_manifest, dict) else None
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        raise ValueError("Invalid native firmware manifest")
    native = [item for item in artifacts if item.get("file") == NATIVE_IMAGE]
    if (len(native) != 1 or native[0].get("sha256") != NATIVE_SHA256 or
            native[0].get("flash_offset") != "0x10000" or type(native[0].get("bytes")) is not int or
            native[0]["bytes"] <= 0):
        raise ValueError("Native firmware metadata does not match this update channel")
    native_path = source / "firmware" / NATIVE_IMAGE
    if native_path.stat().st_size != native[0]["bytes"] or file_hash(native_path) != NATIVE_SHA256:
        raise ValueError("Native firmware image failed platform SHA-256 or size verification")
    paths = ["build/manifest.json", "firmware/manifest.json", "firmware/" + NATIVE_IMAGE] + [
        entry["path"] for entry in manifest["files"]]
    tree = git(source, "ls-tree", "-z", commit, "--", *paths)
    modes = {}
    for record in tree.split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            modes[path.decode()] = metadata.split()[0]
    for path in paths:
        local = source / path
        if local.is_symlink() or modes.get(path) not in (b"100644", b"100755"):
            raise ValueError("OTA artifact must be an ordinary tracked file: " + path)
        payload = local.read_bytes()
        if payload != git(source, "show", commit + ":" + path):
            raise ValueError("Rebuilt OTA artifact differs from the release commit: " + path)
    for entry in manifest["files"]:
        payload = (source / entry["path"]).read_bytes()
        if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError("Manifest hash mismatch: " + entry["path"])
    return commit, data, manifest


def verify_remote_tag(repository, tag, commit, fetch):
    target = json.loads(fetch("https://api.github.com/repos/%s/git/ref/tags/%s" %
                              (repository, tag), 16384)).get("object")
    # Annotated tags can refer to another tag. Bound both depth and responses.
    for _ in range(5):
        if (not isinstance(target, dict) or not isinstance(target.get("sha"), str) or
                not re.fullmatch(r"[0-9a-f]{40}", target["sha"])):
            raise ValueError("Invalid remote release tag object")
        if target.get("type") == "commit":
            if target["sha"] != commit:
                raise ValueError("Local release tag does not match the immutable GitHub tag")
            return
        if target.get("type") != "tag":
            raise ValueError("Remote release tag must resolve to a commit")
        target = json.loads(fetch("https://api.github.com/repos/%s/git/tags/%s" %
                                  (repository, target["sha"]), 16384)).get("object")
    raise ValueError("Remote release tag nesting exceeds its limit")


def verify_release(repository, tag, commit, manifest_data, fetch=download):
    """Verify the published immutable package and SHA before advertising any bytes."""
    validate_repo(repository)
    release = json.loads(fetch("https://api.github.com/repos/%s/releases/tags/%s" %
                               (repository, tag), 1024 * 1024))
    urls = release_assets(repository, release, tag)
    if release.get("immutable") is not True:
        raise ValueError("Release must be published and immutable before channel publication")
    published_at = timestamp(release.get("published_at"))
    verify_remote_tag(repository, tag, commit, fetch)
    name = "planter-" + tag[1:] + ".zip"
    checksum = fetch(urls[name + ".sha256"], 1024).decode("ascii").strip().split()
    if len(checksum) != 2 or not re.fullmatch(r"[0-9a-f]{64}", checksum[0]) or checksum[1] != name:
        raise ValueError("Invalid published archive checksum")
    with tempfile.TemporaryDirectory(prefix="planter-channel-package-") as temporary:
        archive = Path(temporary) / "release.zip"
        fetch(urls[name], MAX_ARCHIVE, archive)
        if file_hash(archive) != checksum[0]:
            raise ValueError("Published archive failed SHA-256 verification")
        published_data, _ = extract_verified(archive, Path(temporary) / "verified", tag[1:])
        if published_data != manifest_data:
            raise ValueError("Published package manifest differs from the release commit")
    return published_at


def write_channel(repository, candidate, publish=False, work_parent=None):
    """Prepare an isolated branch; a normal push rejects concurrent writers safely."""
    validate_repo(repository)
    remote = "https://github.com/" + repository + ".git"
    with tempfile.TemporaryDirectory(prefix="planter-channel-", dir=work_parent) as temporary:
        work = Path(temporary)
        git(work, "init", "--quiet")
        git(work, "remote", "add", "origin", remote)
        remote_head = git(work, "ls-remote", "--heads", "origin", "refs/heads/" + CHANNEL_BRANCH).strip()
        previous = None
        if remote_head:
            git(work, "fetch", "--quiet", "--depth=1", "origin", "refs/heads/" + CHANNEL_BRANCH)
            git(work, "checkout", "--quiet", "-b", CHANNEL_BRANCH, "FETCH_HEAD")
            names = git(work, "ls-tree", "--name-only", "HEAD").decode().splitlines()
            if names != ["channel.json"]:
                raise ValueError("The updates branch may contain only channel.json")
            existing = work / "channel.json"
            if existing.is_symlink() or existing.stat().st_size > MAX_CHANNEL:
                raise ValueError("Invalid existing channel file")
            previous = json.loads(existing.read_bytes())
        else:
            git(work, "checkout", "--quiet", "--orphan", CHANNEL_BRANCH)
        channel = prepare_channel(repository, candidate, previous)
        if not publish or channel == previous:
            return channel
        (work / "channel.json").write_bytes(channel_bytes(channel))
        git(work, "add", "--", "channel.json")
        git(work, "-c", "user.name=github-actions[bot]", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "--quiet",
            "-m", "Advertise verified release v" + candidate["version"])
        gh = shutil.which("gh") or ROOT / ".tools/gh/bin/gh.exe"
        if not Path(gh).exists():
            raise ValueError("Authenticated GitHub CLI is required to publish the channel")
        # Use the existing gh login (or Actions GH_TOKEN), never print or copy
        # credentials into the branch. Configuration lives only in this temp repo.
        helper = '!"' + Path(gh).as_posix() + '" auth git-credential'
        git(work, "config", "--add", "credential.helper", "")
        git(work, "config", "--add", "credential.helper", helper)
        # Never force push. A racing update fails without changing the channel;
        # rerun to re-read the winner and reapply the monotonic timestamp rule.
        git(work, "push", "origin", "HEAD:refs/heads/" + CHANNEL_BRANCH)
        return channel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source", type=Path, default=ROOT)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify-build-only", action="store_true",
                        help="Before publishing a release, compare rebuild with its tag")
    action.add_argument("--publish", action="store_true", help="Fast-forward the updates branch after all checks")
    args = parser.parse_args()
    try:
        validate_repo(args.repo)
        commit, data, _ = verify_build(args.source, args.tag)
        if args.verify_build_only:
            print("Verified tagged OTA artifacts: " + commit)
            return
        published_at = verify_release(args.repo, args.tag, commit, data)
        candidate = {"version": args.tag[1:], "commit": commit,
                     "manifest_sha256": hashlib.sha256(data).hexdigest(),
                     "manifest_size": len(data), "published_at": published_at}
        print(channel_bytes(write_channel(args.repo, candidate, args.publish)).decode(), end="")
        print("Channel published or already current." if args.publish else "Dry run: channel was not changed.")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Channel publication failed: %s\n" % exc)


if __name__ == "__main__":
    main()
