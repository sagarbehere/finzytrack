"""The `fixme` marker runs a test as a strict xfail (S0029; tests/conftest.py).

A fixme test's body must fail while its capability is missing; once it passes, the run turns red, so the landing slice
removes the marker and closes the backlog row. A conftest that skipped fixme tests instead would hide both a vacuous
test and a landed capability, so this pins the behaviour on a planted pair of tests.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
PLANTED = """import pytest


@pytest.mark.fixme(id="L4-01", due="M6", reason="capability not built")
def test_not_built_yet() -> None:
    raise ImportError("app.importing")


@pytest.mark.fixme(id="L4-02", due="M6", reason="capability not built")
def test_already_passes() -> None:
    pass
"""


def test_a_fixme_test_is_a_strict_xfail(tmp_path: Path) -> None:
    shutil.copy(CONFTEST, tmp_path / "conftest.py")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --strict-markers -p no:cacheprovider\nmarkers =\n    fixme\n"
    )
    (tmp_path / "test_planted.py").write_text(PLANTED)
    run = subprocess.run(  # noqa: S603 -- our own interpreter on a planted file
        [sys.executable, "-m", "pytest", "-q", "-rxX", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 1, run.stdout
    assert "1 failed, 1 xfailed" in run.stdout  # the passing fixme test fails as XPASS(strict)
    assert "XPASS(strict)" in run.stdout
    assert "fixme L4-01 (due M6): capability not built" in run.stdout
