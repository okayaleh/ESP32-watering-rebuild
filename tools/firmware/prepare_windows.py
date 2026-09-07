"""Patch only build orchestration for cmd.exe and long Windows paths."""
from pathlib import Path
import os
import shutil

ROOT = Path(os.environ.get("PLANTER_FIRMWARE_BUILD_ROOT", Path(__file__).resolve().parents[2] / ".tools/firmware-build"))
MP = ROOT / "micropython"
path = MP / "py/mkrules.cmake"
text = path.read_text()
before = "    COMMAND ${Python3_EXECUTABLE} ${MICROPY_PY_DIR}/makeqstrdefs.py pp ${CMAKE_C_COMPILER} -E output ${MICROPY_GENHDR_DIR}/qstr.i.last cflags ${MICROPY_CPP_FLAGS} -DNO_QSTR cxxflags ${MICROPY_CPP_FLAGS} -DNO_QSTR sources ${MICROPY_SOURCE_QSTR}"
after = "    COMMAND ${Python3_EXECUTABLE} ${MICROPY_PY_DIR}/portable_qstr.py pp ${CMAKE_C_COMPILER} ${MICROPY_GENHDR_DIR}/cppflags.txt ${MICROPY_QSTRDEFS_LAST} ${MICROPY_GENHDR_DIR}/qstrsources.txt"
if before in text:
    text = text.replace(before, after)
    marker = "# Generate qstrs\n"
    text = text.replace(marker, marker + '''
# Planter native Windows build: move long argument lists out of cmd.exe.
file(GENERATE OUTPUT "${MICROPY_GENHDR_DIR}/cppflags.txt" CONTENT "$<JOIN:${MICROPY_CPP_FLAGS},\\n>\\n")
file(GENERATE OUTPUT "${MICROPY_GENHDR_DIR}/qstrsources.txt" CONTENT "$<JOIN:${MICROPY_SOURCE_QSTR},\\n>\\n")
''')
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("COMMAND cat ${MICROPY_QSTRDEFS_PY}"):
            lines[index] = "    COMMAND ${Python3_EXECUTABLE} ${MICROPY_PY_DIR}/portable_qstr.py qdefs ${CMAKE_C_COMPILER} ${MICROPY_GENHDR_DIR}/cppflags.txt ${MICROPY_QSTRDEFS_PREPROCESSED} ${MICROPY_QSTRDEFS_PY} ${MICROPY_QSTRDEFS_PORT} ${MICROPY_QSTRDEFS_COLLECTED}"
        if line.strip().startswith("COMMAND touch "):
            lines[index] = line.replace("COMMAND touch ", "COMMAND ${CMAKE_COMMAND} -E touch ")
    path.write_text("\n".join(lines) + "\n", newline="\n")
elif after not in text:
    raise ValueError("Unknown upstream qstr command")
shutil.copyfile(Path(__file__).resolve().parent / "portable_qstr.py", MP / "py/portable_qstr.py")
print("Prepared portable qstr preprocessing")
