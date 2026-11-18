"""The sidecar's twin of the engine's Windows path model (`app.platform.windows_paths`; S0131, R2-1): the same answers
as `ledger_engine.fs.pathmodel.WINDOWS.split` on one list of paths, asked of the engine's own model in its own
environment (the sidecar never imports the engine, ADR-0054). Pure: runs on every OS."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.platform.windows_paths import split

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"

PATHS = [
    "C:\\Books\\a\\main.bean",
    "c:\\Books\\main.bean",
    "C:/Books/a/main.bean",
    "C:\\",
    "C:",
    "C:Books",
    "",
    "\\",
    "\\Books",
    "Books\\a",
    "\\\\nas\\share\\Books\\main.bean",
    "\\\\nas\\share",
    "\\\\nas",
    "\\\\nas\\",
    "//nas/share/x.bean",
    "\\\\?\\C:\\Books\\main.bean",
    "\\\\?\\C:",
    "\\\\?\\c:\\x",
    "\\\\?\\",
    "\\\\?",
    "\\\\?\\UNC\\nas\\share\\x.bean",
    "\\\\?\\UNC\\nas",
    "\\\\?\\UNC",
    "\\\\?\\C:/Books",
    "\\\\?\\Volume{01234567-89ab-cdef-0123-456789abcdef}\\Books\\x.bean",
    "\\\\?\\GLOBALROOT\\Device\\x",
    "\\\\.\\C:\\x",
    "\\??\\C:\\x",
    "C:\\Books\\..\\real",
    "C:\\Books\\.\\x",
    "C:\\Books\\aux.txt",
    "C:\\Books\\nul .bean",
    "C:\\Books\\trail.",
    "C:\\Books\\trail ",
    "C:\\Books\\a:stream",
    "C:\\Books\\a?b",
    "C:\\Books\\COM\u00b9",
    "C:\\Books\\x\0y",
    "C:\\\\Books\\\\\\x.bean",
]

ENGINE_ANSWERS = """
import json, sys
from ledger_engine.fs.pathmodel import WINDOWS, MalformedPathError
out = []
for p in json.load(sys.stdin):
    try:
        out.append(list(WINDOWS.split(p)))
    except MalformedPathError:
        out.append(None)
print(json.dumps(out))
"""


def engine_python() -> Path:
    found = ENGINE_VENV / ("Scripts/python.exe" if (ENGINE_VENV / "Scripts").is_dir() else "bin/python")
    if not found.exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return found


def test_the_sidecar_splits_windows_paths_as_the_engine_does() -> None:
    """Must not pass: an incomplete root (`\\\\?\\C:` is a root, `\\\\nas` is malformed) or a device path taken where
    the engine's model answers otherwise, or the twin drifting from the engine's model after either changes."""
    done = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter and a constant)
        [str(engine_python()), "-I", "-c", ENGINE_ANSWERS],
        input=json.dumps(PATHS),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=REPO / "engine",
    )
    assert done.returncode == 0, done.stderr[-2000:]
    engine = json.loads(done.stdout)
    ours = [None if (s := split(p)) is None else list(s) for p in PATHS]
    assert ours == engine
    assert (len(ours), ours.count(None)) == (39, 26), "the list keeps its cases (13 paths, 26 malformed)"
