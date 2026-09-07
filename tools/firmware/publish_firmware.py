"""Copy a completed build and its provenance into the small release folders."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

WORKSPACE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("PLANTER_FIRMWARE_BUILD_ROOT", WORKSPACE / ".tools/firmware-build"))
OUT = WORKSPACE / "firmware"
SOURCE = Path(__file__).resolve().parent
OUT.mkdir(exist_ok=True)
git = str(ROOT / "git/cmd/git.exe")


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def command(*args):
    return subprocess.check_output(list(map(str, args)), text=True, encoding="utf-8")


artifacts = []
for source, name, offset in (
    ("build/firmware.bin", "planter-esp32-1.28.0-romfs.bin", "0x1000"),
    ("build/micropython.bin", "planter-esp32-1.28.0-romfs.app-bin", "0x10000"),
):
    copy(ROOT / source, OUT / name)
    data = (OUT / name).read_bytes()
    artifacts.append({"file": name, "bytes": len(data), "flash_offset": offset,
                      "sha256": hashlib.sha256(data).hexdigest()})
copy(ROOT / "board/partitions.csv", OUT / "partitions.csv")
copy(ROOT / "tool-sources.json", SOURCE / "tool-sources.json")
copy(ROOT / "micropython/ports/esp32/lockfiles/dependencies.lock.esp32", SOURCE / "components.lock")
for name in ("mpconfigboard.h", "mpconfigboard.cmake"):
    copy(ROOT / "board" / name, SOURCE / "board" / name)
# Keep complete build settings without publishing the builder's local paths.
# prepare_firmware.py regenerates absolute partition paths for each workspace.
sdkconfig = []
for line in (ROOT / "build/sdkconfig").read_text(encoding="utf-8").splitlines():
    if line.startswith(("CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=", "CONFIG_PARTITION_TABLE_FILENAME=")):
        line = line.split("=", 1)[0] + '="board/partitions.csv"'
    sdkconfig.append(line)
(SOURCE / "sdkconfig.reference").write_text("\n".join(sdkconfig) + "\n", encoding="utf-8", newline="\n")
(SOURCE / "micropython.patch").write_text(command(git, "-C", ROOT / "micropython", "diff", "--",
    "ports/esp32/gccollect.c", "ports/esp32/esp32_partition.c", "py/mkrules.cmake"), encoding="utf-8", newline="\n")

sources = {}
for name in ("micropython", "esp-idf"):
    sources[name] = {"revision": command(git, "-C", ROOT / name, "rev-parse", "HEAD").strip(),
                     "url": "https://github.com/" + ("micropython/micropython" if name == "micropython" else "espressif/esp-idf")}
    # Includes recursive, exact submodule revisions; leading '-' is not allowed.
    paths = ["lib/berkeley-db-1.xx", "lib/micropython-lib"] if name == "micropython" else []
    status = command(git, "-C", ROOT / name, "submodule", "status", "--recursive", *paths)
    if any(line.startswith(("-", "+", "U")) for line in status.splitlines()):
        raise ValueError("Uninitialised or changed submodule in " + name)
    (SOURCE / (name + "-submodules.txt")).write_text(status, encoding="utf-8", newline="\n")

licenses = OUT / "licenses"
for name in ("micropython", "esp-idf"):
    copy(ROOT / name / "LICENSE", licenses / name / "LICENSE")
# Retain SDK third-party notices, including components compiled into its archive
# libraries. Some optional components are linker-discarded in this board build.
for root, prefix in (
    (ROOT / "esp-idf/components", "esp-idf/components"),
    (ROOT / "micropython/ports/esp32/managed_components", "managed_components"),
):
    for path in root.rglob("*"):
        if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
            copy(path, licenses / prefix / path.relative_to(root))
copy(ROOT / "micropython/lib/micropython-lib/LICENSE", licenses / "micropython-lib/LICENSE")
for name in ("COPYING3", "COPYING.RUNTIME"):
    copy(ROOT / "toolchain/xtensa-esp-elf/share/licenses/gcc" / name, licenses / "gcc-runtime" / name)
db = ROOT / "micropython/lib/berkeley-db-1.xx"
copy(db / "README.Impt.License.Change", licenses / "berkeley-db/README.Impt.License.Change")
for folder, name in ((db, "berkeley-db/source-notices.txt"),
    (ROOT / "micropython/lib/oofatfs", "oofatfs/source-notices.txt"),
    (ROOT / "micropython/lib/littlefs", "littlefs/source-notices.txt")):
    notices = []
    for path in sorted(folder.rglob("*")):
        if path.suffix not in (".c", ".h"):
            continue
        text = path.read_text(errors="replace")
        end = text.find("#include")
        header = text[:end] if 0 <= end <= 15000 else text[:text.find("*/") + 2]
        if "copyright" in header.lower():
            notices.append("--- " + path.relative_to(folder).as_posix() + " ---\n" + header)
    target = licenses / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n".join(notices), encoding="utf-8", newline="\n")
littlefs_license = licenses / "littlefs/LICENSE.md"
if not littlefs_license.exists():
    urllib.request.urlretrieve("https://raw.githubusercontent.com/littlefs-project/littlefs/v2.11.2/LICENSE.md", littlefs_license)

manifest = {"target": "ESP32 classic, 4 MB flash, no PSRAM required", "micropython": "1.28.0",
            "esp_idf": "5.5.1", "sources": sources, "artifacts": artifacts,
            "patches": ["tools/firmware/micropython.patch", "tools/firmware/prepare_firmware.py",
                        "tools/firmware/prepare_windows.py", "tools/firmware/portable_qstr.py"],
            "features": {"romfs_banks": 2, "romfs_bank_bytes": 262144,
                         "filesystem_bytes": 1572864, "gc_split_native_margin_bytes": 32832,
                         "bluetooth": False, "ethernet_lan": False}}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
(OUT / "SHA256SUMS").write_text("".join(item["sha256"] + "  " + item["file"] + "\n" for item in artifacts), encoding="ascii", newline="\n")
print(json.dumps(artifacts, indent=2))
