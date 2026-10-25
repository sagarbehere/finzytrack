"""The `fixme` marker runs a test as a strict xfail that may fail only while its module is missing (S0029, S0032).

A fixme test's body must fail while its capability is missing; once it passes, the run turns red, so the landing slice
removes the marker and closes the backlog row. A conftest that skipped fixme tests instead would hide both a vacuous
test and a landed capability. A fixme that passed on ANY failure would hide a buggy first implementation (seeded
plants G01-G03: a tabular reader dropping a row, a confirm dialog focusing Delete), so the only accepted failure is
`planned()` reporting the planned module or name as missing. This pins the behaviour on planted tests.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
PLANTED = """import pytest

from tests.integrity.planned import planned


@pytest.mark.fixme(id="L4-01", due="M6", reason="capability not built")
def test_module_not_built_yet() -> None:
    planned("app_not_built.core.sources", "adapt_tabular")


@pytest.mark.fixme(id="L4-02", due="M6", reason="capability not built")
def test_submodule_not_built_yet() -> None:
    planned("present.not_built", "x")


@pytest.mark.fixme(id="L4-03", due="M6", reason="capability not built")
def test_name_not_built_yet() -> None:
    planned("present", "not_built")


@pytest.mark.fixme(id="L4-06", due="M6", reason="capability not built")
def test_already_passes() -> None:
    assert planned("present", "VALUE") == 1


@pytest.mark.fixme(id="I5", due="M6", reason="capability not built")
def test_built_but_wrong() -> None:
    assert planned("present", "VALUE") == 2


@pytest.mark.fixme(id="L3-11", due="M6", reason="capability not built")
def test_import_error_raised_by_hand() -> None:
    raise ImportError("app.importing")


@pytest.mark.fixme(id="L3-14", due="M4", reason="capability not built")
def test_module_not_found_raised_by_hand() -> None:
    raise ModuleNotFoundError("app.importing", name="app.importing")


@pytest.mark.fixme(id="L6-12", due="M6", reason="capability not built")
def test_built_module_with_a_missing_dependency() -> None:
    planned("broken", "x")


@pytest.mark.fixme(id="L10-02", due="M3", reason="capability not built")
def test_built_module_whose_import_fails() -> None:
    planned("raising", "x")


@pytest.mark.fixme(id="L10-06", due="M3", reason="capability not built")
def test_built_module_importing_a_missing_name_from_its_package() -> None:
    planned("half.sub", "x")
"""
# A planned module that exists, one that imports a module nobody built, one whose own code fails on import, and a
# submodule importing a name its package does not have.
SIBLINGS = {
    "present/__init__.py": "VALUE = 1\n",
    "broken.py": "import nowhere_to_be_found  # noqa: F401\nx = 1\n",
    "raising.py": "raise ImportError('half built')\n",
    "half/__init__.py": "",
    "half/sub.py": "from half import not_built  # noqa: F401\nx = 1\n",  # ImportError naming the package `half`
}


def run_planted(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    tests = tmp_path / "tests"
    (tests / "integrity").mkdir(parents=True)
    shutil.copy(TESTS / "conftest.py", tests / "conftest.py")
    shutil.copy(TESTS / "integrity" / "planned.py", tests / "integrity" / "planned.py")
    (tests / "__init__.py").write_text("")
    (tests / "integrity" / "__init__.py").write_text("")
    (tests / "test_planted.py").write_text(PLANTED)
    for rel, body in SIBLINGS.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --strict-markers -p no:cacheprovider\nmarkers =\n    fixme\n"
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rfxX", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def outcomes(stdout: str) -> dict[str, str]:
    """{test name: XFAIL | XPASS | FAILED} from pytest's -rfxX summary."""
    found = re.findall(r"^(XFAIL|XPASS|FAILED) tests/test_planted\.py::(\w+)", stdout, flags=re.MULTILINE)
    return {name: kind for kind, name in found}


def test_a_fixme_test_may_fail_only_while_its_planned_module_is_missing(tmp_path: Path) -> None:
    run = run_planted(tmp_path)
    assert run.returncode == 1, run.stdout
    assert outcomes(run.stdout) == {
        "test_module_not_built_yet": "XFAIL",
        "test_submodule_not_built_yet": "XFAIL",
        "test_name_not_built_yet": "XFAIL",
        "test_already_passes": "FAILED",  # XPASS(strict): the capability landed, remove the marker
        "test_built_but_wrong": "FAILED",  # G01-G03: a buggy first implementation is red
        "test_import_error_raised_by_hand": "FAILED",
        "test_module_not_found_raised_by_hand": "FAILED",
        "test_built_module_with_a_missing_dependency": "FAILED",
        "test_built_module_whose_import_fails": "FAILED",
        "test_built_module_importing_a_missing_name_from_its_package": "FAILED",
    }, run.stdout
    assert "XPASS(strict)" in run.stdout
    assert "fixme L4-01 (due M6): capability not built" in run.stdout
    assert "7 failed, 3 xfailed" in run.stdout
