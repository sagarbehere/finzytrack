"""The owner's hand check on Windows (S0150, BL-0283, BL-0087): `scripts/windows_hand_check.py`, run as the README of
`scripts/windows_hand_check/` says, drives the product's open and save on a scratch ledger in a OneDrive folder and
prints a report to paste back. Here its automatic part (`--auto`: no prompt, every step that needs the owner's hands
reported as skipped) runs on a scratch folder on every OS, so the script cannot break unseen before the owner runs it
once: the engine's placeholder mode, the setup, the open, a save, the burst of saves, the looks at each file, the
cloud-only step's request (OneDrive's `attrib`, which only acts in a OneDrive folder) and the report's privacy (no
absolute path). The OneDrive facts themselves are the owner's run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "windows_hand_check.py"
ENGINE_VENV = BACKEND.parent / "engine" / ".venv"

pytestmark = pytest.mark.skipif(not ENGINE_VENV.is_dir(), reason="needs the engine's environment (engine/.venv)")


def run(*args: str, cwd: Path = BACKEND) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (this interpreter, our own script)
        [sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, check=False, timeout=900, cwd=cwd
    )


@pytest.fixture
def report(tmp_path: Path) -> tuple[dict[str, Any], str, Path]:
    scratch = tmp_path / "check"
    scratch.mkdir()
    done = run("--folder", str(scratch), "--auto")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    return cast("dict[str, Any]", json.loads(done.stdout)), done.stdout, scratch


def test_the_automatic_part_opens_and_saves_through_the_product_and_reports_each_step(
    report: tuple[dict[str, Any], str, Path],
) -> None:
    """Must not pass: the script fails, or a step it runs by itself is missing from the report or does not save."""
    got, _, scratch = report
    assert got["platform"] == sys.platform
    assert got["mode"]["engine"] == (1 if os.name == "nt" else None), got["mode"]
    assert got["mode"]["packaged"] == "not asked (no --packaged)"
    assert got["open"]["outcome"] == "opened", got["open"]
    assert got["save"]["outcome"] == "saved", got["save"]
    assert got["save"]["text_on_disk"] == "the plan's text", got["save"]
    burst = got["burst"]
    assert [b["outcome"] for b in burst["saves"]] == ["saved"] * burst["count"], burst
    assert burst["count"] >= 5
    for step in ("offline", "paused", "q1_unlisted_folder", "q2_cloud_only_plugin"):
        assert got[step] == "skipped (--auto)", (step, got[step])
    assert set(got["looks"]) >= {"after_setup", "after_save", "after_free_up_space"}, got["looks"]
    assert (scratch / "report.json").is_file()
    assert json.loads((scratch / "report.json").read_text(encoding="utf-8")) == got


def test_the_report_names_no_absolute_path(report: tuple[dict[str, Any], str, Path]) -> None:
    """Must not pass (INV-23: the owner pastes the report into a session, and it may enter a journal): the scratch
    folder, the temp folder or the user's home appears in the report."""
    _, text, scratch = report
    for path in {str(scratch), str(scratch.resolve()), str(Path.home()), os.path.realpath(scratch)}:
        assert path not in text
        assert json.dumps(path)[1:-1] not in text  # as JSON writes it (a Windows path's backslashes doubled)


def test_a_folder_that_is_not_empty_is_refused_and_left_as_it_is(tmp_path: Path) -> None:
    """Must not pass: the script writes in a folder that holds anything (it may only write its own scratch folder)."""
    (tmp_path / "mine.txt").write_bytes(b"keep")
    done = run("--folder", str(tmp_path), "--auto")
    assert done.returncode == 2, done.stdout + done.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mine.txt"]
    assert (tmp_path / "mine.txt").read_bytes() == b"keep"


def test_without_a_folder_off_windows_it_refuses_before_anything() -> None:
    """Must not pass: off Windows, or without OneDrive, the script makes a folder anywhere."""
    if os.name == "nt" and os.environ.get("ONEDRIVE"):  # Windows: names are case-blind
        pytest.skip("this PC has OneDrive: the script would make its scratch folder there")
    done = run("--auto")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "OneDrive" in done.stderr
