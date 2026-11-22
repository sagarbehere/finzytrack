"""The sidecar's twin of the engine's Windows path model (`app.platform.windows_paths`; S0131, R2-1): the same answers
as `ledger_engine.fs.pathmodel.WINDOWS.split` on one list of paths, asked of the engine's own model in its own
environment (the sidecar never imports the engine, ADR-0054). Pure: runs on every OS."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.platform.windows_paths import normal, split

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


ENGINE_NORMAL = """
import json, sys
from ledger_engine.fs.pathmodel import WINDOWS
out = [[WINDOWS.normpath(p), WINDOWS.is_absolute(p) and WINDOWS.normpath(p) == p] for p in json.load(sys.stdin)]
print(json.dumps(out))
"""

SPELLINGS = [
    ("c:\\Books\\main.bean", "C:\\Books\\main.bean"),
    ("C:\\Books\\main.bean", "C:\\Books\\main.bean"),
    ("\\\\?\\C:\\Books\\main.bean", "C:\\Books\\main.bean"),
    ("\\\\?\\c:\\Books\\main.bean", "C:\\Books\\main.bean"),
    ("\\\\?\\UNC\\nas\\share\\Books\\main.bean", "\\\\nas\\share\\Books\\main.bean"),
    ("\\\\?\\unc\\nas\\share\\x.bean", "\\\\nas\\share\\x.bean"),
    (
        "\\\\?\\Volume{01234567-89ab-cdef-0123-456789abcdef}\\Books\\x.bean",
        "\\\\?\\Volume{01234567-89ab-cdef-0123-456789abcdef}\\Books\\x.bean",
    ),
    ("c:/Books/main.bean", "C:\\Books\\main.bean"),
    ("C:\\\\Books\\\\\\x.bean", "C:\\Books\\x.bean"),
    ("c:\\Books\\", "C:\\Books"),
    ("c:\\", "C:\\"),
    ("\\\\?\\C:", "C:\\"),
    ("//nas/share/x.bean", "\\\\nas\\share\\x.bean"),
    ("\\\\nas\\share", "\\\\nas\\share\\"),
]


@pytest.mark.parametrize(("given", "sent"), SPELLINGS)
def test_S0146_a_windows_path_is_put_in_the_engines_normal_form(given: str, sent: str) -> None:
    """Owner, 2026-11-22 (BL-0087's row): the sidecar normalizes a path before it reaches the engine: the drive letter
    upper case, the long-path prefix taken off (`\\\\?\\UNC\\server\\share` to `\\\\server\\share`), a volume GUID path
    keeping its prefix; and the separators as Windows reads them. Must not pass: a lower-case drive, a prefix or a
    `/` sent on."""
    assert normal(given) == sent


@pytest.mark.parametrize(
    "odd",
    [
        "C:\\Books\\..\\real",
        "C:\\Books\\.\\x",
        "C:\\Books\\aux.txt",
        "C:\\Books\\trail.",
        "Books\\a",
        "\\Books",
        "C:Books",
        "\\\\.\\C:\\x",
        "\\\\?\\GLOBALROOT\\Device\\x",
        "\\\\nas",
        "",
        "/Books/a",
    ],
)
def test_S0146_a_path_the_model_does_not_take_is_sent_as_it_is(odd: str) -> None:
    """Must not pass: a malformed or relative path made into a well-formed one (`..` resolved, a reserved name or a
    trailing dot dropped, a root added): it goes to the engine unchanged, which refuses it, naming why."""
    assert normal(odd) == odd


def test_S0146_the_sidecars_normal_form_is_the_engines_and_passes_its_check() -> None:
    """The twin against the engine's own model, on every path the split list and the spellings hold that `split`
    takes: `normal` is `WindowsPaths.normpath`, and what it gives passes the contract's check (`contract._normal`:
    absolute and its own normal form)."""
    given = [p for p in PATHS + [g for g, _ in SPELLINGS] if split(p) is not None]
    sent = [normal(p) for p in given]
    done = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter and a constant)
        [str(engine_python()), "-I", "-c", ENGINE_NORMAL],
        input=json.dumps(given + sent),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=REPO / "engine",
    )
    assert done.returncode == 0, done.stderr[-2000:]
    answers = json.loads(done.stdout)
    assert [a[0] for a in answers[: len(given)]] == sent, "the engine's normal form of each given path"
    assert all(a[1] for a in answers[len(given) :]), "every path the sidecar sends passes the contract's check"
    assert len(given) == 27
