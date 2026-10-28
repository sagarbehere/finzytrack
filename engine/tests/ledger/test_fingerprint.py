"""The extended fingerprint: observed on disk, reduced to one digest (ADR-0008 step 4, ADR-0015, ADR-0064 spec §1
"Fingerprint"; S0051).

Two requirements: a change the next load would see changes the fingerprint (else a save lands over an outside edit,
or the mirror stays stale: INV-01, INV-05, INV-22), and an unchanged ledger set keeps it (else every open rebuilds and
every save is refused). Each change below is one the loader reads: a file's bytes, a path appearing, vanishing or
becoming a link to nothing, a glob's match list, a documents root's file names and whether each leads to a file, a
`document` directive's file appearing or vanishing. The digest's encoding is pinned by a golden and checked by
properties: it decodes back to the value it came from, so two different fingerprints never share a digest except by
a SHA-256 collision.
"""

from __future__ import annotations

import json
import os
import random
import shutil
from collections.abc import Callable
from os import path
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from ledger_engine.ledger.core.ledger_set import (
    Absent,
    BrokenLink,
    Content,
    FileState,
    Fingerprint,
    Unreadable,
    digest,
    encode,
)
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.ledger.overlay import Loaded, load
from ledger_engine.ledger.policy import DESKTOP

pytestmark = pytest.mark.inv("INV-22")

CORPUS = Path(__file__).resolve().parents[3] / "corpora" / "ledgers" / "write"


def book(tmp_path: Path) -> Path:
    """A ledger set with every kind of input: a plain include, a glob, an absent include, a documents root (with a
    document that is a link to nothing yet), and two `document` directives (one file present, one missing)."""
    for case in ("includes", "documents"):
        shutil.copytree(CORPUS / case, tmp_path / case)
    (tmp_path / "scans").mkdir()
    (tmp_path / "scans" / "receipt.pdf").write_bytes(b"%PDF")
    checking = tmp_path / "documents" / "docs" / "Assets" / "Bank" / "Checking"
    (checking / "2024-03-05.statement.pdf").symlink_to(tmp_path / "scans" / "statement.pdf")
    root = tmp_path / "main.beancount"
    root.write_text(
        'option "documents" "documents/docs"\ninclude "includes/main.beancount"\ninclude "later.beancount"\n'
        '2024-01-01 document Assets:Cash "scans/receipt.pdf"\n2024-01-02 document Assets:Cash "scans/missing.pdf"\n',
        encoding="utf-8",
    )
    return root


def loaded(root: Path) -> Loaded:
    result = load(str(root), {}, DESKTOP)
    assert isinstance(result, Loaded), result
    return result


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def fingerprint_of(root: Path) -> Fingerprint:
    return loaded(root).fingerprint


# ---- what changes it, and what does not --------------------------------------------------------------------------


def edit_a_byte(tmp_path: Path) -> None:
    p = tmp_path / "includes" / "years" / "2024.beancount"
    data = bytearray(p.read_bytes())
    data[-2] = ord("X") if data[-2] != ord("X") else ord("Y")
    p.write_bytes(bytes(data))


def edit_keeping_size_and_mtime(tmp_path: Path) -> None:
    """Same size, mtime put back: a stat-based fingerprint misses this (ADR-0015's `(path, mtime, size)`)."""
    p = tmp_path / "includes" / "accounts.beancount"
    st_before = p.stat()
    data = p.read_bytes()
    p.write_bytes(data.replace(b"USD", b"EUR", 1))
    assert p.stat().st_size == st_before.st_size
    os.utime(p, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))


def create_the_absent_include(tmp_path: Path) -> None:
    (tmp_path / "later.beancount").write_text("2020-01-01 open Assets:New USD\n", encoding="utf-8")


def delete_a_loaded_file(tmp_path: Path) -> None:
    (tmp_path / "includes" / "years" / "2023.beancount").unlink()


def add_a_glob_match(tmp_path: Path) -> None:
    (tmp_path / "includes" / "years" / "2025.beancount").write_text("", encoding="utf-8")


def rename_a_glob_match(tmp_path: Path) -> None:
    years = tmp_path / "includes" / "years"
    (years / "2024.beancount").rename(years / "2024b.beancount")


def add_a_document(tmp_path: Path) -> None:
    (tmp_path / "documents" / "docs" / "Assets" / "Cash").mkdir(parents=True)
    (tmp_path / "documents" / "docs" / "Assets" / "Cash" / "2024-03-01.receipt.pdf").write_bytes(b"%PDF")


def rename_a_document(tmp_path: Path) -> None:
    folder = tmp_path / "documents" / "docs" / "Assets" / "Bank" / "Checking"
    (folder / "2024-02-05.statement.txt").rename(folder / "2024-02-06.statement.txt")


def remove_the_documents_root(tmp_path: Path) -> None:
    shutil.rmtree(tmp_path / "documents" / "docs")


def make_a_file_unreadable(tmp_path: Path) -> None:
    (tmp_path / "includes" / "accounts.beancount").chmod(0)


def replace_a_file_by_a_link_to_other_bytes(tmp_path: Path) -> None:
    p = tmp_path / "includes" / "accounts.beancount"
    other = tmp_path / "elsewhere.beancount"
    other.write_text("2020-01-01 open Assets:Elsewhere USD\n", encoding="utf-8")
    p.unlink()
    p.symlink_to(other)


def make_the_absent_include_a_link_to_nothing(tmp_path: Path) -> None:
    """The loader's glob sees the link (`lexists`), then the file is missing: another error than "no match"."""
    (tmp_path / "later.beancount").symlink_to(tmp_path / "nowhere.beancount")


def remove_a_document_directives_file(tmp_path: Path) -> None:
    (tmp_path / "scans" / "receipt.pdf").unlink()


def create_a_document_directives_missing_file(tmp_path: Path) -> None:
    (tmp_path / "scans" / "missing.pdf").write_bytes(b"%PDF")


def give_a_documents_link_its_target(tmp_path: Path) -> None:
    (tmp_path / "scans" / "statement.pdf").write_bytes(b"%PDF")


# Changes to inputs that S0051's first fingerprint missed (verifier round 1, F1 and its sweep): each changes the
# next load's errors, which the test below checks, so each is a read the fingerprint must record.
DOCUMENT_AND_LINK_CHANGES: dict[str, Callable[[Path], None]] = {
    "an absent include becomes a link to nothing": make_the_absent_include_a_link_to_nothing,
    "a document directive's file removed": remove_a_document_directives_file,
    "a document directive's missing file appears": create_a_document_directives_missing_file,
    "a documents file that is a link gets its target": give_a_documents_link_its_target,
}

CHANGES: dict[str, Callable[[Path], None]] = {
    **DOCUMENT_AND_LINK_CHANGES,
    "a byte of an included file": edit_a_byte,
    "an edit that keeps size and mtime": edit_keeping_size_and_mtime,
    "an absent include appears": create_the_absent_include,
    "a loaded file vanishes": delete_a_loaded_file,
    "a new glob match": add_a_glob_match,
    "a glob match renamed": rename_a_glob_match,
    "a new document": add_a_document,
    "a document renamed": rename_a_document,
    "the documents root removed": remove_the_documents_root,
    "a file made unreadable": make_a_file_unreadable,
    "a file replaced by a link to other bytes": replace_a_file_by_a_link_to_other_bytes,
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_change_the_next_load_would_see_changes_the_fingerprint(change: str, tmp_path: Path) -> None:
    if change == "a file made unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a file whatever its mode")
    root = book(tmp_path)
    before = fingerprint_of(root)
    assert observe(before.ledger_set) == before
    CHANGES[change](tmp_path)
    after = observe(before.ledger_set)
    assert after != before
    assert digest(after) != digest(before)


@pytest.mark.parametrize("change", sorted(DOCUMENT_AND_LINK_CHANGES))
def test_each_of_these_changes_changes_the_next_load(change: str, tmp_path: Path) -> None:
    """The changes above are not superfluous: the next load reports other errors (Beancount 3.2.0's own messages)."""
    root = book(tmp_path)
    before = sorted(e.message for e in loaded(root).errors)
    DOCUMENT_AND_LINK_CHANGES[change](tmp_path)
    assert sorted(e.message for e in loaded(root).errors) != before


def test_the_documents_root_appearing_changes_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "main.beancount"
    root.write_text('option "documents" "docs"\n', encoding="utf-8")
    before = fingerprint_of(root)
    assert before.documents == ((str(tmp_path / "docs"), None),)
    (tmp_path / "docs").mkdir()
    assert digest(observe(before.ledger_set)) != digest(before)


def test_an_empty_documents_root_is_not_an_absent_one(tmp_path: Path) -> None:
    """Beancount reports an absent root as an error and an empty one as nothing: two different loads."""
    root = tmp_path / "main.beancount"
    root.write_text('option "documents" "docs"\n', encoding="utf-8")
    (tmp_path / "docs").mkdir()
    before = fingerprint_of(root)
    (tmp_path / "docs").rmdir()
    assert digest(observe(before.ledger_set)) != digest(before)


def test_an_absent_file_that_appears_unreadable_changes_the_fingerprint(tmp_path: Path) -> None:
    """Absent and unreadable are two states: the next load reports a missing file in one and fails in the other."""
    if os.geteuid() == 0:
        pytest.skip("root reads a file whatever its mode")
    root = book(tmp_path)
    before = fingerprint_of(root)
    create_the_absent_include(tmp_path)
    (tmp_path / "later.beancount").chmod(0)
    after = observe(before.ledger_set)
    assert dict(after.files)[str(tmp_path / "later.beancount")] == Unreadable("EACCES")
    assert digest(after) != digest(before)


def test_what_the_load_cannot_see_keeps_the_fingerprint(tmp_path: Path) -> None:
    """A document's content (Beancount reads names and asks whether they exist, never reads them), a file beside the
    ledger that nothing includes: the next load reads the same inputs, so the mirror stays fresh and a save is not
    refused. mtime alone also keeps it, though the load does read it: `options["input_hash"]` is Beancount's cache key
    over each file's mtime and size, and no entry, error or other option depends on it (S0051 journal, F6)."""
    root = book(tmp_path)
    before = loaded(root)
    os.utime(tmp_path / "includes" / "accounts.beancount", (1, 1))
    statement = tmp_path / "documents" / "docs" / "Assets" / "Bank" / "Checking" / "2024-01-05.statement.txt"
    statement.write_bytes(statement.read_bytes() + b"more")
    (tmp_path / "scans" / "receipt.pdf").write_bytes(b"%PDF-1.7 other bytes")
    (tmp_path / "includes" / "years" / "notes.txt").write_text("not a ledger", encoding="utf-8")
    (tmp_path / "includes" / "unrelated.beancount").write_text("", encoding="utf-8")
    assert observe(before.fingerprint.ledger_set) == before.fingerprint
    after = loaded(root)
    assert after.options["input_hash"] != before.options["input_hash"]  # the one thing the mtime changes
    assert after.entries == before.entries
    assert [e.message for e in after.errors] == [e.message for e in before.errors]
    assert options_but_input_hash(after) == options_but_input_hash(before)


def options_but_input_hash(result: Loaded) -> dict[str, object]:
    """The options a load gives, `input_hash` left out (`dcontext` compared by its rendering: it has no `==`)."""
    return {k: str(v) if k == "dcontext" else v for k, v in result.options.items() if k != "input_hash"}


def test_a_document_added_under_a_linked_account_folder_changes_it(tmp_path: Path) -> None:
    """Beancount's `account.walk` follows links to folders (`followlinks=True`); the listing must too."""
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "docs" / "Assets").mkdir(parents=True)
    (tmp_path / "docs" / "Assets" / "Cash").symlink_to(target)
    root = tmp_path / "main.beancount"
    root.write_text('option "documents" "docs"\n2020-01-01 open Assets:Cash USD\n', encoding="utf-8")
    before = loaded(root)
    (target / "2024-01-01.receipt.pdf").write_bytes(b"%PDF")
    assert len(loaded(root).entries) == len(before.entries) + 1  # the next load sees it
    assert digest(observe(before.fingerprint.ledger_set)) != digest(before.fingerprint)


def test_a_recursive_glob_include_is_followed_and_observed(tmp_path: Path) -> None:
    """`**` is recursive in Beancount's loader (`glob.glob(..., recursive=True)`): it matches files at any depth,
    none included; without `recursive` it is one `*`, a single folder level. The load and the observation both."""
    deep = write(tmp_path / "y" / "2024" / "q1" / "a.beancount", "2020-01-01 open Assets:A USD\n")
    top = write(tmp_path / "y" / "b.beancount", "2020-01-01 open Assets:B USD\n")
    root = write(tmp_path / "main.beancount", 'include "y/**/*.beancount"\n')
    result = loaded(root)
    assert {str(deep), str(top)} <= set(result.options["include"])
    fp = result.fingerprint
    assert observe(fp.ledger_set) == fp
    write(tmp_path / "y" / "2024" / "q1" / "c.beancount", "")
    assert observe(fp.ledger_set) != fp


def test_an_unchanged_set_with_links_to_nothing_observes_unchanged(tmp_path: Path) -> None:
    """The loader asks `os.path.exists`, which is false for a link to nothing; a check that asked `lexists` and then
    failed to read would see a change every time: every open would rebuild and every save be refused."""
    (tmp_path / "x.beancount").symlink_to(tmp_path / "nowhere")
    (tmp_path / "docs" / "Assets" / "Cash").mkdir(parents=True)
    (tmp_path / "docs" / "Assets" / "Cash" / "2024-01-01.r.pdf").symlink_to(tmp_path / "nowhere.pdf")
    root = tmp_path / "main.beancount"
    root.write_text(
        'option "documents" "docs"\ninclude "x.beancount"\ninclude "y/*.beancount"\n'
        '2020-01-01 open Assets:Cash USD\n2024-01-02 document Assets:Cash "gone.pdf"\n',
        encoding="utf-8",
    )
    (tmp_path / "y").mkdir()
    (tmp_path / "y" / "z.beancount").symlink_to(tmp_path / "nowhere")
    (tmp_path / "gone.pdf").symlink_to(tmp_path / "nowhere.pdf")
    result = loaded(root)
    fp = result.fingerprint
    assert dict(fp.files)[str(tmp_path / "x.beancount")] == BrokenLink()
    assert dict(fp.files)[str(tmp_path / "y" / "z.beancount")] == BrokenLink()
    assert dict(fp.document_files)[str(tmp_path / "gone.pdf")] is False  # Beancount reports it missing
    assert (
        dict(dict(fp.documents)[str(tmp_path / "docs")] or {})[path.join("Assets", "Cash", "2024-01-01.r.pdf")] is False
    )
    assert sorted(e.message for e in result.errors if e.message.startswith("File does not exist")) == [
        f'File does not exist: "{tmp_path / "docs" / "Assets" / "Cash" / "2024-01-01.r.pdf"}"',
        f'File does not exist: "{tmp_path / "gone.pdf"}"',
    ]
    assert observe(fp.ledger_set) == fp


def test_observing_twice_gives_one_digest(tmp_path: Path) -> None:
    root = book(tmp_path)
    ledger_set = fingerprint_of(root).ledger_set
    assert digest(observe(ledger_set)) == digest(observe(ledger_set))


def test_the_root_path_is_part_of_the_fingerprint(tmp_path: Path) -> None:
    """ADR-0015: the root path; the same bytes under another root are another ledger set."""
    a = tmp_path / "a" / "main.beancount"
    b = tmp_path / "b" / "main.beancount"
    for p in (a, b):
        p.parent.mkdir()
        p.write_text("2020-01-01 open Assets:Cash USD\n", encoding="utf-8")
    assert digest(fingerprint_of(a)) != digest(fingerprint_of(b))


# ---- the value and its encoding ----------------------------------------------------------------------------------


GOLDEN = Fingerprint.of(
    "/books/main.beancount",
    {
        "/books/main.beancount": Content("abababababababababababababababababababababababababababababababab"),
        "/books/later.beancount": Absent(),
        "/books/linked.beancount": BrokenLink(),
        "/books/locked.beancount": Unreadable("EACCES"),
    },
    {"/books/years/*.beancount": ("/books/years/2024.beancount", "/books/years/2023.beancount")},
    {
        "/books/docs": {"Assets/Cash/2024-01-06.link.pdf": False, "Assets/Cash/2024-01-05.receipt.pdf": True},
        "/books/gone": None,
    },
    {"/books/scans/b.pdf": False, "/books/scans/a.pdf": True},
)
GOLDEN_ENCODING = (
    b'{"document_files":[["/books/scans/a.pdf",true],["/books/scans/b.pdf",false]],'
    b'"documents":[["/books/docs",[["Assets/Cash/2024-01-05.receipt.pdf",true],'
    b'["Assets/Cash/2024-01-06.link.pdf",false]]],["/books/gone",null]],'
    b'"files":[["/books/later.beancount",["absent"]],["/books/linked.beancount",["broken-link"]],'
    b'["/books/locked.beancount",["unreadable","EACCES"]],'
    b'["/books/main.beancount",["sha256","abababababababababababababababababababababababababababababababab"]]],'
    b'"globs":[["/books/years/*.beancount",["/books/years/2024.beancount","/books/years/2023.beancount"]]],'
    b'"root":"/books/main.beancount","v":1}'
)
GOLDEN_DIGEST = "sha256:7b1f81a2a4bc2aabb649b7e99b019cdc1d176089f2dd4cb9610994ca36249823"


def test_the_encoding_is_pinned() -> None:
    """A change here changes every stored digest: the next open of every workspace rebuilds its mirror (a cache,
    ADR-0015). Bump `v` with any change of meaning."""
    assert encode(GOLDEN) == GOLDEN_ENCODING


def test_the_digest_is_the_sha256_of_the_encoding() -> None:
    assert digest(GOLDEN) == GOLDEN_DIGEST


def test_a_fingerprint_is_built_only_in_canonical_order() -> None:
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (("/r/b", Absent()), ("/r/a", Absent())), (), (), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (("/r/a", Absent()), ("/r/a", Absent())), (), (), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (), (("/r/*", ()), ("/r/*", ())), (), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (), (), (("/r/d", (("b", True), ("a", True))),), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (), (), (("/r/d", (("a", True), ("a", False))),), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (), (), (), (("/r/b.pdf", True), ("/r/a.pdf", True)))


def test_a_content_state_is_a_sha256() -> None:
    with pytest.raises(ValueError, match="not canonical: sha256"):
        Content("not-a-hash")
    with pytest.raises(ValueError, match="not canonical: sha256"):
        Content("ABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABAB")


# Properties. Paths include quotes, backslashes, NUL and non-ASCII (`st.text`), and about half hold a lone surrogate:
# the form Python gives an undecodable file name on Linux (`os.fsdecode`), which `st.text` never generates.
LONE_SURROGATE = "\udcff"


def _joined(a: str, s: str, b: str) -> str:
    return a + s + b


paths: st.SearchStrategy[str] = st.text(min_size=1, max_size=12) | st.builds(
    _joined, st.text(max_size=5), st.characters(codec=None, categories=["Cs"]), st.text(max_size=5)
)
hexes = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
states: st.SearchStrategy[FileState] = st.one_of(
    hexes.map(Content),
    st.just(Absent()),
    st.just(BrokenLink()),
    st.sampled_from(["EACCES", "EIO", "ELOOP"]).map(Unreadable),
)
fingerprints = st.builds(
    Fingerprint.of,
    paths,
    st.dictionaries(paths, states, max_size=4),
    st.dictionaries(paths, st.lists(paths, max_size=3).map(tuple), max_size=3),
    st.dictionaries(paths, st.one_of(st.none(), st.dictionaries(paths, st.booleans(), max_size=3)), max_size=3),
    st.dictionaries(paths, st.booleans(), max_size=3),
)
WITH_SURROGATES = Fingerprint.of(
    "/b" + LONE_SURROGATE,
    {"/b/" + LONE_SURROGATE: Absent()},
    {"/b/*": ("/b/\ud800x",)},
    {"/b/d": {"\udfff": True}},
    {"/b/\udc80.pdf": False},
)


def decode(data: bytes) -> Fingerprint:
    """The inverse of `encode`, here only: if every encoding decodes back to its value, no two values share one."""
    raw = json.loads(data)
    assert set(raw) == {"v", "root", "files", "globs", "documents", "document_files"}
    assert raw["v"] == 1

    def state(s: list[str]) -> FileState:
        match s:
            case ["sha256", h]:
                return Content(h)
            case ["absent"]:
                return Absent()
            case ["broken-link"]:
                return BrokenLink()
            case ["unreadable", e]:
                return Unreadable(e)
            case _:
                raise AssertionError(s)

    return Fingerprint(
        raw["root"],
        tuple((p, state(s)) for p, s in raw["files"]),
        tuple((p, tuple(m)) for p, m in raw["globs"]),
        tuple((p, None if m is None else tuple((n, bool(e)) for n, e in m)) for p, m in raw["documents"]),
        tuple((p, bool(e)) for p, e in raw["document_files"]),
    )


@given(fingerprints)
@example(WITH_SURROGATES)
def test_every_encoding_decodes_back_to_its_fingerprint(fp: Fingerprint) -> None:
    assert decode(encode(fp)) == fp


def test_the_surrogate_example_holds_lone_surrogates_in_every_kind_of_key() -> None:
    """So the undecodable-name path runs on every run, whatever Hypothesis draws: each surrogate is kept as an
    ASCII escape (`ensure_ascii`), never encoded (which would raise)."""
    encoded = encode(WITH_SURROGATES)
    for escape in (b"\\udcff", b"\\ud800", b"\\udfff", b"\\udc80"):
        assert escape in encoded


@given(fingerprints, st.randoms())
def test_the_order_of_the_given_paths_does_not_matter(fp: Fingerprint, rnd: random.Random) -> None:
    files, globs, documents, document_files = (
        list(fp.files),
        list(fp.globs),
        list(fp.documents),
        list(fp.document_files),
    )
    for items in (files, globs, documents, document_files):
        rnd.shuffle(items)
    again = Fingerprint.of(
        fp.root,
        dict(files),
        dict(globs),
        {k: None if v is None else dict(rnd.sample(v, len(v))) for k, v in documents},
        dict(document_files),
    )
    assert again == fp
    assert digest(again) == digest(fp)


@given(fingerprints, paths, paths)
def test_a_glob_matches_order_is_kept(fp: Fingerprint, a: str, b: str) -> None:
    """The loader parses glob matches in the order the glob returns them; that order can change entry order."""
    if a == b:
        return
    documents = {k: None if v is None else dict(v) for k, v in fp.documents}
    one = Fingerprint.of(fp.root, dict(fp.files), {"/g/*": (a, b)}, documents, dict(fp.document_files))
    two = Fingerprint.of(fp.root, dict(fp.files), {"/g/*": (b, a)}, documents, dict(fp.document_files))
    assert digest(one) != digest(two)


@given(fingerprints)
def test_the_ledger_set_is_the_fingerprints_keys(fp: Fingerprint) -> None:
    ls = fp.ledger_set
    assert ls.root == fp.root
    assert ls.files == tuple(p for p, _ in fp.files)
    assert ls.globs == tuple(p for p, _ in fp.globs)
    assert ls.documents == tuple(p for p, _ in fp.documents)
    assert ls.document_files == tuple(p for p, _ in fp.document_files)
