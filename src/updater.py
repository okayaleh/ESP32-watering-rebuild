"""Bounded plain-HTTP staging with an immutable boot recovery journal.

Hashes detect damaged transfers, not a hostile mirror. Use a trusted LAN
mirror; authentication of release manifests is a separate deployment concern.
"""
import os
import sys
import socket
try:
    import select
except ImportError:
    import uselect as select
try:
    import hashlib
except ImportError:
    import uhashlib as hashlib
try:
    import binascii
except ImportError:
    import ubinascii as binascii
from compat import ticks_diff, json, epoch
from persistence import atomic_json, read_json, exists, sync

CHUNK = 512
MAX_FILE = 512 * 1024
MAX_MANIFEST = 16384
MAX_FILES = 48
POLLIN = getattr(select, "POLLIN", 1)
POLLOUT = getattr(select, "POLLOUT", 4)
POLLERR = getattr(select, "POLLERR", 8)
POLLHUP = getattr(select, "POLLHUP", 16)
JOURNAL = ".ota-journal.json"
PROTECTED = ("boot.py", "boot.mpy", "romboot.py", "romboot.mpy", "config.py", "config.mpy", "wifi.json",
             "_rom_state.json",
             "settings.json", "watering_state.json", "history.json",
             "state.json", "manifest.json")


def _path(root, name):
    return root.rstrip("/") + "/" + name


def _remove(path):
    if exists(path):
        os.remove(path)


def _hex(digest):
    return binascii.hexlify(digest.digest()).decode()


def _check_mpy_header(data):
    # This build emits portable bytecode only, with 31-bit small integers.
    # Native/viper bundles require board-specific compatibility validation.
    supported = getattr(sys.implementation, "_mpy", 6) & 255
    if (len(data) < 4 or data[0] != 77 or data[1] != supported or
            data[1] != 6 or data[2] & 0xfc or data[3] > 31):
        raise ValueError("Incompatible .mpy file; use portable v6 bytecode")


def validate_filename(name):
    if not isinstance(name, str) or not name or len(name) > 64:
        raise ValueError("Invalid update filename")
    if name.startswith(".") or ".." in name or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for ch in name):
        raise ValueError("Update filenames must be flat and safe")
    if name.lower() in PROTECTED or (name.lower().endswith(".json") and name != "version.json"):
        raise ValueError("Protected device file: " + name)
    if not name.endswith((".py", ".mpy", ".html", ".gz", ".css", ".js", ".json")):
        raise ValueError("Unsupported update file type")
    return name


def _ipv4(value):
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _url(url):
    if not isinstance(url, str) or not url.startswith("http://"):
        raise ValueError("Updates require a plain HTTP mirror")
    tail = url[7:]
    authority, _, path = tail.partition("/")
    host, sep, port = authority.partition(":")
    if not host or "@" in authority or len(host) > 253:
        raise ValueError("Invalid mirror host")
    if any(ch in url for ch in ("\r", "\n", " ", "\t", "#")):
        raise ValueError("Invalid mirror URL")
    port = int(port) if sep and port.isdigit() else 80 if not sep else 0
    if not 1 <= port <= 65535:
        raise ValueError("Invalid mirror port")
    return host, port, "/" + path


class HTTPDownload:
    """One socket operation per poll, 512-byte writes, absolute deadline.

    DNS uses an asynchronous resolver callback. Content-Length is mandatory;
    redirects, transfer-encoding and HTTPS are deliberately rejected.
    """
    def __init__(self, url, destination, limit, now_ms, resolver=None,
                 timeout_ms=120000, socket_module=None, poll_factory=None):
        self.host, self.port, self.path = _url(url)
        self.destination = destination
        self.limit = limit
        self.start = now_ms
        self.timeout_ms = timeout_ms
        self.resolver = resolver
        self.socket_module = socket_module or socket
        self.poll_factory = poll_factory or select.poll
        self.sock = None
        self.poller = None
        self.file = None
        self.phase = "resolve"
        self.request = ("GET %s HTTP/1.0\r\nHost: %s:%s\r\nConnection: close\r\nAccept-Encoding: identity\r\n\r\n" %
                        (self.path, self.host, self.port)).encode()
        if len(self.request) > 2048:
            raise ValueError("Mirror path too long")
        self.offset = 0
        self.header = bytearray()
        self.expected = None
        self.size = 0
        self.hash = hashlib.sha256()
        self.done = False
        self.error = None

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.file:
            self.file.close()
            self.file = None

    def abort(self):
        self.close()
        _remove(self.destination)

    def _body(self, data):
        if not data:
            return
        self.size += len(data)
        if self.size > self.limit or self.size > self.expected:
            raise ValueError("Download exceeds declared or allowed size")
        self.file.write(data)
        self.hash.update(data)
        if self.size == self.expected:
            self.file.flush()
            self.close()
            sync()
            self.done = True

    def poll(self, now_ms):
        if self.done or self.error:
            return
        try:
            if ticks_diff(now_ms, self.start) >= self.timeout_ms:
                raise OSError("Update transfer deadline")
            if self.phase == "resolve":
                ip = self.host if _ipv4(self.host) else self.resolver(self.host) if self.resolver else None
                if ip is None:
                    if self.resolver is None:
                        raise ValueError("Configure a numeric mirror or asynchronous DNS resolver")
                    return
                if not _ipv4(ip):
                    raise ValueError("Resolver did not return IPv4")
                self.sock = self.socket_module.socket()
                self.sock.setblocking(False)
                self.poller = self.poll_factory()
                self.poller.register(self.sock, POLLOUT | POLLERR | POLLHUP)
                try:
                    self.sock.connect((ip, self.port))
                except OSError as exc:
                    if exc.args[0] not in (11, 35, 114, 115, 119, 120, 10035, 10036, 10037):
                        raise
                self.phase = "send"
                return
            if not self.poller.poll(0):
                return
            if self.phase == "send":
                count = self.sock.send(self.request[self.offset:self.offset + CHUNK])
                if not count:
                    raise OSError("Mirror closed during request")
                self.offset += count
                if self.offset == len(self.request):
                    self.poller.modify(self.sock, POLLIN | POLLERR | POLLHUP)
                    self.phase = "header"
                return
            data = self.sock.recv(CHUNK)
            if not data:
                raise OSError("Truncated update transfer")
            if self.phase == "header":
                self.header.extend(data)
                position = self.header.find(b"\r\n\r\n")
                if position < 0:
                    if len(self.header) > 2048:
                        raise ValueError("Mirror response headers too large")
                    return
                if position > 2048:
                    raise ValueError("Mirror response headers too large")
                lines = bytes(self.header[:position]).split(b"\r\n")
                if len(lines[0].split()) < 2 or lines[0].split()[1] != b"200":
                    raise ValueError("Mirror must return HTTP 200 without redirects")
                lengths = []
                for line in lines[1:]:
                    key, sep, value = line.partition(b":")
                    if not sep:
                        raise ValueError("Malformed mirror header")
                    if key.lower() == b"transfer-encoding":
                        raise ValueError("Mirror must send Content-Length, not chunked data")
                    if key.lower() == b"content-length":
                        value = value.strip()
                        if not value.isdigit() or len(value) > 9:
                            raise ValueError("Invalid mirror Content-Length")
                        lengths.append(int(value))
                if len(lengths) != 1 or not 0 < lengths[0] <= self.limit:
                    raise ValueError("Missing, duplicate or oversized Content-Length")
                self.expected = lengths[0]
                self.file = open(self.destination, "wb")
                self.phase = "body"
                body = bytes(self.header[position + 4:])
                self.header = None
                self._body(body)
            else:
                self._body(data)
        except OSError as exc:
            if exc.args and exc.args[0] in (11, 35, 10035):
                return
            self.error = str(exc)
            self.abort()
        except Exception as exc:
            self.error = str(exc)
            self.abort()


class UploadSink:
    def __init__(self, updater, name):
        self.updater = updater
        self.name = validate_filename(name)
        self.path = _path(updater.stage, "new-" + self.name)
        self.file = open(self.path, "wb")
        self.hash = hashlib.sha256()
        self.size = 0
        self.closed = False

    def write(self, data):
        if self.closed or len(data) > CHUNK or self.size + len(data) > MAX_FILE:
            raise ValueError("Upload chunk or file too large")
        self.file.write(data)
        self.hash.update(data)
        self.size += len(data)

    def finish(self):
        if self.closed or not self.size:
            raise ValueError("Upload is closed or empty")
        self.file.flush()
        self.file.close()
        self.closed = True
        sync()
        entry = {"name": self.name, "size": self.size, "sha256": _hex(self.hash)}
        self.updater.uploads[self.name] = entry
        self.updater.status.update({"busy": False, "state": "uploaded",
                                    "available": True, "error": None,
                                    "available_version": "uploaded files",
                                    "files": list(self.updater.uploads)})
        return {"staged": self.name, "size": self.size, "install_required": True}

    def abort(self):
        if not self.closed:
            self.file.close()
            self.closed = True
            _remove(self.path)
        self.updater.status["busy"] = False


class Updater:
    def __init__(self, root=".", base_url="", manifest_path="build/manifest.json",
                 close_valves=None, idle=None, resolver=None, timeout_ms=120000):
        self.root = root
        self.stage = _path(root, ".ota")
        if not exists(self.stage):
            os.mkdir(self.stage)
        self.base_url = base_url.rstrip("/")
        self.manifest_path = manifest_path.lstrip("/")
        self.close_valves = close_valves
        self.idle = idle
        self.resolver = resolver
        self.timeout_ms = timeout_ms
        version_info = read_json(_path(root, "version.json"), {})
        installed_version = version_info.get("version", "unknown") if isinstance(version_info, dict) else str(version_info)
        previous = read_json(_path(root, JOURNAL), {})
        self.status = {"busy": False, "state": "idle", "available": False,
                       "version": None, "error": None, "files": [],
                       "installed_version": installed_version,
                       "available_version": None, "last_check": None,
                       "last_install": previous.get("installed_at") if isinstance(previous, dict) else None}
        self.reboot_required = False
        self.phase = "idle"
        self.transfer = None
        self.entries = []
        self.changed = []
        self.deletions = []
        self.uploads = {}
        self.index = 0
        self.file = None
        self.target = None
        self.digest = None
        self.hashed_size = 0
        self.journal = None
        self.cleanup_names = []
        self.after_cleanup = None

    def _require_idle(self):
        if self.status["busy"] or self.reboot_required:
            raise ValueError("Updater is already busy")
        journal = read_json(_path(self.root, JOURNAL))
        if journal and journal.get("phase") not in ("stable", "rolled_back"):
            raise ValueError("Previous firmware must pass its boot trial first")

    def request_check(self):
        self._require_idle()
        _url(self.base_url)
        self.uploads = {}
        self.changed = []
        self.status.update({"busy": True, "state": "checking", "error": None,
                            "available": False, "files": []})
        self.cleanup_names = list(os.listdir(self.stage))
        self.after_cleanup = "manifest_start"
        self.phase = "cleanup"

    def begin_upload(self, filename):
        self._require_idle()
        if len(self.uploads) >= MAX_FILES and filename not in self.uploads:
            raise ValueError("Too many uploaded files")
        validate_filename(filename)
        self.status["busy"] = True
        self.status["state"] = "uploading"
        return UploadSink(self, filename)

    def request_install(self):
        self._require_idle()
        if not self.status["available"]:
            raise ValueError("No checked update or uploaded files")
        if not self.close_valves or not self.idle:
            raise ValueError("Valve safety callbacks are required for installation")
        if self.uploads:
            self.changed = list(self.uploads.values())
            self.deletions = []
            self.after_cleanup = "verify"
        else:
            self.after_cleanup = "download_start"
        keep = {"new-" + name for name in self.uploads}
        self.cleanup_names = [name for name in os.listdir(self.stage) if name not in keep]
        self.phase = "cleanup"
        self.index = 0
        self.status.update({"busy": True, "state": "downloading", "error": None})

    def _download(self, repo_path, dest, limit, now_ms):
        if not isinstance(repo_path, str) or not repo_path or repo_path.startswith("/"):
            raise ValueError("Manifest paths must be relative to the mirror")
        if ".." in repo_path.split("/") or any(ch in repo_path for ch in ("\\", "?", "#", ":")):
            raise ValueError("Unsafe manifest repository path")
        self.transfer = HTTPDownload(self.base_url + "/" + repo_path, dest,
            limit, now_ms, self.resolver, self.timeout_ms)

    def _parse_manifest(self):
        with open(_path(self.stage, "manifest"), "r") as stream:
            manifest = json.load(stream)
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be an object")
        files = manifest.get("files", [])
        if isinstance(files, dict):
            files = [dict(value, name=name) for name, value in files.items()]
        if not isinstance(files, list) or not 0 < len(files) <= MAX_FILES:
            raise ValueError("Invalid manifest file count")
        names = set()
        total = 0
        for entry in files:
            if not isinstance(entry, dict):
                raise ValueError("Invalid manifest entry")
            name = validate_filename(entry.get("name"))
            if name in names:
                raise ValueError("Duplicate manifest filename")
            names.add(name)
            digest, size = entry.get("sha256"), entry.get("size")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid SHA-256")
            if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_FILE:
                raise ValueError("Invalid manifest file size")
            path = entry.get("path")
            if not isinstance(path, str) or not path or len(path) > 256:
                raise ValueError("Every file requires a repository path")
            total += size
        if total > 2 * 1024 * 1024:
            raise ValueError("Update is too large")
        deletes = manifest.get("delete", [])
        if not isinstance(deletes, list) or len(deletes) > MAX_FILES:
            raise ValueError("Invalid deletion list")
        for name in deletes:
            validate_filename(name)
            if name in names:
                raise ValueError("Cannot update and delete the same file")
        # An old source can shadow an identical, already-downloaded .mpy too.
        for name in names:
            if name.endswith(".mpy"):
                sibling = name[:-4] + ".py"
                if sibling not in names and exists(_path(self.root, sibling)):
                    validate_filename(sibling)
                    deletes.append(sibling)
        self.entries = files
        self.deletions = list(set(deletes))
        self.status["version"] = str(manifest.get("version", "unknown"))[:64]
        self.status["available_version"] = self.status["version"]
        self.index = 0

    def _prepare(self):
        if self.close_valves() is False:
            raise OSError("Valves did not confirm closed")
        if not self.idle():
            raise OSError("Valves did not confirm closed")
        # Delete source siblings that would otherwise shadow new .mpy modules.
        names = {entry["name"] for entry in self.changed}
        deletes = set(self.deletions)
        for name in names:
            if name.endswith(".mpy"):
                sibling = name[:-4] + ".py"
                if sibling not in names and exists(_path(self.root, sibling)):
                    validate_filename(sibling)
                    deletes.add(sibling)
        entries = [{"name": name, "old": exists(_path(self.root, name)),
                    "delete": name in deletes}
                   for name in sorted(names | deletes)]
        if len(entries) > MAX_FILES * 2:
            raise ValueError("Too many transaction entries")
        self.journal = {"phase": "committing", "boots": 0, "entries": entries,
                        "version": self.status["available_version"]}
        self.index = 0
        self.phase = "backup"
        self.status["state"] = "preparing"

    def _close_files(self):
        for name in ("file", "target"):
            stream = getattr(self, name)
            if stream:
                stream.close()
                setattr(self, name, None)

    def poll(self, now_ms):
        try:
            self._poll(now_ms)
        except Exception as exc:
            self._close_files()
            if self.transfer:
                self.transfer.abort()
                self.transfer = None
            self.status.update({"busy": False, "state": "error", "error": str(exc)})
            journal = read_json(_path(self.root, JOURNAL))
            if journal and journal.get("phase") in ("committing", "pending", "rolling_back"):
                # Recovery belongs to immutable boot code; never continue
                # running an installation with only some modules replaced.
                self.reboot_required = True
            self.phase = "idle"

    def _poll(self, now_ms):
        if self.phase == "idle":
            return
        if self.phase == "cleanup":
            if self.cleanup_names:
                name = self.cleanup_names.pop()
                if name == "manifest" or name.startswith(("new-", "old-")):
                    _remove(_path(self.stage, name))
            else:
                self.phase = self.after_cleanup
            return
        if self.phase == "manifest_start":
            self._download(self.manifest_path, _path(self.stage, "manifest"), MAX_MANIFEST, now_ms)
            self.phase = "manifest"
            return
        if self.phase in ("manifest", "download"):
            self.transfer.poll(now_ms)
            if self.transfer.error:
                raise OSError(self.transfer.error)
            if not self.transfer.done:
                return
            if self.phase == "manifest":
                self._parse_manifest()
                self.phase = "hash"
            else:
                entry = self.changed[self.index]
                if self.transfer.size != entry["size"] or _hex(self.transfer.hash) != entry["sha256"]:
                    self.transfer.abort()
                    raise ValueError("Downloaded file failed SHA-256 or size check")
                self.index += 1
                self.phase = "download_start"
            self.transfer = None
            return
        if self.phase == "hash":
            if self.index >= len(self.entries):
                self.status.update({"busy": False, "state": "checked",
                    "available": bool(self.changed or self.deletions),
                    "last_check": epoch(),
                    "files": [item["name"] for item in self.changed] + self.deletions})
                self.phase = "idle"
                return
            entry = self.entries[self.index]
            if self.file is None:
                path = _path(self.root, entry["name"])
                if not exists(path):
                    self.changed.append(entry)
                    self.index += 1
                    return
                self.file = open(path, "rb")
                self.digest = hashlib.sha256()
            data = self.file.read(CHUNK)
            if data:
                self.digest.update(data)
            else:
                self._close_files()
                if _hex(self.digest) != entry["sha256"]:
                    self.changed.append(entry)
                self.index += 1
            return
        if self.phase == "download_start":
            if self.index >= len(self.changed):
                self.index = 0
                self.phase = "verify"
                return
            entry = self.changed[self.index]
            self._download(entry["path"], _path(self.stage, "new-" + entry["name"]), entry["size"], now_ms)
            self.phase = "download"
            return
        if self.phase == "verify":
            if self.index >= len(self.changed):
                self.phase = "prepare"
                return
            entry = self.changed[self.index]
            if self.file is None:
                self.file = open(_path(self.stage, "new-" + entry["name"]), "rb")
                self.digest = hashlib.sha256()
                self.hashed_size = 0
            data = self.file.read(CHUNK)
            if data:
                if self.hashed_size == 0 and entry["name"].endswith(".mpy"):
                    _check_mpy_header(data)
                self.digest.update(data)
                self.hashed_size += len(data)
            else:
                self._close_files()
                if self.hashed_size != entry["size"] or _hex(self.digest) != entry["sha256"]:
                    raise ValueError("Staged file failed SHA-256 or size readback")
                self.index += 1
            return
        if self.phase == "prepare":
            self._prepare()
            return
        if self.phase == "backup":
            if not self.idle():
                raise OSError("Valve opened during update preparation")
            if self.index >= len(self.journal["entries"]):
                sync()
                atomic_json(_path(self.root, JOURNAL), self.journal)
                self.index = 0
                self.phase = "commit"
                self.status["state"] = "installing"
                return
            entry = self.journal["entries"][self.index]
            if not entry["old"]:
                self.index += 1
                return
            if self.file is None:
                self.file = open(_path(self.root, entry["name"]), "rb")
                self.target = open(_path(self.stage, "old-" + entry["name"]), "wb")
            data = self.file.read(CHUNK)
            if data:
                self.target.write(data)
            else:
                self.target.flush()
                self._close_files()
                self.index += 1
            return
        if self.phase == "commit":
            if self.close_valves() is False:
                raise OSError("Valve closure failed during installation")
            if not self.idle():
                raise OSError("Valve opened during update installation")
            if self.index < len(self.journal["entries"]):
                entry = self.journal["entries"][self.index]
                destination = _path(self.root, entry["name"])
                _remove(destination)
                if not entry["delete"]:
                    os.rename(_path(self.stage, "new-" + entry["name"]), destination)
                sync()
                self.index += 1
                return
            self.journal["phase"] = "pending"
            self.journal["installed_at"] = epoch()
            atomic_json(_path(self.root, JOURNAL), self.journal)
            self.reboot_required = True
            self.status.update({"busy": False, "state": "reboot_required", "available": False,
                                "last_install": self.journal["installed_at"]})
            self.phase = "idle"


def mark_stable(root="."):
    """Call only after 60 seconds of successful safety-loop iterations."""
    path = _path(root, JOURNAL)
    journal = read_json(path)
    if not journal or journal.get("phase") == "committing":
        return False
    journal["phase"] = "stable"
    atomic_json(path, journal)
    # Keep a stable tombstone. It avoids reactivating a stale .prev journal
    # across a power cut and costs one small file. Next update replaces it.
    return True
