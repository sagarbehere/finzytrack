"""Every measurement script under `backend/scripts/` runs on a tiny input (S0106, BL-0235).

Nothing ran these scripts, so they could break unseen (`engine/scripts/measure_save.py` did, from S0087 to S0106).
Each runs end to end in its own interpreter, from `backend/` as its usage line says, on a small ledger or a small
mirror in a scratch folder, and must print its JSON report. The cases are checked against the folder, so a new
`measure_*.py` without a case fails here. The mirror is the read tests' synthetic one, built from the DDL snapshot
(`tests.mirror.support.make_mirror`): the sidecar's scripts never need the engine's code for it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from tests.mirror.support import make_mirror

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
ENGINE_VENV = BACKEND.parent / "engine" / ".venv"

LEDGER = b"""option "operating_currency" "USD"
2020-01-01 open Assets:Bank:Checking USD
2020-01-01 open Expenses:Food:Groceries USD
2020-01-01 open Income:Salary USD
2020-01-05 * "Salary"
  Assets:Bank:Checking  1000 USD
  Income:Salary
2020-01-06 * "Food"
  Expenses:Food:Groceries  12 USD
  Assets:Bank:Checking
"""


def run(script: str, *args: str, tmp_path: Path, scripts: Path = SCRIPTS) -> dict[str, object]:
    done = subprocess.run(  # noqa: S603 (fixed argv: a script of this repository)
        [sys.executable, "-I", str(scripts / script), *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads(done.stdout)
    assert isinstance(report, dict)
    return cast("dict[str, object]", report)


def mirror_reads(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path / "m.db")
    report = run("measure_mirror_reads.py", str(mirror), "--runs", "1", "--indexes", tmp_path=tmp_path)
    assert "queries" in report, report
    assert "queries_with_indexes" in report, report


def health(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path / "m.db")
    args = ("--size", "demo", "--loads", "1", "--idle", "5", "--reads-mirror", str(mirror), "--read-threads", "2")
    report = run("measure_health.py", *args, tmp_path=tmp_path)
    for key in ("health_ms_idle", "health_ms_reads_only", "health_ms_during_load", "reads_done"):
        assert key in report, report


def protocol(tmp_path: Path) -> None:
    folder = tmp_path / "L"
    folder.mkdir()
    (folder / "main.beancount").write_bytes(LEDGER)
    for extra in ((), ("--warm",)):  # the product's path, and warm engines
        args = (str(folder / "main.beancount"), "--runs", "1", "--pings", "3", *extra)
        report = run("measure_protocol.py", *args, tmp_path=tmp_path)
        for key in ("path", "ping_ms", "save", "save_until_published_ms", "refused_after_decode"):
            assert key in report, report
        saves = report["save"]
        assert isinstance(saves, list), report
        assert saves, report
        outcomes = [s["outcome"] for s in cast("list[dict[str, object]]", saves)]
        assert outcomes == ["saved"] * len(outcomes), report


CASES: dict[str, Callable[[Path], None]] = {
    "measure_mirror_reads.py": mirror_reads,
    "measure_health.py": health,
    "measure_protocol.py": protocol,
}


NEEDS_ENGINE = pytest.mark.skipif(not ENGINE_VENV.is_dir(), reason="needs the engine's environment (engine/.venv)")
MAC_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="the save's port is macOS-only until BL-0087")
MARKS: dict[str, tuple[pytest.MarkDecorator, ...]] = {
    "measure_health.py": (NEEDS_ENGINE,),
    "measure_protocol.py": (NEEDS_ENGINE, MAC_ONLY),
}


def test_every_measurement_script_has_a_case() -> None:
    """Must not pass: a `measure_*.py` in the folder that no case runs."""
    assert {p.name for p in SCRIPTS.glob("measure_*.py")} == set(CASES)


@pytest.mark.parametrize("name", [pytest.param(n, marks=MARKS.get(n, ())) for n in sorted(CASES)])
def test_measurement_script_runs(name: str, tmp_path: Path) -> None:
    """Every case runs (S0106 verifier F2: a case without its own test function never ran)."""
    CASES[name](tmp_path)


def test_a_script_that_fails_fails_its_case(tmp_path: Path) -> None:
    """Must not pass: a measurement script that exits non-zero, run as the cases run it, goes unnoticed."""
    (tmp_path / "measure_broken.py").write_text('import sys\nprint("{}")\nsys.exit(1)\n')
    with pytest.raises(AssertionError, match="assert 1 == 0"):
        run("measure_broken.py", tmp_path=tmp_path, scripts=tmp_path)
