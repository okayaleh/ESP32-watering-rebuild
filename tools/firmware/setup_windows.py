"""One-time isolated Windows setup. Requires only an existing Python 3.10+."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

SOURCE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("PLANTER_FIRMWARE_BUILD_ROOT", Path(__file__).resolve().parents[2] / ".tools/firmware-build"))
ROOT.mkdir(parents=True, exist_ok=True)
subprocess.check_call([sys.executable, str(SOURCE / "bootstrap.py")])
git = ROOT / "git/cmd/git.exe"
for name, tag, revision, url in (
    ("micropython", "v1.28.0", "e0e9fbb17ed6fd06bb76e266ae554784c9c80804", "https://github.com/micropython/micropython.git"),
    ("esp-idf", "v5.5.1", "fcae32885b0296b32044cb99ecbdc50d98dddb83", "https://github.com/espressif/esp-idf.git"),
):
    checkout = ROOT / name
    if not checkout.exists():
        subprocess.check_call([str(git), "-c", "core.longpaths=true", "clone", "--depth", "1", "--branch", tag, url, str(checkout)])
    actual = subprocess.check_output([str(git), "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if actual != revision:
        raise ValueError("Unexpected source revision in " + str(checkout))
    submodules = ["lib/berkeley-db-1.xx", "lib/micropython-lib"] if name == "micropython" else []
    subprocess.check_call([str(git), "-c", "core.longpaths=true", "-C", str(checkout), "submodule", "update",
                           "--init", "--recursive", "--depth", "1", "--jobs", "8"] + submodules)
python = ROOT / "python/python.exe"
subprocess.check_call([str(python), str(ROOT / "get-pip.py"), "--no-warn-script-location"])
subprocess.check_call([str(python), "-m", "pip", "install", "--no-warn-script-location", "setuptools", "wheel"])
constraints = ROOT / "idf-tools/espidf.constraints.v5.5.txt"
constraints.parent.mkdir(exist_ok=True)
if (SOURCE / "espidf.constraints.v5.5.txt").exists():
    shutil.copyfile(SOURCE / "espidf.constraints.v5.5.txt", constraints)
else:
    urllib.request.urlretrieve("https://dl.espressif.com/dl/esp-idf/espidf.constraints.v5.5.txt", constraints)
requirements = SOURCE / "requirements-lock.txt"
if not requirements.exists():
    requirements = ROOT / "esp-idf/tools/requirements/requirements.core.txt"
subprocess.check_call([str(python), "-m", "pip", "install", "--no-build-isolation", "--no-warn-script-location",
                       "--constraint", str(constraints), "--requirement", str(requirements), "mpy-cross==1.28.0.post2"])
scripts = ROOT / "python/Scripts"
scripts.mkdir(exist_ok=True)
shutil.copyfile(python, scripts / "python.exe")
for path in (ROOT / "python").glob("*.dll"):
    shutil.copyfile(path, scripts / path.name)
(scripts / "python312._pth").write_text("../python312.zip\n..\n../Lib/site-packages\nimport site\n")
shutil.copyfile(SOURCE / "sitecustomize.py", ROOT / "python/sitecustomize.py")
subprocess.check_call([str(python), str(SOURCE / "prepare_firmware.py")])
subprocess.check_call([str(python), str(SOURCE / "prepare_windows.py")])
print("Setup complete. Run tools/firmware/build_firmware.py with the isolated Python interpreter.")
