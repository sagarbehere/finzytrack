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
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from ledger_engine.ledger import overlay
from ledger_engine.ledger.overlay import Loaded, LoadResult
from ledger_engine.ledger.policy import LoadPolicy
from scripts import d2_local
from tests import windows_pending
from tests.ledger import parity

ENGINE = Path(__file__).resolve().parents[2]
REPO = ENGINE.parent
SCRIPT = ENGINE / "scripts" / "d2_local.py"

pytestmark = pytest.mark.inv("INV-24")

TIER_ENV = "FT_LOCAL_TIER_DIR"
EQUAL_CASES = ("fava-features", "plugins-local", "documents", "options", "custom-roots")
"""Parity corpus cases the corpus test holds equal and a plain copy keeps loadable: includes, a local plugin with
`pythonpath`, documents roots, options and custom roots (`includes` uses `..`, which the plain-copy rule refuses)."""

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
    """Each case copied as `cp -RL` copies: links followed, plain files only."""
    for case in EQUAL_CASES:
        shutil.copytree(parity.CORPUS / case, tier / case, symlinks=False, ignore=shutil.ignore_patterns("__pycache__"))
    return {case: tier / case / "main.beancount" for case in EQUAL_CASES}


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
        {"verdict": "EQUAL", "entries": 1, "errors": 0, "options": 1, "kinds": {}, "reason": ["unhashable"]},
        {"verdict": ["EQUAL"], "entries": 1, "errors": 0, "options": 1, "kinds": {}, "reason": ""},
        {"verdict": {"x": 1}, "entries": 1, "errors": 0, "options": 1, "kinds": {}, "reason": {}},
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


def no_scan(tier: Path) -> str | None:
    return None


@pytest.mark.usefixtures("no_beancount_load")
def test_a_ledger_that_names_a_path_outside_the_tier_is_refused_before_any_load(
    tier: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The text scan refuses first (`refuse_tree`), and the overlay's policy refuses the same ledgers when the scan is
    switched off (a shape the scan missed): either way Beancount's load never runs."""
    lines = (
        ('include "{outside}/other.beancount"', "read-refused"),
        ('include "../../elsewhere/other.beancount"', "read-refused"),
        ('option "documents" "{outside}/docs"', "documents-refused"),
    )
    for line, policy_reason in lines:
        root = outside_ledger(tier, tmp_path, line)
        report = d2_local.measure(root)
        assert (report.verdict, report.reason) == ("REFUSED", "reaches-out"), line
        assert d2_local.main([str(root)], child=d2_local.measure) == 2
        out = capsys.readouterr().out
        assert "nothing was loaded" in out, line
        assert str(tmp_path) not in out
        with monkeypatch.context() as m:
            m.setattr(d2_local, "refuse_tree", no_scan)
            report = d2_local.measure(root)
        assert (report.verdict, report.reason) == ("REFUSED", policy_reason), line
        shutil.rmtree(root.parent)


@pytest.fixture
def listed_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every folder outside the tier (`tmp_path / "elsewhere"`) that `os.scandir` or `os.listdir` is asked to list."""
    outside = (tmp_path / "elsewhere").resolve()
    outside.mkdir(exist_ok=True)
    listed: list[str] = []
    real_scandir, real_listdir = os.scandir, os.listdir

    def note(p: object) -> None:
        if isinstance(p, (str, os.PathLike)) and Path(os.fspath(p)).resolve().is_relative_to(outside):
            listed.append(os.fspath(p))

    def scandir(p: str = ".") -> Iterator[os.DirEntry[str]]:
        note(p)
        return real_scandir(p)

    def listdir(p: str = ".") -> list[str]:
        note(p)
        return real_listdir(p)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(os, "listdir", listdir)
    return listed


def test_globs_that_leave_the_tier_are_refused_with_no_folder_listed_outside(
    tier: Path, tmp_path: Path, listed_outside: list[str]
) -> None:
    """S0158 V1-2, V2-3: the overlay lists a glob's folders before its policy is asked; the scan refuses such a ledger
    first, so no folder outside the tier is listed (`option "include"` is an include too)."""
    (tmp_path / "elsewhere" / "deep" / "er").mkdir(parents=True)
    for line in (
        'include "{outside}/**/*.beancount"',
        'include "../../elsewhere/**/*.beancount"',
        'include "{outside}/deep/*/x.beancount"',
        'include "../*/../../elsewhere/deep/*.beancount"',
        'option "include" "{outside}/**/*.beancount"',
        'option "include" "../../elsewhere/*/*.beancount"',
        'option "documents" "{outside}/deep"',
    ):
        root = outside_ledger(tier, tmp_path, line)
        assert d2_local.main([str(root)], child=d2_local.measure) == 2, line
        assert listed_outside == [], line
        shutil.rmtree(root.parent)


def test_the_scan_misses_no_spelling_of_a_path_that_could_leave(tier: Path, tmp_path: Path) -> None:
    """Must not pass: each spelling of an include or documents path that is absolute, starts with `~` or `$`, has a
    `..` segment or a backslash is refused, however the line is written."""
    out = (tmp_path / "elsewhere").as_posix()
    spellings = [
        f'\ufeffinclude "{out}/x.beancount"\n',
        f'2020-01-01 open Assets:A\r\ninclude "{out}/x.beancount"\r\n',
        f'2020-01-01 open Assets:A\rinclude "{out}/x.beancount"\r',
        f'\tinclude\t"{out}/x.beancount"\n',
        f'include "{out}\n/x.beancount"\n',
        'include "~/x.beancount"\n',
        'include "$HOME/x.beancount"\n',
        'include "x\\t/y.beancount"\n',  # Beancount reads `\t` as a tab (V2-5): any backslash is refused
        'include "x\\/../y.beancount"\n',
        'include "a/../b.beancount"\n',
        'include "a/.."\n',
        'include "C:/x.beancount"\n',
        f'option "documents" "{out}"\n',
        'option  "documents"  "../docs"\n',
        f'option "include" "{out}/*.beancount"\n',
        'option\t"include"\t"../*.beancount"\n',
        f'include "{out}/[ab]/x.beancount"\n',
        '; a comment\n; include "../x.beancount"\n',  # a false refusal, by design
    ]
    folder = tier / "s"
    folder.mkdir()
    for text in spellings:
        (folder / "main.beancount").write_text(text, encoding="utf-8")
        assert d2_local.refuse_tree(tier) == "reaches-out", repr(text)


@pytest.mark.parametrize(
    "value",
    ["sub/*.beancount", "**/*.beancount", "x.beancount", "sub/x.beancount", "./x.beancount", "a..b/x.beancount"],
)
def test_relative_paths_below_their_file_are_allowed(tier: Path, value: str) -> None:
    folder = tier / "a" / "b"
    (folder / "sub").mkdir(parents=True)
    root = folder / "main.beancount"
    root.write_text(f'include "{value}"\noption "documents" "sub"\n2020-01-01 open Assets:Cash USD\n', encoding="utf-8")
    assert d2_local.refuse_tree(tier) is None


def links(tier: Path, tmp_path: Path) -> dict[str, Callable[[], Path]]:
    """Each way a copy can hold a link (`cp -R` keeps links), as a function that builds the ledger: out of the tier
    (a plugin module, a plugin package, an include, an unused file) and inside it (a file, a folder: V2-4)."""
    outside = tmp_path / "elsewhere"
    outside.mkdir(exist_ok=True)
    (outside / "linked_s0158.py").write_text("__plugins__ = []\n", encoding="utf-8")
    (outside / "linkpkg_s0158").mkdir(exist_ok=True)
    (outside / "linkpkg_s0158" / "__init__.py").write_text("__plugins__ = []\n", encoding="utf-8")
    (outside / "other.beancount").write_text("2020-01-01 open Assets:Other USD\n", encoding="utf-8")
    (tier / "shared").mkdir(exist_ok=True)
    (tier / "shared" / "other.beancount").write_text("2020-01-01 open Assets:Other USD\n", encoding="utf-8")

    def ledger(head: str, link: str, target: Path) -> Path:
        root = private_ledger(tier)
        root.write_bytes(head.encode() + b"\n" + PRIVATE)
        (root.parent / link).symlink_to(target, target_is_directory=target.is_dir())
        return root

    return {
        "module": lambda: ledger(
            'option "insert_pythonpath" "TRUE"\nplugin "linked_s0158"', "linked_s0158.py", outside / "linked_s0158.py"
        ),
        "package": lambda: ledger(
            'option "insert_pythonpath" "TRUE"\nplugin "linkpkg_s0158"', "linkpkg_s0158", outside / "linkpkg_s0158"
        ),
        "include": lambda: ledger('include "other.beancount"', "other.beancount", outside / "other.beancount"),
        "unused": lambda: ledger("", "stray.txt", outside / "other.beancount"),
        "inside file": lambda: ledger(
            'include "other.beancount"', "other.beancount", tier / "shared" / "other.beancount"
        ),
        "inside folder": lambda: ledger('include "s/*.beancount"', "s", tier / "shared"),
    }


def refused_with_nothing_loaded(done: subprocess.CompletedProcess[str], *hidden: str) -> None:
    assert done.returncode == 2, done.stdout + done.stderr
    assert "L1 REFUSED" in done.stdout
    assert "nothing was loaded" in done.stdout
    assert "Traceback" not in done.stdout + done.stderr
    for text in hidden:
        assert text not in done.stdout + done.stderr, text


def test_any_link_refuses_every_ledger_before_any_load(tier: Path, tmp_path: Path) -> None:
    """S0158 V1-1, V2-4: any link in the tier, out of it or inside it, refuses the run (the plain-copy rule)."""
    for kind, build in links(tier, tmp_path).items():
        root = build()
        assert d2_local.refuse_tree(tier) == "link", kind
        refused_with_nothing_loaded(run(str(root), tier=tier), str(tier))
        shutil.rmtree(root.parent)


def test_a_link_in_a_folder_the_walk_cannot_list_is_never_reached(
    tier: Path, tmp_path: Path, listed_outside: list[str]
) -> None:
    """S0158 V2-1: a plugin package whose `__init__.py` links outside, in a folder that can be entered but not listed
    (mode 0311): the folder that cannot be listed refuses the run."""
    if sys.platform == "win32":
        raise pytest.skip.Exception(windows_pending.NO_UID)
    (tmp_path / "elsewhere" / "p_out.py").write_text("__plugins__ = []\n", encoding="utf-8")
    root = private_ledger(tier)
    root.write_bytes(b'option "insert_pythonpath" "TRUE"\nplugin "pk_s0158"\n' + PRIVATE)
    package = root.parent / "pk_s0158"
    package.mkdir()
    (package / "__init__.py").symlink_to(tmp_path / "elsewhere" / "p_out.py")
    package.chmod(0o311)
    try:
        assert d2_local.refuse_tree(tier) == "unreadable"
        refused_with_nothing_loaded(run(str(root), tier=tier), str(tier))
        assert d2_local.main([str(root)], child=d2_local.measure) == 2
        assert listed_outside == []
    finally:
        package.chmod(0o755)


def test_an_unreadable_file_refuses_the_run_without_naming_it(tier: Path) -> None:
    """S0158 V2-2: a file the check cannot read refuses the run with a fixed reason: no traceback, no name."""
    if sys.platform == "win32":
        raise pytest.skip.Exception(windows_pending.NO_UID)
    root = private_ledger(tier)
    secret = tier / "Quixotic-Payee-statement.pdf"
    secret.write_bytes(b"x")
    secret.chmod(0o000)
    try:
        refused_with_nothing_loaded(run(str(root), tier=tier), "Quixotic", "statement", str(tier))
    finally:
        secret.chmod(0o644)


def test_odd_bytes_in_an_include_never_crash_the_check(tier: Path) -> None:
    """S0158 V2-2: a NUL or undecodable bytes in an include value are judged as bytes, never raised on."""
    root = private_ledger(tier)
    (tier / "note.txt").write_bytes(b'include "a\0b/x"\ninclude "\xff\xfe/../x"\n')
    done = run(str(root), tier=tier)
    refused_with_nothing_loaded(done, str(tier))  # the `..` refuses; the NUL alone would not


def test_a_second_hard_link_refuses_the_run(tier: Path, tmp_path: Path) -> None:
    """S0158 V2-6: a plugin that is a hard link to a file outside the tier names no path that leaves it; a regular file
    with a second link refuses the run."""
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "ph.py").write_text("__plugins__ = []\n", encoding="utf-8")
    root = private_ledger(tier)
    root.write_bytes(b'option "insert_pythonpath" "TRUE"\nplugin "ph_s0158"\n' + PRIVATE)
    os.link(tmp_path / "elsewhere" / "ph.py", root.parent / "ph_s0158.py")
    assert d2_local.refuse_tree(tier) == "hardlink"
    refused_with_nothing_loaded(run(str(root), tier=tier), str(tier))


def test_something_neither_a_file_nor_a_folder_refuses_the_run(tier: Path) -> None:
    if sys.platform == "win32":
        raise pytest.skip.Exception(windows_pending.NO_FIFO)
    root = private_ledger(tier)
    os.mkfifo(root.parent / "pipe")
    assert d2_local.refuse_tree(tier) == "special"
    refused_with_nothing_loaded(run(str(root), tier=tier), str(tier))


def test_a_failure_of_one_ledger_is_an_error_with_a_fixed_reason(
    tier: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(root: Path) -> d2_local.Report:
        raise ValueError("Zebra")

    root = private_ledger(tier)
    assert d2_local.main([str(root)], child=broken) == 1
    out = capsys.readouterr()
    assert "L1 ERROR" in out.out
    assert "Zebra" not in out.out + out.err


def test_any_failure_of_the_check_refuses_with_a_fixed_reason(tier: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object) -> None:
        raise ValueError("Zebra")

    monkeypatch.setattr(d2_local, "_first_problem", broken)
    assert d2_local.refuse_tree(tier) == "unreadable"


WIDEN = b"""__plugins__ = ["widen"]


def widen(entries, options_map, config):
    options_map["include"].append(config)
    return entries, []
"""


@pytest.mark.usefixtures("no_reads_outside")
def test_a_file_a_plugin_adds_to_the_includes_is_not_read_when_outside_the_tier(tier: Path, tmp_path: Path) -> None:
    """The candidates are the files the overlay's parse read; a name a plugin adds to `include` (here outside the
    folder) is never read."""
    root = outside_ledger(
        tier, tmp_path, 'option "insert_pythonpath" "TRUE"\nplugin "widen_s0158" "{outside}/other.beancount"'
    )
    (root.parent / "widen_s0158.py").write_bytes(WIDEN)
    report = d2_local.measure(root)
    assert report.verdict == "EQUAL", report  # the added name is no candidate (S0158 V1-3), so it is never read


def test_a_file_a_plugin_adds_to_the_includes_inside_the_tier_is_no_candidate(tier: Path, tmp_path: Path) -> None:
    """S0158 V1-3: Beancount and the overlay agree on a ledger whose plugin adds an existing file of the folder to
    `include`; the runner must not turn that name into a candidate the overlay never reaches (a false DIFFERENT)."""
    root = private_ledger(tier)
    other = root.parent / "unreached.beancount"
    other.write_text("2020-01-01 open Assets:Other USD\n", encoding="utf-8")
    root.write_bytes(
        f'option "insert_pythonpath" "TRUE"\nplugin "widen_s0158" "{other.as_posix()}"\n'.encode() + PRIVATE
    )
    (root.parent / "widen_s0158.py").write_bytes(WIDEN)
    done = run(str(root), tier=tier)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "L1 EQUAL" in done.stdout


STATEFUL = b"""import collections

E = collections.namedtuple("E", "source message entry")
N = [0]
__plugins__ = ["count"]


def count(entries, options_map):
    N[0] += 1
    return entries, [E({"filename": "x", "lineno": 1}, "e", None) for _ in range(N[0])]
"""


def test_another_spelling_of_the_tier_still_forgets_the_plugins_between_loads(tier: Path, tmp_path: Path) -> None:
    """S0158 V1-4: the tier named through a link, the root by its real path: a plugin that counts its runs must still
    be imported afresh for each load (each load sees one run), so the ledger is EQUAL."""
    root = private_ledger(tier)
    root.write_bytes(b'option "insert_pythonpath" "TRUE"\nplugin "stateful_s0158"\n' + PRIVATE)
    (root.parent / "stateful_s0158.py").write_bytes(STATEFUL)
    spelled = tmp_path / "tier-link"
    spelled.symlink_to(tier, target_is_directory=True)
    for given_tier, given_root in ((spelled, root), (tier, spelled / root.relative_to(tier))):
        done = run(str(given_root), tier=given_tier)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "L1 EQUAL" in done.stdout


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
