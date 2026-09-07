"""Verify and package source + build; replace config with scrubbed defaults."""
import hashlib
import json
from pathlib import Path
import zipfile
from build import scrub_config

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "build/manifest.json").read_text())
for entry in manifest["files"]:
    path = root / entry["path"]
    payload = path.read_bytes()
    if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise SystemExit("Build hash mismatch: " + entry["name"])
platform_manifest = root / "firmware/manifest.json"
if platform_manifest.exists():
    for entry in json.loads(platform_manifest.read_text())["artifacts"]:
        payload = (root / "firmware" / entry["file"]).read_bytes()
        if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise SystemExit("Platform hash mismatch: " + entry["file"])
clean = scrub_config((root / "src/config.example.py").read_text(encoding="utf-8"))
directory = root / "release"
directory.mkdir(exist_ok=True)
archive_path = directory / ("planter-" + manifest["version"] + ".zip")
with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for folder in ("src", "build", "docs", "tools", "tests", ".github", "firmware"):
        for path in sorted((root / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc" or path.name == "config.py":
                continue
            archive.write(path, path.relative_to(root).as_posix())
    for filename in ("README.md", "LICENSE", ".gitignore", ".gitattributes", "build_mpy.ps1", "serve_updates.ps1", "requirements-dev.txt"):
        archive.write(root / filename, filename)
    archive.writestr("build/config.py", clean)
digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
(directory / (archive_path.name + ".sha256")).write_text(digest + "  " + archive_path.name + "\n")
with zipfile.ZipFile(archive_path) as archive:
    if archive.testzip() is not None:
        raise SystemExit("Archive verification failed")
print(str(archive_path))
print("Bytes:", archive_path.stat().st_size, "SHA-256:", digest)
