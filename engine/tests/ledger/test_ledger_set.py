"""What a load read, recorded by the load itself: its ledger set and fingerprint (ADR-0008 step 4, ADR-0015; S0051).

The fingerprint of a load describes the bytes that load parsed, the glob matches it followed, the paths it looked
for and did not find, and the documents roots it scanned (listed before the scan), so a later observation that
equals it means the next load would read the same inputs. Every overlay corpus case (`corpora/ledgers/write/`) is
loaded from a temp copy (INV-24) and observed again unchanged.
"""

from __future__ import annotations

import glob
import hashlib
import shutil
import tomllib
from os import path
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger.core.ledger_set import Absent, Content, Fingerprint
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.ledger.overlay import Loaded, load
from ledger_engine.ledger.policy import DESKTOP

pytestmark = pytest.mark.inv("INV-22")

CORPUS = Path(__file__).resolve().parents[3] / "corpora" / "ledgers" / "write"
CASES: dict[str, Any] = tomllib.loads((CORPUS / "cases.toml").read_text(encoding="utf-8"))
LOAD_CASES = sorted(k for k, v in CASES.items() if v["expect"] == "load")


def sha(data: bytes) -> Content:
    return Content(hashlib.sha256(data).hexdigest())


def loaded(root: Path, overlay: dict[str, bytes] | None = None) -> Loaded:
    result = load(str(root), overlay or {}, DESKTOP)
    assert isinstance(result, Loaded), result
    return result


def copy_case(name: str, tmp_path: Path) -> Path:
    dest = tmp_path / name
    shutil.copytree(CORPUS / name, dest)
    return dest / "main.beancount"


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_every_corpus_case_records_what_it_read_and_observes_unchanged(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Population: every `load` case of the write corpus (no parametrize, so no case can be switched off)."""
    assert len(LOAD_CASES) >= 15
    failures: list[str] = []
    for case in LOAD_CASES:
        root = copy_case(case, tmp_path_factory.mktemp(case))
        result = loaded(root)
        fp = result.fingerprint
        read = {p for p, state in fp.files if isinstance(state, Content)}
        if read != set(result.options["include"]):
            failures.append(f"{case}: read {sorted(read)} != include {result.options['include']}")
        for p, state in fp.files:
            if isinstance(state, Content) and state != sha(Path(p).read_bytes()):
                failures.append(f"{case}: {p} hash is not of its bytes")
        if fp.root != str(root):
            failures.append(f"{case}: root {fp.root}")
        if observe(fp.ledger_set) != fp:
            failures.append(f"{case}: an unchanged ledger set observes differently")
    assert failures == []


def test_a_glob_include_records_its_pattern_and_matches_in_the_loaders_order(tmp_path: Path) -> None:
    root = copy_case("includes", tmp_path)
    fp = loaded(root).fingerprint
    pattern = path.join(str(root.parent), "years/*.beancount")
    assert dict(fp.globs) == {pattern: tuple(glob.glob(pattern, recursive=True))}
    assert set(dict(fp.globs)[pattern]) == {
        str(root.parent / "years" / "2023.beancount"),
        str(root.parent / "years" / "2024.beancount"),
    }


def test_a_missing_plain_include_is_recorded_absent(tmp_path: Path) -> None:
    root = write(tmp_path / "main.beancount", 'include "later.beancount"\n')
    fp = loaded(root).fingerprint
    assert dict(fp.files)[str(tmp_path / "later.beancount")] == Absent()
    assert dict(fp.files)[str(root)] == sha(root.read_bytes())
    assert fp.globs == ()


def test_a_glob_that_matches_nothing_is_recorded_with_no_matches(tmp_path: Path) -> None:
    root = write(tmp_path / "main.beancount", 'include "years/*.beancount"\n')
    fp = loaded(root).fingerprint
    assert fp.globs == ((str(tmp_path / "years/*.beancount"), ()),)


def test_a_missing_root_is_recorded_absent(tmp_path: Path) -> None:
    root = tmp_path / "nothing.beancount"
    fp = loaded(root).fingerprint
    assert fp.files == ((str(root), Absent()),)


def test_documents_roots_are_resolved_as_beancount_resolves_them_and_listed(tmp_path: Path) -> None:
    root = copy_case("documents", tmp_path)
    fp = loaded(root).fingerprint
    docs = str(root.parent / "docs")
    assert fp.documents == (
        (
            docs,
            (
                path.join("Assets", "Bank", "Checking", "2024-01-05.statement.txt"),
                path.join("Assets", "Bank", "Checking", "2024-02-05.statement.txt"),
            ),
        ),
    )


def test_an_absent_documents_root_is_recorded_absent(tmp_path: Path) -> None:
    root = write(tmp_path / "book" / "main.beancount", 'option "documents" "../scans"\n')
    fp = loaded(root).fingerprint
    assert fp.documents == ((str(tmp_path / "scans"), None),)


def test_a_candidate_is_fingerprinted_by_the_candidate_bytes(tmp_path: Path) -> None:
    root = write(tmp_path / "main.beancount", 'include "new.beancount"\n')
    new = str(tmp_path / "new.beancount")
    candidate = b"2020-01-01 open Assets:Cash USD\n"
    fp = loaded(root, {new: candidate}).fingerprint
    assert dict(fp.files)[new] == sha(candidate)
    assert dict(observe(fp.ledger_set).files)[new] == Absent()  # not on disk yet: the disk differs
    Path(new).write_bytes(candidate)
    assert observe(fp.ledger_set) == fp  # after the save wrote the same bytes, the disk is what was loaded


def test_the_hash_is_of_the_bytes_parsed_even_when_the_file_changes_during_the_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An editor saves between the load's read and its parse: the fingerprint must describe what was parsed, so
    the next observation differs (else a stale load would pass the write gate, ADR-0013)."""
    old = b"2020-01-01 open Assets:Cash USD\n"
    new = b"2020-01-01 open Assets:Bank USD\n"
    root = tmp_path / "main.beancount"
    root.write_bytes(old)
    original = port.parse_string

    def editor_saves_first(data: bytes, report_filename: str | None = None) -> port.ParseResult:
        root.write_bytes(new)
        return original(data, report_filename)

    monkeypatch.setattr(port, "parse_string", editor_saves_first)
    result = loaded(root)
    assert [e.account for e in result.entries if isinstance(e, port.Open)] == ["Assets:Cash"]
    assert dict(result.fingerprint.files)[str(root)] == sha(old)
    monkeypatch.undo()
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_the_fingerprint_is_a_value() -> None:
    assert Fingerprint.of("/r/main.beancount", {}, {}, {}) == Fingerprint.of("/r/main.beancount", {}, {}, {})


def test_a_path_found_absent_and_then_read_in_one_load_stays_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load reported the include missing and then read the file: its result holds both, which the next load
    would not give, so the recorded state must make the next observation differ (the first state stays)."""
    root = write(tmp_path / "main.beancount", 'include "x.beancount"\ninclude "a.beancount"\n')
    write(tmp_path / "a.beancount", 'include "x.beancount"\n')
    x = tmp_path / "x.beancount"
    original = port.parse_string

    def x_appears_before_a_is_parsed(data: bytes, report_filename: str | None = None) -> port.ParseResult:
        if report_filename == str(tmp_path / "a.beancount"):
            x.write_text("2020-01-01 open Assets:Cash USD\n", encoding="utf-8")
        return original(data, report_filename)

    monkeypatch.setattr(port, "parse_string", x_appears_before_a_is_parsed)
    result = loaded(root)
    monkeypatch.undo()
    assert str(x) in result.options["include"]
    assert dict(result.fingerprint.files)[str(x)] == Absent()
    assert observe(result.fingerprint.ledger_set) != result.fingerprint
