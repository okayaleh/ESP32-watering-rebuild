"""Fetch pinned portable Windows firmware build tools; never edits global PATH."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import urllib.request
import zipfile
import time

ROOT = Path(os.environ.get("PLANTER_FIRMWARE_BUILD_ROOT", Path(__file__).resolve().parents[2] / ".tools/firmware-build"))
DOWNLOADS = ROOT / "downloads"
DOWNLOADS.mkdir(parents=True, exist_ok=True)

def fetch(name, url, destination, expected=None):
    archive = DOWNLOADS / (name + ".zip")
    if not archive.exists():
        print("Downloading " + name, flush=True)
        partial = Path(str(archive) + ".part")
        for attempt in range(8):
            offset = partial.stat().st_size if partial.exists() else 0
            request = urllib.request.Request(url, headers={"Range": "bytes=%d-" % offset} if offset else {})
            try:
                with urllib.request.urlopen(request, timeout=60) as source:
                    append = offset and source.status == 206 and source.headers.get("Content-Range", "").startswith("bytes %d-" % offset)
                    with open(partial, "ab" if append else "wb") as target:
                        while data := source.read(1024 * 1024):
                            target.write(data)
                partial.replace(archive)
                break
            except (OSError, TimeoutError) as exc:
                print("Retry %s (%s), saved %d bytes" % (name, type(exc).__name__, partial.stat().st_size if partial.exists() else 0), flush=True)
                if attempt == 7:
                    raise
                time.sleep(1)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected and digest != expected:
        raise ValueError("SHA256 mismatch: " + name)
    destination = ROOT / destination
    if not (destination / ".extracted").exists():
        print("Extracting " + name, flush=True)
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                final = (destination / member.filename).resolve()
                if not final.is_relative_to(destination.resolve()):
                    raise ValueError("Unsafe archive member")
            source.extractall(destination)
        (destination / ".extracted").write_text(digest)
    print("Ready " + name + " SHA256 " + digest, flush=True)
    return {"name": name, "url": url, "sha256": digest}

items = [
    ("python-3.12.10", "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip", "python", "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"),
    ("mingit-2.55.0.5", "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/MinGit-2.55.0.5-64-bit.zip", "git", "56d7b226b7693196cfc71fef26568f536c4a021ab6c37ff2db4287bed908e96e"),
    ("cmake-3.30.2", "https://github.com/Kitware/CMake/releases/download/v3.30.2/cmake-3.30.2-windows-x86_64.zip", "cmake", "48bf4b3dc2d668c578e0884cac7878e146b036ca6b5ce4f8b5572f861b004c25"),
    ("ninja-1.12.1", "https://github.com/ninja-build/ninja/releases/download/v1.12.1/ninja-win.zip", "ninja", "f550fec705b6d6ff58f2db3c374c2277a37691678d6aba463adcbb129108467a"),
    ("xtensa-esp-elf-14.2.0", "https://github.com/espressif/crosstool-NG/releases/download/esp-14.2.0_20241119/xtensa-esp-elf-14.2.0_20241119-x86_64-w64-mingw32.zip", "toolchain", "62ae704777d73c30689efff6e81178632a1ca44d1a2d60f4621eb997e040e028"),
]
with ThreadPoolExecutor(max_workers=5) as executor:
    results = list(executor.map(lambda args: fetch(*args), items))
(ROOT / "tool-sources.json").write_text(json.dumps(results, indent=2))
pth = ROOT / "python/python312._pth"
pth.write_text("python312.zip\n.\nLib/site-packages\nimport site\n")
get_pip = ROOT / "get-pip.py"
if not get_pip.exists():
    urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip)
print("Portable dependencies downloaded", flush=True)
