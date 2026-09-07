"""Bounded GitHub HTTPS / optional LAN HTTP staging and boot recovery."""
import gc
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
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("Configure GitHub updates or a trusted HTTP mirror")
    secure = url.startswith("https://")
    tail = url[8:] if secure else url[7:]
    authority, _, path = tail.partition("/")
    host, sep, port = authority.partition(":")
    if not host or "@" in authority or len(host) > 253 or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in host):
        raise ValueError("Invalid mirror host")
    if any(ord(ch) < 33 or ord(ch) > 126 or ch in "#\\" for ch in url):
        raise ValueError("Invalid mirror URL")
    port = int(port) if sep and port.isdigit() else (443 if secure else 80) if not sep else 0
    if not 1 <= port <= 65535:
        raise ValueError("Invalid mirror port")
    if secure and (host != "raw.githubusercontent.com" or port != 443):
        raise ValueError("HTTPS updates must use raw.githubusercontent.com:443")
    return host, port, "/" + path


def _tls_context():
    # MicroPython 1.28's native `tls` accepts positional certificate data.
    # Its frozen `ssl` wrapper instead treats that argument as a filename.
    # CPython's equivalent is cadata. All paths require trusted certificates.
    if sys.implementation.name == "micropython":
        import tls as ssl
    else:
        import ssl
    from github_trust import CA_PEM
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    if sys.implementation.name == "micropython":
        context.load_verify_locations(CA_PEM)
    else:
        context.check_hostname = True
        context.load_verify_locations(cadata=CA_PEM.decode("ascii"))
    return context


class HTTPDownload:
    """One socket operation per poll, 512-byte writes, absolute deadline.

    DNS and TLS handshake use nonblocking sockets. Content-Length is mandatory;
    redirects, compression and transfer-encoding are rejected. HTTPS trusts only
    the bundled CAs and exact GitHub raw hostname, with no insecure fallback.
    """
    def __init__(self, url, destination, limit, now_ms, resolver=None,
                 timeout_ms=120000, socket_module=None, poll_factory=None,
                 tls_factory=None):
        self.host, self.port, self.path = _url(url)
        self.secure = url.startswith("https://")
        self.tls_factory = tls_factory or _tls_context
        self.context = None
        self.raw_sock = None
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
        self.request = ("GET %s HTTP/1.0\r\nHost: %s:%s\r\nConnection: close\r\nAccept-Encoding: identity\r\nUser-Agent: Planter-OTA/1\r\n\r\n" %
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
        self.header_limit = 4096 if self.secure else 2048

    def close(self):
        # mbedTLS invalidates its reference after handshake failure, so retain
        # and close the underlying socket independently on every exit path.
        for sock in (self.sock, self.raw_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        self.sock = self.raw_sock = self.poller = self.context = None
        if self.file:
            self.file.close()
            self.file = None
        if self.secure:
            gc.collect()

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
                self.raw_sock = self.sock
                self.sock.setblocking(False)
                self.poller = self.poll_factory()
                self.poller.register(self.sock, POLLOUT | POLLERR | POLLHUP)
                try:
                    self.sock.connect((ip, self.port))
                except OSError as exc:
                    if exc.args[0] not in (11, 35, 114, 115, 119, 120, 10035, 10036, 10037):
                        raise
                self.phase = "connect" if self.secure else "send"
                return
            if not self.poller.poll(0):
                return
            if self.phase == "connect":
                gc.collect()
                self.context = self.tls_factory()
                self.sock = self.context.wrap_socket(self.raw_sock,
                    server_hostname=self.host, do_handshake_on_connect=False)
                # Poll the wrapper: MicroPython maps TLS WANT_READ/WANT_WRITE
                # internally, including decrypted bytes already in its buffer.
                self.poller = self.poll_factory()
                self.poller.register(self.sock, POLLOUT | POLLERR | POLLHUP)
                self.phase = "send"
                return
            if self.phase == "send":
                data = self.request[self.offset:self.offset + CHUNK]
                count = self.sock.write(data) if self.secure else self.sock.send(data)
                if count is None:
                    return
                if not count:
                    raise OSError("Mirror closed during request")
                self.offset += count
                self.poller.modify(self.sock, POLLOUT | POLLERR | POLLHUP)
                if self.offset == len(self.request):
                    self.poller.modify(self.sock, POLLIN | POLLERR | POLLHUP)
                    self.phase = "header"
                return
            data = self.sock.read(CHUNK) if self.secure else self.sock.recv(CHUNK)
            if data is None:
                return
            if not data:
                raise OSError("Truncated update transfer")
            self.poller.modify(self.sock, POLLIN | POLLERR | POLLHUP)
            if self.phase == "header":
                self.header.extend(data)
                position = self.header.find(b"\r\n\r\n")
                if position < 0:
                    if len(self.header) > self.header_limit:
                        raise ValueError("Mirror response headers too large")
                    return
                if position > self.header_limit:
                    raise ValueError("Mirror response headers too large")
                lines = bytes(self.header[:position]).split(b"\r\n")
                status = lines[0].split()
                if len(status) < 2 or status[0] not in (b"HTTP/1.0", b"HTTP/1.1"):
                    raise ValueError("Invalid update HTTP response")
                if status[1] != b"200":
                    raise ValueError("Update server returned HTTP " + status[1].decode()[:8] + "; expected 200 without redirects")
                lengths = []
                for line in lines[1:]:
                    key, sep, value = line.partition(b":")
                    if not sep:
                        raise ValueError("Malformed mirror header")
                    if key.lower() == b"transfer-encoding":
                        raise ValueError("Mirror must send Content-Length, not chunked data")
                    if key.lower() == b"content-encoding" and value.strip().lower() != b"identity":
                        raise ValueError("Compressed HTTP responses are not allowed")
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
            # CPython exposes TLS retry directions as exceptions. MicroPython
            # uses None/EAGAIN and its SSL poll implementation handles direction.
            kind = type(exc).__name__
            if self.secure and kind in ("SSLWantReadError", "SSLWantWriteError"):
                self.poller.modify(self.sock, (POLLIN if kind == "SSLWantReadError" else POLLOUT) | POLLERR | POLLHUP)
                return
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


def _repository(value):
    if (not isinstance(value, str) or len(value) > 140 or len(value.split("/")) != 2 or
            any(not part or part.startswith(".") or part.endswith(".") for part in value.split("/")) or
            any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-" for ch in value) or
            ".." in value):
        raise ValueError("Invalid GitHub repository; use owner/repository")
    return value


def _digest(value, size):
    return isinstance(value, str) and len(value) == size and all(c in "0123456789abcdef" for c in value)


class Updater:
    def __init__(self, root=".", base_url="", manifest_path="build/manifest.json",
                 close_valves=None, idle=None, resolver=None, timeout_ms=120000,
                 github_repo="okayaleh/ESP32-watering-rebuild"):
        self.root = root
        self.stage = _path(root, ".ota")
        if not exists(self.stage):
            os.mkdir(self.stage)
        self.base_url = base_url.rstrip("/")
        self.source = "mirror" if self.base_url else "github"
        self.github_repo = _repository(github_repo) if self.source == "github" else None
        self.download_base = self.base_url
        self.release = None
        self.selected_version = None
        self.automatic_check = False
        self.auto_eligible = False
        self.manifest_path = manifest_path.lstrip("/")
        self.close_valves = close_valves
        self.idle = idle
        self.resolver = resolver
        self.timeout_ms = timeout_ms
        version_info = read_json(_path(root, "version.json"), {})
        installed_version = version_info.get("version", "unknown") if isinstance(version_info, dict) else str(version_info)
        previous = read_json(_path(root, JOURNAL), {})
        self.policy = read_json(_path(root, ".ota-policy.json"), {})
        if not isinstance(self.policy, dict):
            raise ValueError("Invalid update recovery policy")
        rejected = self.policy.get("rejected", [])
        if not isinstance(rejected, list) or any(not isinstance(v, str) for v in rejected):
            raise ValueError("Invalid rejected update list")
        if isinstance(previous, dict) and previous.get("phase") == "rolled_back":
            failed = previous.get("version")
            if isinstance(failed, str) and failed not in rejected:
                self.policy["rejected"] = (rejected + [failed])[-8:]
                atomic_json(_path(root, ".ota-policy.json"), self.policy)
        self.status = {"busy": False, "state": "idle", "available": False,
                       "version": None, "error": None, "files": [],
                       "installed_version": installed_version,
                       "available_version": None, "last_check": None,
                       "source": self.source, "repository": self.github_repo,
                       "available_versions": [], "release_commit": None,
                       "blocked_version": None,
                       "automatic_paused": bool(self.policy.get("held_version")),
                       "held_version": self.policy.get("held_version"),
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

    def request_check(self, automatic=False, version=None):
        self._require_idle()
        if version is not None and (not isinstance(version, str) or not 0 < len(version) <= 64):
            raise ValueError("Invalid release version")
        if self.source == "mirror":
            _url(self.base_url)
            if version is not None:
                raise ValueError("Choose the release on your HTTP mirror")
        self.release = None
        self.selected_version = version
        self.automatic_check = automatic
        self.auto_eligible = False
        self.uploads = {}
        self.changed = []
        self.deletions = []
        self.entries = []
        self.status.update({"busy": True, "state": "checking", "error": None,
                            "available": False, "files": [], "available_version": None,
                            "release_commit": None, "blocked_version": None})
        self.cleanup_names = list(os.listdir(self.stage))
        self.after_cleanup = "channel_start" if self.source == "github" else "manifest_start"
        self.phase = "cleanup"

    def begin_upload(self, filename):
        self._require_idle()
        if len(self.uploads) >= MAX_FILES and filename not in self.uploads:
            raise ValueError("Too many uploaded files")
        validate_filename(filename)
        self.auto_eligible = False
        self.release = None
        if not self.uploads:
            self.status["available"] = False
            self.changed = []
            self.deletions = []
        self.status["busy"] = True
        self.status["state"] = "uploading"
        return UploadSink(self, filename)

    def request_install(self):
        self._require_idle()
        if not self.status["available"]:
            raise ValueError("No checked update or uploaded files")
        if not self.close_valves or not self.idle:
            raise ValueError("Valve safety callbacks are required for installation")
        self.auto_eligible = False
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
        self.transfer = HTTPDownload(self.download_base + "/" + repo_path, dest,
            limit, now_ms, self.resolver, self.timeout_ms)

    def _parse_channel(self):
        with open(_path(self.stage, "channel"), "r") as stream:
            channel = json.load(stream)
        if (not isinstance(channel, dict) or type(channel.get("schema")) is not int or
                channel["schema"] != 1 or channel.get("repository") != self.github_repo):
            raise ValueError("Invalid GitHub update channel")
        releases = channel.get("releases")
        if not isinstance(releases, list) or not 0 < len(releases) <= 3:
            raise ValueError("GitHub channel requires one to three releases")
        versions = []
        for release in releases:
            if not isinstance(release, dict):
                raise ValueError("Invalid channel release")
            version = release.get("version")
            if (not isinstance(version, str) or not 0 < len(version) <= 64 or
                    any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in version) or
                    version in versions):
                raise ValueError("Invalid or duplicate channel version")
            if not _digest(release.get("commit"), 40) or not _digest(release.get("manifest_sha256"), 64):
                raise ValueError("Channel must pin an immutable commit and manifest SHA-256")
            size = release.get("manifest_size")
            if type(size) is not int or not 0 < size <= MAX_MANIFEST:
                raise ValueError("Invalid channel manifest size")
            versions.append(version)
        self.status["available_versions"] = versions
        selected = self.selected_version or versions[0]
        if selected not in versions:
            raise ValueError("Selected version is no longer in the retained GitHub channel")
        if selected in self.policy.get("rejected", []):
            self.status["blocked_version"] = selected
            raise ValueError("This release failed its boot trial; wait for a newer release")
        self.release = dict(releases[versions.index(selected)])
        self.release["rollback"] = selected != versions[0]
        self.download_base = "https://raw.githubusercontent.com/" + self.github_repo + "/" + self.release["commit"]
        self.status["release_commit"] = self.release["commit"]

    def _parse_manifest(self):
        with open(_path(self.stage, "manifest"), "r") as stream:
            manifest = json.load(stream)
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be an object")
        if self.release:
            if (manifest.get("platform") != "planter-esp32-romfs-mpy6" or
                    manifest.get("native_sha256") != "9369e9e2eba45a9828d3d8c929c2b39ddeb41987318d8aa092aa130908780073"):
                raise ValueError("This release requires a different native firmware; install it by USB")
            if manifest.get("version") != self.release["version"]:
                raise ValueError("Manifest version does not match the GitHub channel")
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
            if (path.startswith("/") or ".." in path.split("/") or
                    any(ch in path for ch in ("\\", "?", "#", ":", "\r", "\n", " ", "\t"))):
                raise ValueError("Unsafe manifest repository path")
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
        self.deletions = [name for name in set(deletes) if exists(_path(self.root, name))]
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
        if self.release:
            held = self.release["version"] if self.release["rollback"] else None
            if self.policy.get("held_version") != held:
                self.policy["held_version"] = held
                atomic_json(_path(self.root, ".ota-policy.json"), self.policy)
        self.index = 0
        self.phase = "backup"
        self.status["state"] = "preparing"

    def _close_files(self):
        for name in ("file", "target"):
            stream = getattr(self, name)
            if stream:
                stream.close()
                setattr(self, name, None)

    def _release_transfer(self):
        if self.transfer is None:
            return
        self.transfer.close()
        self.transfer = None
        # TLS buffers are much larger than the application's regular slices.
        # Release them before JSON parsing or opening the next TLS connection.
        import gc
        gc.collect()

    def poll(self, now_ms):
        try:
            self._poll(now_ms)
        except Exception as exc:
            self._close_files()
            if self.transfer:
                self.transfer.abort()
            self._release_transfer()
            self.auto_eligible = False
            self.status.update({"busy": False, "state": "error", "error": str(exc),
                                "available": False})
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
                if name in ("manifest", "channel") or name.startswith(("new-", "old-")):
                    _remove(_path(self.stage, name))
            else:
                self.phase = self.after_cleanup
            return
        if self.phase == "channel_start":
            self.download_base = "https://raw.githubusercontent.com/" + self.github_repo
            self._download("updates/channel.json", _path(self.stage, "channel"), 4096, now_ms)
            self.phase = "channel"
            return
        if self.phase == "manifest_start":
            path = "build/manifest.json" if self.release else self.manifest_path
            limit = self.release["manifest_size"] if self.release else MAX_MANIFEST
            self._download(path, _path(self.stage, "manifest"), limit, now_ms)
            self.phase = "manifest"
            return
        if self.phase in ("channel", "manifest", "download"):
            self.transfer.poll(now_ms)
            if self.transfer.error:
                raise OSError(self.transfer.error)
            if not self.transfer.done:
                return
            if self.phase == "channel":
                self._release_transfer()
                self._parse_channel()
                self.phase = "manifest_start"
            elif self.phase == "manifest":
                if self.release and (self.transfer.size != self.release["manifest_size"] or
                        _hex(self.transfer.hash) != self.release["manifest_sha256"]):
                    raise ValueError("Manifest failed its channel SHA-256 or size check")
                self._release_transfer()
                self._parse_manifest()
                self.phase = "hash"
            else:
                entry = self.changed[self.index]
                if self.transfer.size != entry["size"] or _hex(self.transfer.hash) != entry["sha256"]:
                    self.transfer.abort()
                    raise ValueError("Downloaded file failed SHA-256 or size check")
                self.index += 1
                self.phase = "download_start"
            self._release_transfer()
            return
        if self.phase == "hash":
            if self.index >= len(self.entries):
                self.status.update({"busy": False, "state": "checked",
                    "available": bool(self.changed or self.deletions),
                    "last_check": epoch(),
                    "files": [item["name"] for item in self.changed] + self.deletions})
                self.auto_eligible = self.automatic_check and self.status["available"]
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


class UpdateSchedule:
    """Daily checks with startup catch-up and bounded retries, while idle only."""
    def __init__(self):
        self.completed_slot = None
        self.pending = None
        self.pending_slot = None
        self.last_attempt = None
        self.failures = 0
        self.apply_pending = False

    def _failed(self, now_ms):
        self.failures += 1
        self.last_attempt = now_ms
        self.completed_slot = None
        self.apply_pending = False

    def poll(self, app, now_ms):
        updater = app.updater
        if not updater:
            return
        status = updater.status
        if self.pending:
            if status["busy"]:
                return
            if status["state"] in ("checked", "reboot_required") and not status.get("error"):
                self.completed_slot = self.pending_slot
                self.failures = 0
                self.apply_pending = (self.pending == "check" and updater.auto_eligible and
                                      getattr(app.config, "UPDATE_AUTO_INSTALL", False))
            else:
                self._failed(now_ms)
            self.pending = None
        hour = getattr(app.config, "UPDATE_CHECK_HOUR", 4)
        if (type(hour) is not int or not 0 <= hour <= 23 or app.uptime_ms < 60000 or
                not app.controller.synced or not app.wifi or not app.wifi.connected or
                not app.controller.idle() or app.controller.paused or
                app.controller.inhibited or app.reboot_at is not None or
                status["busy"] or updater.reboot_required or status.get("automatic_paused") or
                (app.sensors and app.sensors.calibration) or
                (app.server and app.server.health().get("active_uploads", 0))):
            return
        # A user's staged upload or manually checked release needs their Apply
        # action. Never replace it with an automatic check or install it later.
        if updater.uploads or (status["available"] and not updater.auto_eligible):
            return
        local = epoch() + app.settings["tz_offset_min"] * 60
        slot = (local - hour * 3600) // 86400
        if self.apply_pending:
            self.apply_pending = False
            try:
                app.update_action("apply", automatic=True)
                self.pending = "apply"
                self.pending_slot = slot
            except Exception:
                self._failed(now_ms)
                raise
            return
        if self.completed_slot == slot:
            return
        retry_ms = 300000 if self.failures <= 1 else 1800000
        if self.failures and self.last_attempt is not None and ticks_diff(now_ms, self.last_attempt) < retry_ms:
            return
        self.last_attempt = now_ms
        try:
            app.update_action("check", automatic=True)
            self.pending = "check"
            self.pending_slot = slot
        except Exception:
            self._failed(now_ms)
            raise


def mark_stable(root="."):
    """Call only after 60 seconds of successful safety-loop iterations."""
    path = _path(root, JOURNAL)
    journal = read_json(path)
    if journal is None:
        if exists(path) or exists(path + ".prev"):
            raise ValueError("Unreadable update recovery journal")
        # A fresh USB installation has no update transaction to acknowledge.
        # The caller still requires 60 seconds of healthy safety-loop service.
        return True
    if not isinstance(journal, dict):
        raise ValueError("Invalid update recovery journal")
    if journal.get("phase") in ("committing", "rolling_back"):
        return False
    if journal.get("phase") in ("stable", "rolled_back"):
        return True
    if journal.get("phase") != "pending":
        raise ValueError("Unknown update recovery phase")
    journal["phase"] = "stable"
    atomic_json(path, journal)
    # Keep a stable tombstone. It avoids reactivating a stale .prev journal
    # across a power cut and costs one small file. Next update replaces it.
    return True
