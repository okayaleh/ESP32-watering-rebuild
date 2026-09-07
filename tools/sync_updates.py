"""Download a selected public GitHub release into a verified, immutable OTA cache."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 65536
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_MANIFEST = 16384
MAX_FILE = 512 * 1024
MAX_FILES = 48
RELEASES_PER_PAGE = 20
MAX_RELEASE_PAGES = 100
GITHUB_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com",
                "objects.githubusercontent.com"}
PROTECTED = {"boot.py", "boot.mpy", "romboot.py", "romboot.mpy", "config.py", "config.mpy",
             "secrets.py", "secrets.mpy", "credentials.py", "credentials.mpy",
             "wifi_config.py", "wifi_config.mpy"}


def github_https(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in GITHUB_HOSTS or
            parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("Release downloads require HTTPS on GitHub's download hosts")
    return url


class GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        github_https(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def download(url, limit, destination=None):
    request = Request(github_https(url), headers={"User-Agent": "Planter-release-sync",
                                                "Accept": "application/vnd.github+json"})
    opener = build_opener(GitHubRedirects())
    with opener.open(request, timeout=30) as response:
        github_https(response.geturl())
        if response.status != 200:
            raise ValueError("GitHub did not return HTTP 200")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise ValueError("Release download exceeds its size limit")
        total = 0
        data = bytearray() if destination is None else None
        stream = open(destination, "wb") if destination is not None else None
        try:
            while True:
                chunk = response.read(min(CHUNK, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError("Release download exceeds its size limit")
                if stream:
                    stream.write(chunk)
                else:
                    data.extend(chunk)
        finally:
            if stream:
                stream.close()
        if length is not None and total != int(length):
            raise ValueError("Incomplete release download")
        return bytes(data) if data is not None else total


def ota_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", value)
            or ".." in value or value.lower() in PROTECTED
            or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9]", value.split(".")[0], re.I)
            or not value.endswith((".py", ".mpy", ".html", ".gz", ".css", ".js", ".json"))
            or (value.lower().endswith(".json") and value != "version.json")):
        raise ValueError("Unsafe or protected OTA filename")
    return value


def validate_manifest(manifest, version=None):
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("version"), str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", manifest["version"])
            or (version is not None and manifest["version"] != version)):
        raise ValueError("Manifest version does not match the selected release")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_FILES:
        raise ValueError("Invalid manifest file count")
    names, total = set(), 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid manifest entry")
        name = ota_name(entry.get("name"))
        if name.lower() in names or entry.get("path") != "build/" + name:
            raise ValueError("Duplicate filename or unsafe manifest path")
        names.add(name.lower())
        size, digest = entry.get("size"), entry.get("sha256")
        if (type(size) is not int or not 0 < size <= MAX_FILE or
                not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Invalid manifest size or SHA-256")
        total += size
    if total > 2 * 1024 * 1024:
        raise ValueError("OTA payload exceeds its size limit")
    deleted = manifest.get("delete", [])
    if not isinstance(deleted, list) or len(deleted) > MAX_FILES:
        raise ValueError("Invalid manifest deletion list")
    for name in deleted:
        if ota_name(name).lower() in names:
            raise ValueError("Manifest cannot update and delete the same file")
    return manifest


def read_manifest(path, version=None):
    with open(path, "rb") as stream:
        data = stream.read(MAX_MANIFEST + 1)
    if len(data) > MAX_MANIFEST:
        raise ValueError("Manifest exceeds its size limit")
    return validate_manifest(json.loads(data), version)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_cache(directory, manifest_bytes, manifest):
    if directory.is_symlink():
        raise ValueError("Immutable cache must not be a symbolic link")
    if (directory / "build/manifest.json").read_bytes() != manifest_bytes:
        raise ValueError("Existing immutable cache has a different manifest")
    expected = {"build/manifest.json"} | {entry["path"] for entry in manifest["files"]}
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    if actual != expected or any(path.is_symlink() for path in directory.rglob("*")):
        raise ValueError("Existing immutable cache has unexpected files")
    for entry in manifest["files"]:
        path = directory / entry["path"]
        if path.stat().st_size != entry["size"] or file_hash(path) != entry["sha256"]:
            raise ValueError("Existing immutable cache failed SHA-256 verification")


def extract_verified(archive_path, directory, version):
    with zipfile.ZipFile(archive_path) as archive:
        members = {}
        for info in archive.infolist():
            name = info.filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or "\\" in name or ":" in name or
                    ".." in parts or name in members or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("Unsafe or duplicate release archive entry")
            members[name] = info
        info = members.get("build/manifest.json")
        if info is None or info.file_size > MAX_MANIFEST or info.is_dir():
            raise ValueError("Missing or oversized release manifest")
        manifest_bytes = archive.read(info)
        manifest = validate_manifest(json.loads(manifest_bytes), version)
        (directory / "build").mkdir(parents=True)
        for entry in manifest["files"]:
            info = members.get(entry["path"])
            if info is None or info.is_dir() or info.file_size != entry["size"]:
                raise ValueError("Release file is missing or has an incorrect size")
            digest, size = hashlib.sha256(), 0
            with archive.open(info) as source, (directory / entry["path"]).open("wb") as target:
                while chunk := source.read(CHUNK):
                    size += len(chunk)
                    if size > entry["size"]:
                        raise ValueError("Release file exceeds manifest size")
                    digest.update(chunk)
                    target.write(chunk)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ValueError("Release file failed SHA-256 verification")
        (directory / "build/manifest.json").write_bytes(manifest_bytes)
    return manifest_bytes, manifest


def validate_repo(repo):
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo)
            or any(part in (".", "..") for part in repo.split("/"))):
        raise ValueError("Use an explicit public repository in owner/name form")


def release_assets(repo, release, tag):
    if not re.fullmatch(r"v[A-Za-z0-9][A-Za-z0-9._-]{0,63}", tag) or ".." in tag:
        raise ValueError("Use an explicit v-prefixed release tag")
    if not isinstance(release, dict) or release.get("tag_name") != tag or release.get("draft"):
        raise ValueError("The selected public release is unavailable")
    name = "planter-" + tag[1:] + ".zip"
    urls = {}
    assets = release.get("assets")
    if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
        raise ValueError("Invalid GitHub release asset list")
    for asset in assets:
        if asset.get("name") in (name, name + ".sha256"):
            asset_name = asset["name"]
            expected = "https://github.com/%s/releases/download/%s/%s" % (repo, tag, asset_name)
            if asset_name in urls or asset.get("browser_download_url") != expected:
                raise ValueError("Unexpected release asset URL")
            urls[asset_name] = expected
    if len(urls) != 2:
        raise ValueError("Release must contain the Planter ZIP and its SHA-256 file")
    return urls


def list_releases(repo, fetch=download):
    """Return three newest published packages; metadata is not a hardware verdict."""
    validate_repo(repo)
    choices = []
    # Read all pages: GitHub's listing order need not equal publication order.
    # Keep only three small records while filtering unrelated/incomplete releases.
    for page in range(1, MAX_RELEASE_PAGES + 1):
        url = "https://api.github.com/repos/%s/releases?per_page=%s&page=%s" % (repo, RELEASES_PER_PAGE, page)
        releases = json.loads(fetch(url, 1024 * 1024))
        if not isinstance(releases, list):
            raise ValueError("GitHub returned an invalid release listing")
        for release in releases:
            if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
                continue
            try:
                urls = release_assets(repo, release, release["tag_name"])
                published = release["published_at"]
                if not isinstance(published, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", published):
                    continue
                datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
                if not isinstance(release.get("prerelease"), bool):
                    continue
                selected = [asset for asset in release["assets"] if asset["name"] in urls]
                if any(asset.get("state") != "uploaded" or type(asset.get("size")) is not int
                       or not 0 < asset["size"] <= (1024 if asset["name"].endswith(".sha256") else MAX_ARCHIVE)
                       for asset in selected):
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            candidate = {"tag": release["tag_name"], "published_at": published,
                         "prerelease": release["prerelease"]}
            previous = next((item for item in choices if item["tag"] == candidate["tag"]), None)
            if previous:
                if previous["published_at"] >= published:
                    continue
                choices.remove(previous)
            choices.append(candidate)
            choices.sort(key=lambda item: (item["published_at"], item["tag"]), reverse=True)
            del choices[3:]
        if len(releases) < RELEASES_PER_PAGE:
            return choices
    raise ValueError("Release listing exceeded the page limit; select an explicit --tag")


def print_releases(releases):
    if not releases:
        print("No published Planter updates with complete package files were found.")
        return
    print("Available updates, newest publication first:")
    for index, release in enumerate(releases):
        label = ("Newest", "Previous", "Two releases ago")[index]
        kind = "prerelease" if release["prerelease"] else "release"
        print("%s: %s | %s | published %s" % (label, release["tag"], kind, release["published_at"][:10]))
    print("These are available releases, not a record of versions tested on your controller.")
    print("Choose a listed tag with --tag to download and verify it.")


def sync_release(repo, tag, cache=None, fetch=download):
    validate_repo(repo)
    if not isinstance(tag, str) or not re.fullmatch(r"v[A-Za-z0-9][A-Za-z0-9._-]{0,63}", tag) or ".." in tag:
        raise ValueError("Use an explicit v-prefixed release tag")
    version = tag[1:]
    release = json.loads(fetch("https://api.github.com/repos/%s/releases/tags/%s" % (repo, tag), 1024 * 1024))
    urls = release_assets(repo, release, tag)
    name = "planter-" + version + ".zip"
    checksum = fetch(urls[name + ".sha256"], 1024).decode("ascii").strip().split()
    if len(checksum) != 2 or not re.fullmatch(r"[0-9a-f]{64}", checksum[0]) or checksum[1] != name:
        raise ValueError("Invalid archive SHA-256 file")
    cache_root = (Path(cache) if cache is not None else ROOT / ".tools/updates").resolve()
    parent = cache_root / repo / tag
    if not parent.resolve().is_relative_to(cache_root):
        raise ValueError("Release cache must remain inside its configured root")
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / checksum[0]
    with tempfile.TemporaryDirectory(prefix=".download-", dir=parent) as temporary:
        temporary = Path(temporary)
        archive_path = temporary / "release.zip"
        fetch(urls[name], MAX_ARCHIVE, archive_path)
        if file_hash(archive_path) != checksum[0]:
            raise ValueError("Release archive failed SHA-256 verification")
        prepared = temporary / "verified"
        manifest_bytes, manifest = extract_verified(archive_path, prepared, version)
        if directory.exists():
            verify_cache(directory, manifest_bytes, manifest)
        else:
            prepared.rename(directory)
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Public GitHub owner/repository")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--tag", help="Explicit release tag to download, including v prefix")
    action.add_argument("--list", action="store_true", help="Show newest update and two previous available releases")
    parser.add_argument("--cache", type=Path, help="Cache root (default: .tools/updates)")
    args = parser.parse_args()
    try:
        if args.list:
            print_releases(list_releases(args.repo))
        else:
            print(sync_release(args.repo, args.tag, args.cache))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        parser.exit(1, "Release %s failed: %s\n" % ("listing" if args.list else "sync", exc))
