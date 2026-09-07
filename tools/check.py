"""Host validation: compile device Python, JS syntax, then fault tests."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
for directory in ("src", "tools", "tests"):
    for path in (root / directory).glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
html = (root / "src/index.html").read_text(encoding="utf-8")
scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I)
node = shutil.which("node") or str(root / ".tools/node.exe")
if not Path(node).exists():
    raise SystemExit("Node.js is required for dashboard syntax validation")
with tempfile.TemporaryDirectory() as temp:
    script = Path(temp) / "dashboard.js"
    script.write_text("\n".join(scripts), encoding="utf-8")
    subprocess.run([node, "--check", str(script)], check=True)
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
print("Python compilation, dashboard syntax and host tests passed.")
