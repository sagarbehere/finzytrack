"""The local tier's D2 runner (`engine/scripts/d2_local.py`, S0158): the overlay load against Beancount's own load on
copies of real ledgers, reading only inside `FT_LOCAL_TIER_DIR` and printing counts, case IDs and verdicts only
(INV-23, INV-24; PROCESS.md §6 T-local, §8).

The runner runs here as the owner runs it (its own interpreter, a fresh process per ledger) on corpus ledgers copied
into a temp T-local folder; the DIFFERENT case and the policy's refusals run its per-ledger step in this process, with
the overlay's result changed on purpose. Must-not-pass: an unset or unsafe T-local folder, a root outside it, in the
repo or behind a link out of it, and a ledger that reads outside it, each refused with exit 2.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ledger_engine.ledger import overlay
from ledger_engine.ledger.overlay import Loaded, LoadResult
from ledger_engine.ledger.policy import LoadPolicy
from scripts import d2_local
from tests.ledger import parity

ENGINE = Path(__file__).resolve().parents[2]
REPO = ENGINE.parent
SCRIPT = ENGINE / "scripts" / "d2_local.py"

pytestmark = pytest.mark.inv("INV-24")

TIER_ENV = "FT_LOCAL_TIER_DIR"
EQUAL_CASES = ("fava-features", "includes", "plugins-local", "documents", "options", "custom-roots")
"""Parity corpus cases the corpus test holds equal: includes and globs, a local plugin with `pythonpath`, documents
roots, options and custom roots."""

PRIVATE = b"""option "title" "Hidden Title Marker"
option "operating_currency" "USD"
2020-01-01 open Assets:Zebra:Checking USD
2020-01-01 open Expenses:Quokka:Groceries USD
2020-01-01 open Income:Salary USD
2020-01-05 * "Quixotic Payee" "Narration Marker"
  Assets:Zebra:Checking  1234.56 USD
  Income:Salary
2020-01-06 * "Food"
  Expenses:Quokka:Groceries  98.76 USD
  Assets:Zebra:Checking
2020-01-07 balance Assets:Zebra:Checking 1.00 USD
"""
LEDGER_TEXT = ("Hidden", "Zebra", "Quokka", "Quixotic", "Narration", "1234", "98.76", "Salary", "secretname", "USD")


def run(*args: str, tier: Path | None) -> subprocess.CompletedProcess[str]:
    """The runner as its docstring says to run it, from `engine/` with the engine's interpreter."""
    env = {k: v for k, v in os.environ.items() if k != TIER_ENV}
    if tier is not None:
        env[TIER_ENV] = str(tier)
    return subprocess.run(  # noqa: S603 (this interpreter, a script of this repository)
        [sys.executable, str(SCRIPT), *args], cwd=ENGINE, env=env, capture_output=True, text=True, check=False
    )


def tree(folder: Path) -> dict[str, bytes | None]:
    """Every entry under `folder`: a file's bytes, a folder's None."""
    return {
        p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None) for p in sorted(folder.rglob("*"))
    }


@pytest.fixture
def tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = (tmp_path / "tier").resolve()
    folder.mkdir()
    monkeypatch.setenv(TIER_ENV, str(folder))
    return folder


def copy_corpus(tier: Path) -> dict[str, Path]:
    shutil.copytree(parity.CORPUS, tier / "parity", ignore=shutil.ignore_patterns("__pycache__"))
    return {case: tier / "parity" / case / "main.beancount" for case in EQUAL_CASES}


def private_ledger(tier: Path) -> Path:
    folder = tier / "secretname-folder"
    folder.mkdir()
    root = folder / "secretname.beancount"
    root.write_bytes(PRIVATE)
    return root


# ---- EQUAL, as the owner runs it ----------------------------------------------------------------------------------


def test_corpus_ledgers_are_equal_and_nothing_is_written(tier: Path) -> None:
    roots = copy_corpus(tier)
    before = tree(tier)
    done = run(*(str(r) for r in roots.values()), tier=tier)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    for i in range(1, len(roots) + 1):
        assert any(re.fullmatch(rf"L{i} EQUAL entries=\d+ errors=\d+ options=\d+", line) for line in lines), lines
    assert f"{len(roots)} EQUAL, 0 DIFFERENT, 0 REFUSED, 0 ERROR" in done.stdout
    assert tree(tier) == before  # no mirror, cache, pickle or bytecode beside the copies
    assert str(tier) not in done.stdout + done.stderr


def test_the_output_names_no_ledger_text(tier: Path) -> None:
    root = private_ledger(tier)
    done = run(str(root), tier=tier)
    assert done.returncode == 0, done.stdout + done.stderr
    for text in (*LEDGER_TEXT, str(tier), str(root)):
        assert text not in done.stdout + done.stderr, text


NOISY = b"""import sys

__plugins__ = ["noisy"]


def noisy(entries, options_map):
    for e in entries:
        print(getattr(e, "narration", ""), getattr(e, "account", ""), options_map["filename"])
        sys.stderr.write(repr(e) + "\\n")
    return entries, []
"""


def test_what_a_plugin_prints_goes_nowhere(tier: Path) -> None:
    """A plugin that prints every entry (and the root's path) on both outputs: none of it reaches the run's output."""
    root = private_ledger(tier)
    root.write_bytes(b'option "insert_pythonpath" "TRUE"\nplugin "noisy_s0158"\n' + PRIVATE)
    (root.parent / "noisy_s0158.py").write_bytes(NOISY)
    before = tree(tier)
    done = run(str(root), tier=tier)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "L1 EQUAL" in done.stdout
    for text in (*LEDGER_TEXT, str(tier), str(root)):
        assert text not in done.stdout + done.stderr, text
    assert tree(tier) == before  # no bytecode beside the plugin


# ---- DIFFERENT --------------------------------------------------------------------------------------------------


def changed_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlay's result with an entry dropped and an option changed (a deliberate divergence)."""
    real = overlay.load

    def load(root: str, candidates: dict[str, bytes], policy: LoadPolicy) -> LoadResult:
        result = real(root, candidates, policy)
        assert isinstance(result, Loaded)
        return replace(result, entries=result.entries[:-1], options={**result.options, "title": "Hidden Other"})

    monkeypatch.setattr(overlay, "load", load)


def test_a_different_overlay_result_is_different_and_names_no_ledger_text(
    tier: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = private_ledger(tier)
    changed_overlay(monkeypatch)
    report = d2_local.measure(root)
    assert report.verdict == "DIFFERENT"
    assert report.kinds == {"plain.entries": 1, "plain.options": 1, "candidates.entries": 1, "candidates.options": 1}
    assert d2_local.main([str(root)], child=d2_local.measure) == 1
    out = capsys.readouterr()
    assert re.search(r"^L1 DIFFERENT entries=6 errors=1 options=\d+ differ: candidates\.entries=1 ", out.out, re.M)
    for text in (*LEDGER_TEXT, str(tier), str(root)):
        assert text not in out.out + out.err, text


def test_a_child_answer_cannot_carry_text(tier: Path) -> None:
    """The parent prints only verdicts, counts and its own words: an answer with any other kind, reason or value is an
    ERROR, never echoed."""
    answers: list[object] = [
        {"verdict": "EQUAL", "entries": 1, "errors": 0, "options": 1, "kinds": {}, "reason": "Zebra"},
        {"verdict": "DIFFERENT", "entries": 1, "errors": 0, "options": 1, "kinds": {"Zebra": 1}, "reason": ""},
        {
            "verdict": "DIFFERENT",
            "entries": 1,
            "errors": 0,
            "options": 1,
            "kinds": {"plain.entries": "Z"},
            "reason": "",
        },
        {"verdict": "Zebra", "entries": 1, "errors": 0, "options": 1, "kinds": {}, "reason": ""},
        {"verdict": "EQUAL", "entries": "Zebra", "errors": 0, "options": 1, "kinds": {}, "reason": ""},
        {"verdict": "EQUAL", "entries": True, "errors": 0, "options": 1, "kinds": {}, "reason": ""},
        {"verdict": "EQUAL", "entries": -1, "errors": 0, "options": 1, "kinds": {}, "reason": ""},
        ["EQUAL"],
    ]
    for answer in answers:
        report = d2_local.Report.decode(answer)
        assert report.verdict == "ERROR", answer
        assert "Zebra" not in d2_local.line("L1", report)


# ---- must not pass: refused inputs ----------------------------------------------------------------------------------


def test_an_unset_tier_is_refused(tmp_path: Path) -> None:
    root = private_ledger(tmp_path)
    done = run(str(root), tier=None)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "d2-local: nothing was loaded" in done.stdout  # every input is checked before any ledger is read
    assert "REFUSED" in done.stdout + done.stderr
    assert "EQUAL" not in done.stdout


def test_the_repo_as_the_tier_is_refused() -> None:
    root = REPO / "corpora" / "ledgers" / "parity" / "fava-features" / "main.beancount"
    done = run(str(root), tier=REPO)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "d2-local: nothing was loaded" in done.stdout  # every input is checked before any ledger is read
    assert "EQUAL" not in done.stdout


def test_a_root_outside_the_tier_is_refused(tier: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    root = private_ledger(outside)
    done = run(str(root), tier=tier)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "d2-local: nothing was loaded" in done.stdout  # every input is checked before any ledger is read
    assert "L1 REFUSED" in done.stdout
    assert str(root) not in done.stdout + done.stderr


def test_a_root_in_the_repo_is_refused(tier: Path) -> None:
    root = REPO / "corpora" / "ledgers" / "parity" / "fava-features" / "main.beancount"
    done = run(str(root), tier=tier)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "d2-local: nothing was loaded" in done.stdout  # every input is checked before any ledger is read
    assert "L1 REFUSED" in done.stdout


def test_a_root_that_links_out_of_the_tier_is_refused(tier: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tier / "link.beancount").symlink_to(private_ledger(outside))
    done = run(str(tier / "link.beancount"), tier=tier)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "d2-local: nothing was loaded" in done.stdout  # every input is checked before any ledger is read
    assert "L1 REFUSED" in done.stdout


def test_a_missing_root_and_a_folder_are_refused(tier: Path) -> None:
    done = run(str(tier / "absent.beancount"), str(tier), tier=tier)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "d2-local: nothing was loaded" in done.stdout  # every input is checked before any ledger is read
    assert "L1 REFUSED" in done.stdout
    assert "L2 REFUSED" in done.stdout


def test_one_refused_root_stops_every_load(tier: Path, tmp_path: Path) -> None:
    good = private_ledger(tier)
    done = run(str(good), str(tmp_path / "elsewhere.beancount"), tier=tier)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "d2-local: nothing was loaded" in done.stdout  # every input is checked before any ledger is read
    assert "EQUAL" not in done.stdout


def outside_ledger(tier: Path, tmp_path: Path, line: str) -> Path:
    outside = tmp_path / "elsewhere"
    (outside / "docs").mkdir(parents=True, exist_ok=True)
    (outside / "other.beancount").write_text("2020-01-01 open Assets:Other USD\n", encoding="utf-8")
    folder = tier / "reads-out"
    folder.mkdir()
    root = folder / "main.beancount"
    root.write_text(line.format(outside=outside.as_posix()) + "\n2020-01-01 open Assets:Cash USD\n", encoding="utf-8")
    return root


@pytest.fixture
def no_beancount_load(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(root: Path) -> parity.Result:
        pytest.fail("Beancount's load ran on a ledger the policy refused")

    monkeypatch.setattr(parity, "beancount_load", refuse)
    return


@pytest.mark.usefixtures("no_beancount_load")
def test_a_ledger_that_reads_outside_the_tier_is_refused_before_beancount_loads_it(
    tier: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for line, reason in (
        ('include "{outside}/other.beancount"', "read-refused"),
        ('option "documents" "{outside}/docs"', "documents-refused"),
    ):
        root = outside_ledger(tier, tmp_path, line)
        report = d2_local.measure(root)
        assert (report.verdict, report.reason) == ("REFUSED", reason), line
        assert d2_local.main([str(root)], child=d2_local.measure) == 2
        assert str(tmp_path) not in capsys.readouterr().out
        shutil.rmtree(root.parent)


WIDEN = b"""__plugins__ = ["widen"]


def widen(entries, options_map, config):
    options_map["include"].append(config)
    return entries, []
"""


@pytest.mark.usefixtures("no_reads_outside")
def test_a_file_a_plugin_adds_to_the_includes_is_not_read_when_outside_the_tier(tier: Path, tmp_path: Path) -> None:
    """The candidates are read from Beancount's `include` list, which a plugin may extend: a name outside the folder
    is refused before it is read."""
    root = outside_ledger(
        tier, tmp_path, 'option "insert_pythonpath" "TRUE"\nplugin "widen_s0158" "{outside}/other.beancount"'
    )
    (root.parent / "widen_s0158.py").write_bytes(WIDEN)
    report = d2_local.measure(root)
    assert (report.verdict, report.reason) == ("REFUSED", "read-refused")


@pytest.fixture
def no_reads_outside(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        assert not self.resolve().is_relative_to((tmp_path / "elsewhere").resolve()), "read outside the tier"
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_the_policy_allows_only_the_tier(tier: Path, tmp_path: Path) -> None:
    policy = d2_local.TierPolicy()
    inside, outside = tier / "a" / "b.beancount", tmp_path / "elsewhere" / "b.beancount"
    for ask in (policy.may_read, policy.may_add_pythonpath, policy.may_scan_documents):
        assert ask(str(inside))
        assert not ask(str(outside))
        assert not ask(str(REPO / "corpora"))
    assert policy.may_run_plugin("any.module")  # a plugin's code is found on the `pythonpath` the policy confines
