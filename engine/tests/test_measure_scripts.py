"""Every measurement script under `engine/scripts/` runs on a tiny ledger (S0106, BL-0235).

The scripts are not product code, and for a while nothing ran them: `measure_mirror_build.py` crashed from S0088 on
and `measure_save.py` from S0087 on, unseen until the numbers were needed. Each script here runs end to end in its
own interpreter, as a person runs it, on a three-entry ledger in a scratch folder, and must print its report. The
cases are checked against the folder, so a new `measure_*.py` without a case fails here.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]
SCRIPTS = ENGINE / "scripts"

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


def run(script: str, *args: str) -> str:
    """The script under this interpreter (the engine's environment), from `engine/` as its usage line says."""
    done = subprocess.run(  # noqa: S603 (fixed argv: a script of this repository)
        [sys.executable, "-I", str(SCRIPTS / script), *args],
        cwd=ENGINE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def ledger(tmp_path: Path) -> Path:
    folder = tmp_path / "L"
    folder.mkdir()
    root = folder / "main.beancount"
    root.write_bytes(LEDGER)
    return root


def mirror_build(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    text = run("measure_mirror_build.py", str(ledger(tmp_path)), str(out), "--runs", "1")
    assert "run 0: entries 5 postings 4" in text, text
    assert "peak RSS" in text, text


def save(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    text = run("measure_save.py", str(ledger(tmp_path)), str(work), "--runs", "1")
    for part in ("open (", "save ", "durable order:", "rebuild (", "restore:", "Resolved"):
        assert part in text, text


def large_result(tmp_path: Path) -> None:
    text = run("measure_large_result.py", str(ledger(tmp_path)), "--runs", "1")
    for part in ("postings 4", "reply line", "encode", "decode", "peak"):
        assert part in text, text


CASES: dict[str, Callable[[Path], None]] = {
    "measure_mirror_build.py": mirror_build,
    "measure_save.py": save,
    "measure_large_result.py": large_result,
}


def test_every_measurement_script_has_a_case() -> None:
    """Must not pass: a `measure_*.py` in the folder that no case runs."""
    assert {p.name for p in SCRIPTS.glob("measure_*.py")} == set(CASES)


@pytest.mark.skipif(sys.platform != "darwin", reason="the save's port is macOS-only until BL-0087")
def test_measure_save_runs(tmp_path: Path) -> None:
    save(tmp_path)


@pytest.mark.parametrize("name", ["measure_mirror_build.py", "measure_large_result.py"])
def test_measurement_script_runs(name: str, tmp_path: Path) -> None:
    CASES[name](tmp_path)
