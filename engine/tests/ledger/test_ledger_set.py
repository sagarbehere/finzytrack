"""What a load read, recorded by the load itself: its ledger set and fingerprint (ADR-0008 step 4, ADR-0015; S0051).

The fingerprint of a load describes the bytes that load parsed, the glob matches it followed, the paths it looked
for and did not find, each `include`'s glob answer under the string the glob was asked (a plain path's too), the
documents roots it scanned (listed before the scan, with whether each name leads to a file), and whether the file of
each `document` entry Beancount checks exists (a directive's asked before Beancount asks, a plugin's taken from
Beancount's own check where it is known), so a later observation that equals it means the next load would read the
same inputs. Every overlay corpus case (`corpora/ledgers/write/`) is loaded from a temp copy (INV-24) and observed
again unchanged.
"""

from __future__ import annotations

import glob
import hashlib
import shutil
import tomllib
from collections.abc import Callable
from os import path
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint as fp_module
from ledger_engine.ledger.core.ledger_set import (
    Absent,
    Content,
    Fingerprint,
    UndecodableError,
    as_before,
    decode,
    encode,
)
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
        listed = {
            path.join(d, name): exists for d, names in fp.documents if names is not None for name, exists in names
        }
        for entry in result.entries:
            if isinstance(entry, port.Document):
                recorded = dict(fp.document_files).get(entry.filename, listed.get(entry.filename))
                if recorded is None:
                    failures.append(f"{case}: document {entry.filename} is not recorded")
                elif recorded != path.exists(entry.filename):
                    failures.append(f"{case}: document {entry.filename} recorded {recorded}")
    assert failures == []


def test_a_glob_include_records_its_pattern_and_matches_in_the_loaders_order(tmp_path: Path) -> None:
    root = copy_case("includes", tmp_path)
    fp = loaded(root).fingerprint
    pattern = path.join(str(root.parent), "years/*.beancount")
    assert dict(fp.globs)[pattern] == tuple(glob.glob(pattern, recursive=True))
    assert set(dict(fp.globs)[pattern]) == {
        str(root.parent / "years" / "2023.beancount"),
        str(root.parent / "years" / "2024.beancount"),
    }


def test_a_missing_plain_include_is_recorded_as_its_globs_empty_answer(tmp_path: Path) -> None:
    """Beancount's loader globs a plain path too (`glob.glob` asks `lexists` of it): no match, so it never asks
    `exists` and never opens it; the record is the glob's answer, under the string it was asked."""
    root = write(tmp_path / "main.beancount", 'include "later.beancount"\n')
    fp = loaded(root).fingerprint
    assert fp.files == ((str(root), sha(root.read_bytes())),)
    assert fp.globs == ((str(tmp_path / "later.beancount"), ()),)


def test_a_glob_that_matches_nothing_is_recorded_with_no_matches(tmp_path: Path) -> None:
    root = write(tmp_path / "main.beancount", 'include "years/*.beancount"\n')
    fp = loaded(root).fingerprint
    assert fp.globs == ((str(tmp_path / "years/*.beancount"), ()),)


def test_a_missing_root_is_recorded_absent(tmp_path: Path) -> None:
    root = tmp_path / "nothing.beancount"
    fp = loaded(root).fingerprint
    assert fp.files == ((str(root), Absent()),)


def test_a_plain_include_that_is_a_link_to_nothing_is_recorded_as_the_glob_and_the_loader_saw_it(
    tmp_path: Path,
) -> None:
    """The loader's glob finds the link (`lexists`) and the loader then finds no file (`exists`): "does not exist".
    For an absent path the glob finds nothing: "does not match". Two errors, told apart by the glob's answer."""
    root = write(tmp_path / "main.beancount", 'include "x.beancount"\ninclude "y.beancount"\n')
    (tmp_path / "x.beancount").symlink_to(tmp_path / "nowhere.beancount")
    result = loaded(root)
    x, y = str(tmp_path / "x.beancount"), str(tmp_path / "y.beancount")
    assert dict(result.fingerprint.globs) == {x: (x,), y: ()}
    assert dict(result.fingerprint.files)[x] == Absent()
    assert y not in dict(result.fingerprint.files)
    assert sorted(e.message for e in result.errors) == [
        f'File "{tmp_path / "x.beancount"}" does not exist',
        'File glob "y.beancount" does not match any files',
    ]
    assert observe(result.fingerprint.ledger_set) == result.fingerprint


def test_a_link_removed_between_the_glob_and_the_read_is_recorded_as_the_glob_saw_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The glob found the name, so the load reports "does not exist", not "no match": the glob's answer is recorded as
    it was given (asked again, it would find nothing and equal the next observation, hiding that the next load
    differs)."""
    root = write(tmp_path / "main.beancount", 'include "x.beancount"\n')
    link = tmp_path / "x.beancount"
    link.symlink_to(tmp_path / "nowhere.beancount")
    original = fp_module.matches

    def link_removed_after_the_glob(pattern: str) -> list[str]:
        found = original(pattern)
        if pattern == str(link):
            link.unlink()
        return found

    monkeypatch.setattr(fp_module, "matches", link_removed_after_the_glob)
    result = loaded(root)
    monkeypatch.undo()
    assert [e.message for e in result.errors] == [f'File "{link}" does not exist']
    assert dict(result.fingerprint.globs)[str(link)] == (str(link),)
    assert dict(result.fingerprint.files)[str(link)] == Absent()
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_a_root_that_is_a_link_to_nothing_is_recorded_absent(tmp_path: Path) -> None:
    """The root is never globbed: the loader asks only `exists`, false for a link to nothing as for no file."""
    root = tmp_path / "main.beancount"
    root.symlink_to(tmp_path / "nowhere.beancount")
    fp = loaded(root).fingerprint
    assert fp.files == ((str(root), Absent()),)
    assert observe(fp.ledger_set) == fp


def test_document_directives_are_recorded_with_whether_their_file_exists(tmp_path: Path) -> None:
    """Every load asks `os.path.exists` of each `document` directive's file (`ops.documents.verify_document_files_
    exist`); a relative name is resolved from the folder of the file that holds the directive."""
    root = write(
        tmp_path / "main.beancount",
        '2020-01-01 open Assets:Cash USD\ninclude "sub/more.beancount"\n'
        '2024-01-01 document Assets:Cash "scans/a.pdf"\n',
    )
    write(tmp_path / "sub" / "more.beancount", '2024-01-02 document Assets:Cash "b.pdf"\n')
    write(tmp_path / "scans" / "a.pdf", "%PDF")
    result = loaded(root)
    assert result.fingerprint.document_files == (
        (str(tmp_path / "scans" / "a.pdf"), True),
        (str(tmp_path / "sub" / "b.pdf"), False),
    )
    assert [e.message for e in result.errors] == [f'File does not exist: "{tmp_path / "sub" / "b.pdf"}"']


def test_a_document_directives_file_is_recorded_before_beancount_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file vanishes after the load recorded it and before Beancount's check: the load's result reports it
    missing, so the record must make the next observation differ (the state seen first, as for every input)."""
    root = write(
        tmp_path / "main.beancount", '2020-01-01 open Assets:Cash USD\n2024-01-01 document Assets:Cash "a.pdf"\n'
    )
    pdf = write(tmp_path / "a.pdf", "%PDF")
    original = port.run_transformations

    def file_vanishes_first(
        entries: list[port.Directive], errors: list[port.BeancountError], options: port.OptionsMap
    ) -> tuple[list[port.Directive], list[port.BeancountError]]:
        pdf.unlink()
        return original(entries, errors, options)

    monkeypatch.setattr(port, "run_transformations", file_vanishes_first)
    result = loaded(root)
    monkeypatch.undo()
    assert [e.message for e in result.errors] == [f'File does not exist: "{pdf}"']
    assert result.fingerprint.document_files == ((str(pdf), True),)
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_a_document_a_plugin_makes_is_recorded(tmp_path: Path) -> None:
    """A plugin may add a `document` entry, and Beancount checks its file when its documents plugin runs after it
    (here `plugin_processing_mode "raw"` with `beancount.ops.documents` listed after the plugin): Beancount's error
    says the file is missing."""
    made = tmp_path / "made.pdf"
    write(
        tmp_path / "s0051_document_maker.py",
        "import datetime\nfrom beancount.core import data\n__plugins__ = ['make']\n"
        "def make(entries, options_map, config):\n"
        "    meta = data.new_metadata('<s0051_document_maker>', 0)\n"
        "    doc = data.Document(meta, datetime.date(2024, 1, 3), 'Assets:Cash', config, data.EMPTY_SET,"
        " data.EMPTY_SET)\n"
        "    return entries + [doc], []\n",
    )
    root = write(
        tmp_path / "main.beancount",
        'option "plugin_processing_mode" "raw"\noption "insert_pythonpath" "TRUE"\n'
        f'plugin "s0051_document_maker" "{made}"\nplugin "beancount.ops.documents"\n'
        "2020-01-01 open Assets:Cash USD\n",
    )
    result = loaded(root)
    assert [e.message for e in result.errors] == [f'File does not exist: "{made}"']
    assert result.fingerprint.document_files == ((str(made), False),)
    made.write_bytes(b"%PDF")
    assert observe(result.fingerprint.ledger_set) != result.fingerprint
    assert loaded(root).errors == []


def document_maker(tmp_path: Path, module: str, filename: str = "config") -> None:
    """A plugin module that adds one `document` entry for `Assets:Cash`; its file is the plugin's config by default."""
    write(
        tmp_path / f"{module}.py",
        "import datetime, pathlib\nfrom beancount.core import data\n__plugins__ = ['make']\n"
        "def make(entries, options_map, config=None):\n"
        f"    meta = data.new_metadata('<{module}>', 0)\n"
        f"    doc = data.Document(meta, datetime.date(2024, 1, 3), 'Assets:Cash', {filename}, data.EMPTY_SET,"
        " data.EMPTY_SET)\n"
        "    return entries + [doc], []\n",
    )


def after_the_plugins(monkeypatch: pytest.MonkeyPatch, action: Callable[[], object]) -> None:
    """Run `action` once Beancount's transformations (its documents check among them) are done, before the load
    goes on."""
    original = port.run_transformations

    def then_act(
        entries: list[port.Directive], errors: list[port.BeancountError], options: port.OptionsMap
    ) -> tuple[list[port.Directive], list[port.BeancountError]]:
        result = original(entries, errors, options)
        action()
        return result

    monkeypatch.setattr(port, "run_transformations", then_act)


def during_validation(monkeypatch: pytest.MonkeyPatch, action: Callable[[], object]) -> None:
    original = port.validate

    def act_first(entries: list[port.Directive], options: port.OptionsMap) -> list[Any]:
        action()
        return original(entries, options)

    monkeypatch.setattr(port, "validate", act_first)


RAW = 'option "plugin_processing_mode" "raw"\noption "insert_pythonpath" "TRUE"\n'


@pytest.mark.parametrize(
    "plugins", [["maker", "documents"], ["documents", "maker", "documents"]], ids=["after", "between-two"]
)
def test_a_plugin_document_beancount_checked_is_recorded_as_beancount_saw_it(
    plugins: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beancount found the file (no error), and it vanished right after: the record is Beancount's answer, not asked
    again, so the next observation differs (verifier round 2, F2)."""
    made = write(tmp_path / "made.pdf", "%PDF")
    document_maker(tmp_path, "s0051_r2_checked")
    lines = {"maker": f'plugin "s0051_r2_checked" "{made}"\n', "documents": 'plugin "beancount.ops.documents"\n'}
    root = write(
        tmp_path / "main.beancount", RAW + "".join(lines[p] for p in plugins) + "2020-01-01 open Assets:Cash USD\n"
    )
    after_the_plugins(monkeypatch, made.unlink)
    result = loaded(root)
    monkeypatch.undo()
    assert result.errors == []
    assert result.fingerprint.document_files == ((str(made), True),)
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_a_plugin_document_appearing_after_beancounts_check_is_recorded_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = tmp_path / "made.pdf"
    document_maker(tmp_path, "s0051_r2_appears")
    root = write(
        tmp_path / "main.beancount",
        RAW
        + f'plugin "s0051_r2_appears" "{made}"\nplugin "beancount.ops.documents"\n2020-01-01 open Assets:Cash USD\n',
    )
    after_the_plugins(monkeypatch, lambda: made.write_bytes(b"%PDF"))
    result = loaded(root)
    monkeypatch.undo()
    assert [e.message for e in result.errors] == [f'File does not exist: "{made}"']
    assert result.fingerprint.document_files == ((str(made), False),)
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_with_a_plugin_after_the_check_a_document_is_asked_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which plugin added it is not known, so the file is asked about: right after the plugins, so a delete while
    the load validates (its longest step after them) makes the next observation differ."""
    early = write(tmp_path / "early.pdf", "%PDF")
    document_maker(tmp_path, "s0051_r2_window_early")
    document_maker(tmp_path, "s0051_r2_window_late")
    root = write(
        tmp_path / "main.beancount",
        RAW + f'plugin "s0051_r2_window_early" "{early}"\nplugin "beancount.ops.documents"\n'
        f'plugin "s0051_r2_window_late" "{tmp_path / "late.pdf"}"\n2020-01-01 open Assets:Cash USD\n',
    )
    during_validation(monkeypatch, early.unlink)
    result = loaded(root)
    monkeypatch.undo()
    assert dict(result.fingerprint.document_files)[str(early)] is True
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_a_document_directive_keeps_the_answer_asked_before_the_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directive's file is asked before Beancount checks it; the plugin documents' record must not replace that
    answer with a later one (a delete after Beancount's check would then be hidden)."""
    pdf = write(tmp_path / "a.pdf", "%PDF")
    document_maker(tmp_path, "s0051_r2_directive_late")
    root = write(
        tmp_path / "main.beancount",
        RAW + 'plugin "beancount.ops.documents"\n'
        f'plugin "s0051_r2_directive_late" "{tmp_path / "late.pdf"}"\n'
        '2020-01-01 open Assets:Cash USD\n2024-01-01 document Assets:Cash "a.pdf"\n',
    )
    after_the_plugins(monkeypatch, pdf.unlink)
    result = loaded(root)
    monkeypatch.undo()
    assert result.errors == []
    assert dict(result.fingerprint.document_files)[str(pdf)] is True
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_a_plugin_document_beancount_never_checks_is_not_recorded(tmp_path: Path) -> None:
    """Default mode: Beancount's documents plugin runs before the user's plugins (`loader.PLUGINS_PRE`), so it never
    checks a document a plugin adds; the load does not depend on that file, and an unchanged set observes unchanged
    whether or not it exists."""
    made = tmp_path / "made.pdf"
    document_maker(tmp_path, "s0051_r2_unchecked")
    root = write(
        tmp_path / "main.beancount",
        f'option "insert_pythonpath" "TRUE"\nplugin "s0051_r2_unchecked" "{made}"\n2020-01-01 open Assets:Cash USD\n',
    )
    result = loaded(root)
    assert result.errors == []
    assert result.fingerprint.document_files == ()
    assert observe(result.fingerprint.ledger_set) == result.fingerprint


def test_a_documents_plugin_listed_with_a_config_checks_nothing(tmp_path: Path) -> None:
    """Its functions take no config, so Beancount reports the plugin failing and checks no file."""
    made = tmp_path / "made.pdf"
    document_maker(tmp_path, "s0051_r2_configured")
    root = write(
        tmp_path / "main.beancount",
        'option "plugin_processing_mode" "raw"\noption "insert_pythonpath" "TRUE"\n'
        f'plugin "s0051_r2_configured" "{made}"\nplugin "beancount.ops.documents" "x"\n'
        "2020-01-01 open Assets:Cash USD\n",
    )
    result = loaded(root)
    assert [e.message.split(":")[0] for e in result.errors] == ['Error applying plugin "beancount.ops.documents"'] * 2
    assert result.fingerprint.document_files == ()


def test_a_plugin_after_the_documents_plugin_leaves_its_document_unchecked_and_observed_unchanged(
    tmp_path: Path,
) -> None:
    """Which plugin added a document is not known: with a plugin after Beancount's check, a document with no error is
    recorded as it is now, so an unchanged set observes unchanged (the liveness side; journal, round 2)."""
    early, late = tmp_path / "early.pdf", tmp_path / "late.pdf"
    document_maker(tmp_path, "s0051_r2_early")
    document_maker(tmp_path, "s0051_r2_late")
    root = write(
        tmp_path / "main.beancount",
        'option "plugin_processing_mode" "raw"\noption "insert_pythonpath" "TRUE"\n'
        f'plugin "s0051_r2_early" "{early}"\nplugin "beancount.ops.documents"\nplugin "s0051_r2_late" "{late}"\n'
        "2020-01-01 open Assets:Cash USD\n",
    )
    result = loaded(root)
    assert [e.message for e in result.errors] == [f'File does not exist: "{early}"']
    assert result.fingerprint.document_files == ((str(early), False), (str(late), False))
    assert observe(result.fingerprint.ledger_set) == result.fingerprint
    early.write_bytes(b"%PDF")
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


def test_a_plugin_document_named_by_a_path_object_is_recorded_by_its_text_and_never_breaks_the_load(
    tmp_path: Path,
) -> None:
    """A plugin may give a `pathlib.Path`: Beancount's check takes it (`os.path.exists`) and its error names its text;
    unchecked, Beancount never looks at it. Neither load may raise (verifier round 2, F3)."""
    document_maker(tmp_path, "s0051_r2_pathobj", "pathlib.Path(config)")
    for mode, listed in (("default", ""), ("raw", 'plugin "beancount.ops.documents"\n')):
        made = tmp_path / f"{mode}.pdf"
        root = write(
            tmp_path / f"{mode}.beancount",
            f'option "plugin_processing_mode" "{mode}"\noption "insert_pythonpath" "TRUE"\n'
            f'plugin "s0051_r2_pathobj" "{made}"\n{listed}2020-01-01 open Assets:Cash USD\n'
            f'2024-01-01 document Assets:Cash "y.pdf"\n',
        )
        result = loaded(root)
        expected = [f'File does not exist: "{tmp_path / "y.pdf"}"'] + (
            [f'File does not exist: "{made}"'] if mode == "raw" else []
        )
        assert sorted(e.message for e in result.errors) == sorted(expected)
        assert dict(result.fingerprint.document_files).get(str(made)) == (False if mode == "raw" else None)
        assert observe(result.fingerprint.ledger_set) == result.fingerprint


def test_documents_roots_are_resolved_as_beancount_resolves_them_and_listed(tmp_path: Path) -> None:
    root = copy_case("documents", tmp_path)
    fp = loaded(root).fingerprint
    docs = str(root.parent / "docs")
    assert fp.documents == (
        (
            docs,
            (
                (path.join("Assets", "Bank", "Checking", "2024-01-05.statement.txt"), True),
                (path.join("Assets", "Bank", "Checking", "2024-02-05.statement.txt"), True),
            ),
        ),
    )


def test_a_documents_listing_records_whether_each_name_leads_to_a_file(tmp_path: Path) -> None:
    """Beancount makes a `document` entry for a dated name that is a link to nothing, then reports its file missing."""
    root = write(tmp_path / "main.beancount", 'option "documents" "docs"\n2020-01-01 open Assets:Cash USD\n')
    write(tmp_path / "docs" / "Assets" / "Cash" / "2024-01-01.a.pdf", "%PDF")
    link = tmp_path / "docs" / "Assets" / "Cash" / "2024-01-02.b.pdf"
    link.symlink_to(tmp_path / "nowhere.pdf")
    result = loaded(root)
    assert result.fingerprint.documents == (
        (
            str(tmp_path / "docs"),
            (
                (path.join("Assets", "Cash", "2024-01-01.a.pdf"), True),
                (path.join("Assets", "Cash", "2024-01-02.b.pdf"), False),
            ),
        ),
    )
    assert [e.message for e in result.errors] == [f'File does not exist: "{link}"']


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
    assert dict(fp.globs)[new] == (new,)  # as the glob will answer once the file is written
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
    assert Fingerprint.of("/r/main.beancount", {}, {}, {}, {}) == Fingerprint.of("/r/main.beancount", {}, {}, {}, {})


def test_a_path_found_absent_and_then_read_in_one_load_keeps_the_first_answer(
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
    assert dict(result.fingerprint.globs)[str(x)] == ()  # the first answer stays
    assert observe(result.fingerprint.ledger_set) != result.fingerprint


# -- the encoding's inverse and the re-check's expected disk (S0063) ---------------------------------------------------


@pytest.mark.parametrize("name", LOAD_CASES)
def test_every_corpus_fingerprint_decodes_to_itself(name: str, tmp_path: Path) -> None:
    fp = loaded(copy_case(name, tmp_path)).fingerprint
    assert decode(encode(fp)) == fp


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"[]",
        b'{"v":1}',
        b'{"document_files":[],"documents":[],"files":[],"globs":[],"root":"/r","v":2}',  # another version
        b'{"document_files":[],"documents":[],"files":[],"globs":[],"root":"/r","v":1,"x":0}',  # an extra key
        b'{"document_files":[],"documents":[],"files":[["/b",["absent"]],["/a",["absent"]]],"globs":[],"root":"/r","v":1}',
        b'{"document_files":[],"documents":[],"files":[["/a",["sha256","XX"]]],"globs":[],"root":"/r","v":1}',
        b'{"document_files":[],"documents":[],"files":[["/a",["gone"]]],"globs":[],"root":"/r","v":1}',
        b'{"documents":[],"document_files":[],"files":[],"globs":[],"root":"/r","v":1}',  # not canonical key order
        b'{"document_files":[["/d",1]],"documents":[],"files":[],"globs":[],"root":"/r","v":1}',
        b'{"document_files":[],"documents":[],"files":[],"globs":[["/g",[1]]],"root":"/r","v":1}',
        b'{"document_files":[],"documents":[],"files":[],"globs":[],"root":"/r","v":1} ',
    ],
)
def test_anything_encode_would_not_make_is_refused(data: bytes) -> None:
    with pytest.raises(UndecodableError):
        decode(data)


def test_as_before_puts_each_candidate_back_at_its_pre_state_and_keeps_every_other_read(tmp_path: Path) -> None:
    main = write(tmp_path / "main.bean", 'include "inc.bean"\ninclude "new.bean"\n')
    inc = write(tmp_path / "inc.bean", "")
    new = str(tmp_path / "new.bean")
    post = b'include "inc.bean"\ninclude "new.bean"\n; edited\n'
    fp = loaded(main, {str(main): post, new: b"; created\n"}).fingerprint
    states = dict(fp.files)
    assert states[str(main)] == sha(post)
    before = as_before(fp, {str(main): hashlib.sha256(main.read_bytes()).hexdigest(), new: None})
    assert dict(before.files) == {str(main): sha(main.read_bytes()), str(inc): sha(b""), new: Absent()}
    assert dict(before.globs)[new] == ()  # the disk's glob finds nothing there yet
    assert observe(before.ledger_set) == before  # the disk as the load read it
