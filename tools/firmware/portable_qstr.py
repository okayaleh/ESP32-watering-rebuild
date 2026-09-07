"""Portable replacements for qstr preprocessing shell pipelines.

Response files avoid both cmd.exe's 8191-character limit and CreateProcess's
32767-character limit. This only changes build orchestration, not the firmware.
"""
import json
from pathlib import Path
import re
import subprocess
import sys

mode, compiler, flags_path, output, *inputs = sys.argv[1:]
flags = Path(flags_path).read_text().splitlines()
rsp = Path(output + ".flags.rsp")
rsp.parent.mkdir(parents=True, exist_ok=True)
rsp.write_text("\n".join(json.dumps(flag) for flag in flags if flag) + "\n")
if mode == "pp":
    # Reuse upstream's parallel source preprocessing and grouping.
    import makeqstrdefs
    class Args:
        pass
    args = Args()
    args.pp = [compiler, "-E"]
    args.output = [output]
    args.cflags = ["@" + str(rsp), "-DNO_QSTR"]
    args.cxxflags = args.cflags
    args.sources = Path(inputs[0]).read_text().splitlines()
    args.changed_sources = []
    args.dependencies = []
    makeqstrdefs.args = args
    makeqstrdefs.preprocess()
elif mode == "qdefs":
    source = "\n".join(Path(path).read_text() for path in inputs)
    source = re.sub(r"^(Q\(.*\))", r'"\1"', source, flags=re.MULTILINE)
    result = subprocess.check_output([compiler, "-E", "@" + str(rsp), "-"], input=source.encode())
    result = re.sub(rb'^"(Q\(.*\))"', rb"\1", result, flags=re.MULTILINE)
    Path(output).write_bytes(result)
else:
    raise ValueError("Unknown preprocessing operation")
