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


LAYOUTS = ("one", "files12", "meta20")
JOBS = ("preview", "save", "rebuild")


def write(tmp_path: Path) -> None:
    """S0119: one write on the product's path (the preview, its confirm, the rebuild until the publish) in each
    layout derived from the ledger: one file, the same entries in 12 files under the root, and the 20 MB recipe."""
    folder = tmp_path / "L"
    folder.mkdir()
    (folder / "main.beancount").write_bytes(LEDGER)
    args = [str(folder / "main.beancount"), "--runs", "1"]
    for layout in LAYOUTS:
        args += ["--layout", layout]
    report = run("measure_write.py", *args, tmp_path=tmp_path)
    for key in ("path", "producer", "layouts", "runs", "summary"):
        assert key in report, report
    layouts = cast("dict[str, dict[str, object]]", report["layouts"])
    assert set(layouts) == set(LAYOUTS), report
    assert layouts["one"]["files"] == 1, layouts
    assert layouts["files12"]["files"] == 13, layouts  # the root and 12 files under it, one glob include
    for name in LAYOUTS:
        assert layouts[name]["transactions"] == 2, layouts
    # the recipe: five metadata lines on every transaction, nothing else changed
    assert layouts["meta20"]["lines"] == cast("int", layouts["one"]["lines"]) + 5 * 2, layouts
    runs = cast("list[dict[str, object]]", report["runs"])
    assert sorted(str(r["layout"]) for r in runs) == sorted(LAYOUTS), report
    for r in runs:
        assert r["outcome"] == "published", r
        for job in JOBS:
            assert cast("float", r[f"{job}_ms"]) > 0, r
            assert cast("float", r[f"{job}_peak_rss_mb"]) > 0, r
        assert cast("float", r["total_ms"]) >= cast("float", r["confirm_to_published_ms"]) > 0, r
        assert "load_average" in r, r
    summary = cast("dict[str, dict[str, object]]", report["summary"])
    assert set(summary) == set(LAYOUTS), report
    for name in LAYOUTS:
        for key in (*(f"{j}_ms" for j in JOBS), "confirm_to_published_ms", "total_ms"):
            assert key in summary[name], summary


CASES: dict[str, Callable[[Path], None]] = {
    "measure_mirror_reads.py": mirror_reads,
    "measure_health.py": health,
    "measure_protocol.py": protocol,
    "measure_write.py": write,
}


NEEDS_ENGINE = pytest.mark.skipif(not ENGINE_VENV.is_dir(), reason="needs the engine's environment (engine/.venv)")
MAC_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="the save's port is macOS-only until BL-0087")
MARKS: dict[str, tuple[pytest.MarkDecorator, ...]] = {
    "measure_health.py": (NEEDS_ENGINE,),
    "measure_protocol.py": (NEEDS_ENGINE, MAC_ONLY),
    "measure_write.py": (NEEDS_ENGINE, MAC_ONLY),
}


def test_every_measurement_script_has_a_case() -> None:
    """Must not pass: a `measure_*.py` in the folder that no case runs."""
    assert {p.name for p in SCRIPTS.glob("measure_*.py")} == set(CASES)


@pytest.mark.parametrize("name", [pytest.param(n, marks=MARKS.get(n, ())) for n in sorted(CASES)])
def test_measurement_script_runs(name: str, tmp_path: Path) -> None:
    """Every case runs (S0106 verifier F2: a case without its own test function never ran)."""
    CASES[name](tmp_path)


@NEEDS_ENGINE
@MAC_ONLY
def test_measure_write_reports_a_run_that_does_not_publish(tmp_path: Path) -> None:
    """S0119 verifier F1. Must not pass: a run that does not publish ends the script without its report, or is counted
    in the summary. A preview whose reply passes the protocol's 16 MiB line (the file's new text: over 16 MiB of
    comments before the tiny ledger) stops the engine; a job that does not answer within `--deadline` ends its run."""
    folder = tmp_path / "L"
    folder.mkdir()
    (folder / "main.beancount").write_bytes((b";" + b"x" * 99 + b"\n") * 170_000 + LEDGER)
    report = run("measure_write.py", str(folder / "main.beancount"), tmp_path=tmp_path)
    (r,) = cast("list[dict[str, object]]", report["runs"])
    assert str(r["outcome"]).startswith("preview: "), r
    assert "preview_ms" in r, r  # the figures measured before the end stay in the run
    stops = cast("list[str]", r["engine_stops"])
    assert any(s.startswith("protocol_violation") for s in stops), r
    assert report["summary"] == {"one": {"published": 0, "not_published": 1}}, report
    (folder / "main.beancount").write_bytes(LEDGER)
    report = run("measure_write.py", str(folder / "main.beancount"), "--deadline", "0.000001", tmp_path=tmp_path)
    (r,) = cast("list[dict[str, object]]", report["runs"])
    assert str(r["outcome"]).startswith("not measured: open: no answer"), r
    assert report["summary"] == {"one": {"published": 0, "not_published": 1}}, report


def test_a_script_that_fails_fails_its_case(tmp_path: Path) -> None:
    """Must not pass: a measurement script that exits non-zero, run as the cases run it, goes unnoticed."""
    (tmp_path / "measure_broken.py").write_text('import sys\nprint("{}")\nsys.exit(1)\n')
    with pytest.raises(AssertionError, match="assert 1 == 0"):
        run("measure_broken.py", tmp_path=tmp_path, scripts=tmp_path)
