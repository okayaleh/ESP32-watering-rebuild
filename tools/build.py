"""Build reproducible flat MicroPython artifacts; never publish credentials."""
import argparse
import ast
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def scrub_config(source):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    replacements = []
    seen = set()
    root_statements = set(id(statement) for statement in tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("WIFI_SSID", "WIFI_PASSWORD") for t in node.targets) and id(node) not in root_statements:
            raise ValueError("Credential assignments must not be nested in conditions or functions")
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            keys = [target.id for target in statement.targets if isinstance(target, ast.Name)]
            if any(key in ("WIFI_SSID", "WIFI_PASSWORD") for key in keys):
                if len(keys) != 1 or len(statement.targets) != 1:
                    raise ValueError("Credentials must be separate top-level literal assignments")
                key = keys[0]
                seen.add(key)
                replacements.append((statement.lineno - 1, statement.end_lineno, key + ' = ""\n'))
    if seen != {"WIFI_SSID", "WIFI_PASSWORD"}:
        raise ValueError("Both credential assignments are required")
    for start, end, replacement in reversed(replacements):
        lines[start:end] = [replacement]
    clean = "".join(lines)
    # Verify exact literals without executing the user's configuration.
    for statement in ast.parse(clean).body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in seen:
                    if ast.literal_eval(statement.value) != "":
                        raise ValueError("Credential scrubbing failed")
    # A comment or second assignment containing a credential is also a leak.
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id in seen for t in statement.targets):
            original = ast.literal_eval(statement.value)
            if original and original not in ("YOUR_WIFI_SSID", "YOUR_WIFI_PASSWORD") and str(original) in clean:
                raise ValueError("A credential remains elsewhere in config; remove it before building")
    return clean

def compiler_path(explicit=None):
    if explicit:
        return explicit
    local = ROOT / ".tools/python/mpy_cross/mpy-cross.exe"
    if local.exists():
        return str(local)
    result = shutil.which("mpy-cross")
    if result:
        return result
    try:
        import mpy_cross
        path = Path(mpy_cross.__file__).parent
        for name in ("mpy-cross", "mpy-cross.exe"):
            if (path / name).exists():
                return str(path / name)
    except ImportError:
        pass
    raise SystemExit("Install: python -m pip install mpy-cross==1.28.0.post2")

def build(version, compiler=None):
    source, output = ROOT / "src", ROOT / "build"
    output.mkdir(exist_ok=True)
    config_path = source / "config.py"
    config = (config_path if config_path.exists() else source / "config.example.py").read_text(encoding="utf-8")
    clean = scrub_config(config)
    (source / "config.example.py").write_text(clean, encoding="utf-8", newline="\n")
    (output / "config.py").write_text(config, encoding="utf-8", newline="\n")
    cross = compiler_path(compiler)
    print(subprocess.check_output([cross, "--version"], text=True).strip())
    generated = []
    for path in sorted(source.glob("*.py")):
        if path.name in ("config.py", "config.example.py"):
            continue
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
        if path.name in ("main.py", "boot.py", "romboot.py"):
            target = output / path.name
            shutil.copyfile(path, target)
        else:
            target = output / (path.stem + ".mpy")
            subprocess.run([cross, "-O3", "-s", path.name, "-o", str(target), str(path)], check=True)
            header = target.read_bytes()[:4]
            if header[0] != ord("M") or header[1] != 6:
                raise ValueError("Unexpected MicroPython bytecode format")
        generated.append(target)
    dashboard = (source / "index.html").read_bytes()
    (output / "index.html").write_bytes(dashboard)
    (output / "index.html.gz").write_bytes(gzip.compress(dashboard, compresslevel=9, mtime=0))
    generated.extend([output / "index.html", output / "index.html.gz"])
    (output / "version.json").write_text(json.dumps({"version": version}) + "\n", encoding="utf-8", newline="\n")
    generated.append(output / "version.json")
    # boot.py is immutable recovery code, installed only with USB commissioning.
    files = [{"name": p.name, "path": "build/" + p.name,
              "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in generated if p.name not in ("boot.py", "romboot.py")]
    manifest = {"version": version, "mpy": "6.3", "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("Built %s OTA files; dashboard %s -> %s bytes gzip" % (len(files), len(dashboard), (output / "index.html.gz").stat().st_size))
    print("Flash boot.py and romboot.py separately; config.py is local-only. All three are excluded from OTA.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="2.0.0-rebuild.3")
    parser.add_argument("--mpy-cross")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", args.version):
        parser.error("Invalid version")
    build(args.version, args.mpy_cross)
