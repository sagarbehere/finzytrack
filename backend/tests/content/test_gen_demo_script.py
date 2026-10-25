"""The demo build step `backend/scripts/gen_demo.py` (S0027; L10-18; review:L10 §1.5; ADR-0022).

Spec lines checked here:
- review:L10 §1.5 "The build generates the demo pack once per release with a recorded `generatedFor`. It never
  rewrites a tracked source file during the build": the output goes to a new or empty folder outside the source
  tree (or under the ignored `build/`), never over existing files, with a `generated-for.json` beside the ledger.
- ADR-0022 "the anchor is the release tag's date (`SOURCE_DATE_EPOCH`)": with no anchor given the build refuses;
  it never falls back to the build clock.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.cap("CAP-DATA-DEMO-GEN")

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
SCRIPT = BACKEND / "scripts" / "gen_demo.py"


def run(*args: str, epoch: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"}
    if epoch is not None:
        env["SOURCE_DATE_EPOCH"] = epoch
    return subprocess.run(  # noqa: S603 (fixed argv: this interpreter and the script under test)
        [sys.executable, str(SCRIPT), *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_writes_the_ledger_and_its_generated_for_record(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == ["generated-for.json", "main.beancount"]
    record = json.loads((out / "generated-for.json").read_text())
    assert record == {"anchor": "2026-10-25", "fragments": ["household"], "generator": 1, "size": "demo"}
    assert (out / "main.beancount").read_text().startswith("; ")


def test_source_date_epoch_is_the_anchor_when_no_flag_is_given(tmp_path: Path) -> None:
    r = run("--size", "demo", "--out", str(tmp_path / "a"), epoch="1792972799")
    assert r.returncode == 0, r.stderr
    assert json.loads((tmp_path / "a" / "generated-for.json").read_text())["anchor"] == "2026-10-25"
    r = run("--size", "demo", "--source-date-epoch", "1792972799", "--out", str(tmp_path / "b"))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "a" / "main.beancount").read_bytes() == (tmp_path / "b" / "main.beancount").read_bytes()


def test_without_an_anchor_the_build_refuses(tmp_path: Path) -> None:
    r = run("--size", "demo", "--out", str(tmp_path / "x"))
    assert r.returncode == 2
    assert "SOURCE_DATE_EPOCH" in r.stderr
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize("epoch", ["", "soon", "-5"])
def test_a_malformed_source_date_epoch_in_the_environment_is_refused(tmp_path: Path, epoch: str) -> None:
    r = run("--size", "demo", "--out", str(tmp_path / "x"), epoch=epoch)
    assert r.returncode == 2
    assert "SOURCE_DATE_EPOCH" in r.stderr
    assert not (tmp_path / "x").exists()


def test_two_anchor_flags_are_refused(tmp_path: Path) -> None:
    r = run("--size", "demo", "--anchor", "2026-10-25", "--source-date-epoch", "0", "--out", str(tmp_path / "x"))
    assert r.returncode == 2
    assert not (tmp_path / "x").exists()


def test_existing_files_are_never_overwritten(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "main.beancount").write_text("keep me\n")
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(out))
    assert r.returncode == 2
    assert "not empty" in r.stderr
    assert (out / "main.beancount").read_text() == "keep me\n"
    assert sorted(p.name for p in out.iterdir()) == ["main.beancount"]


def test_an_empty_existing_folder_is_accepted(tmp_path: Path) -> None:
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(tmp_path))
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "inside",
    ["backend/src/app/content/demo/out", "corpora/ledgers/demo", "design/demo", "build-x/demo", "."],
)
def test_the_source_tree_is_refused(inside: str) -> None:
    target = REPO / inside
    existed = target.exists()
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(target))
    assert r.returncode == 2
    assert "source tree" in r.stderr
    assert target.exists() == existed


def test_a_symlink_into_the_source_tree_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(REPO / "design")
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(link / "demo"))
    assert r.returncode == 2
    assert "source tree" in r.stderr
    assert not (REPO / "design" / "demo").exists()


def test_the_ignored_build_folder_is_accepted(tmp_path: Path) -> None:
    # `build/` is ignored by .gitignore ("Packaging"), so nothing tracked can be rewritten there.
    target = REPO / "build" / f"s0027-test-{tmp_path.name}"
    try:
        r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(target))
        assert r.returncode == 0, r.stderr
        assert (target / "main.beancount").is_file()
    finally:
        for p in sorted(target.glob("*")):
            p.unlink()
        if target.exists():
            target.rmdir()


def test_the_repository_spelled_in_another_case_is_refused() -> None:
    # S0027 verifier F4: on a case-insensitive disk `.../FINZYTRACK/design` is the repository's design folder.
    other = Path(str(REPO).swapcase())
    if not other.exists():
        pytest.skip("case-sensitive file system: the other spelling is another folder")
    target = other / "design" / "s0027-case-probe"
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(target))
    assert r.returncode == 2
    assert "source tree" in r.stderr
    assert not (REPO / "design" / "s0027-case-probe").exists()


def test_build_spelled_in_another_case_is_refused(tmp_path: Path) -> None:
    # Only the exact ignored folder `build/` is allowed; `BUILD/` is not in .gitignore on a case-sensitive disk.
    target = REPO / "BUILD" / f"s0027-{tmp_path.name}"
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(target))
    assert r.returncode == 2
    assert not target.exists()


def test_an_anchor_the_generator_cannot_use_is_refused_cleanly(tmp_path: Path) -> None:
    # S0027 verifier F5: year 1 leaves no room for the span; refused with a message, no traceback, nothing written.
    r = run("--size", "demo", "--anchor", "0001-06-01", "--out", str(tmp_path / "x"))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert "refused" in r.stderr
    assert not (tmp_path / "x").exists()


def test_a_file_in_place_of_a_parent_folder_is_refused_cleanly(tmp_path: Path) -> None:
    (tmp_path / "afile").write_text("x")
    r = run("--size", "demo", "--anchor", "2026-10-25", "--out", str(tmp_path / "afile" / "out"))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert (tmp_path / "afile").read_text() == "x"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_demo_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failure_after_the_first_write_leaves_nothing(tmp_path: Path) -> None:
    # The second file cannot be written (its folder does not exist): no output folder and no temp folder remain.
    out = tmp_path / "out"
    write_all: Callable[[Path, dict[str, str]], None] = load_script().write_all
    with pytest.raises(OSError, match="missing"):
        write_all(out, {"main.beancount": "x\n", "missing/b.beancount": "y\n"})
    assert list(tmp_path.iterdir()) == []


def test_an_empty_existing_folder_is_replaced_by_the_written_one(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_all: Callable[[Path, dict[str, str]], None] = load_script().write_all
    write_all(out, {"main.beancount": "x\n"})
    assert [p.name for p in tmp_path.iterdir()] == ["out"]
    assert (out / "main.beancount").read_text() == "x\n"
