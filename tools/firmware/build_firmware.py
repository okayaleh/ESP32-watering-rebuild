"""Build Planter MicroPython with process-local paths; no system installation."""
import json
import os
from pathlib import Path
import subprocess
import sys

WORKSPACE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("PLANTER_FIRMWARE_BUILD_ROOT", WORKSPACE / ".tools/firmware-build"))
MP = ROOT / "micropython"
PORT = MP / "ports/esp32"
env = os.environ.copy()
paths = [ROOT / "python", ROOT / "python/Scripts", ROOT / "git/cmd",
         ROOT / "cmake/cmake-3.30.2-windows-x86_64/bin", ROOT / "ninja",
         ROOT / "toolchain/xtensa-esp-elf/bin", ROOT / "esp-idf/tools"]
env["PATH"] = os.pathsep.join(map(str, paths)) + os.pathsep + env.get("PATH", "")
env["IDF_PATH"] = str(ROOT / "esp-idf")
env["IDF_TOOLS_PATH"] = str(ROOT / "idf-tools")
env["IDF_PYTHON_ENV_PATH"] = str(ROOT / "python")
env["IDF_VERSION"] = "5.5.1"
env["IDF_TARGET"] = "esp32"
cross = ROOT / "python/Lib/site-packages/mpy_cross/mpy-cross.exe"
if not cross.exists():
    cross = WORKSPACE / ".tools/python/mpy_cross/mpy-cross.exe"
env["MICROPY_MPYCROSS"] = str(cross)
env["IDF_COMPONENT_CACHE_PATH"] = str(ROOT / "component-cache")
env["IDF_COMPONENT_REGISTRY_URL"] = "https://components.espressif.com"
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
build = ROOT / "build"
build.mkdir(exist_ok=True)
command = [str(ROOT / "python/python.exe"), str(ROOT / "esp-idf/tools/idf.py"),
           "-B", str(build), "-D", "MICROPY_BOARD=PLANTER_ESP32",
           "-D", "MICROPY_BOARD_DIR=" + (ROOT / "board").as_posix(),
           "-D", "Python3_EXECUTABLE=" + (ROOT / "python/python.exe").as_posix(),
           "-D", "PYTHON=" + (ROOT / "python/python.exe").as_posix(),
           "-D", "CMAKE_NINJA_FORCE_RESPONSE_FILE=ON"] + (sys.argv[1:] or ["build"])
print("Running isolated ESP32 firmware build", flush=True)
with open(ROOT / "build-latest.log", "w", encoding="utf-8") as log:
    process = subprocess.Popen(command, cwd=PORT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for line in process.stdout:
        log.write(line)
        log.flush()
        print(line, end="", flush=True)
    status = process.wait()
if status == 0 and (build / "micropython.bin").exists():
    subprocess.check_call([str(ROOT / "python/python.exe"), "makeimg.py", str(build / "sdkconfig"),
        str(build / "bootloader/bootloader.bin"), str(build / "partition_table/partition-table.bin"),
        str(build / "micropython.bin"), str(build / "firmware.bin"), str(build / "micropython.uf2")],
        cwd=PORT, env=env)
sys.exit(status)
