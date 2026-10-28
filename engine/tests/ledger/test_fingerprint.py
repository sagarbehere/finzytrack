"""The extended fingerprint: observed on disk, reduced to one digest (ADR-0008 step 4, ADR-0015, ADR-0064 spec §1
"Fingerprint"; S0051).

Two requirements: a change the next load would see changes the fingerprint (else a save lands over an outside edit,
or the mirror stays stale: INV-01, INV-05, INV-22), and an unchanged ledger set keeps it (else every open rebuilds and
every save is refused). Each change below is one the loader reads: a file's bytes, a path appearing or vanishing, a
glob's match list, a documents root's file names. The digest's encoding is pinned by a golden and checked by
properties: it decodes back to the value it came from, so two different fingerprints never share a digest except by
a SHA-256 collision.
"""

from __future__ import annotations

import json
import os
import random
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_engine.ledger.core.ledger_set import (
    Absent,
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
    """A ledger set with every kind of input: a plain include, a glob, an absent include, a documents root."""
    for case in ("includes", "documents"):
        shutil.copytree(CORPUS / case, tmp_path / case)
    root = tmp_path / "main.beancount"
    root.write_text(
        'option "documents" "documents/docs"\ninclude "includes/main.beancount"\ninclude "later.beancount"\n',
        encoding="utf-8",
    )
    return root


def fingerprint_of(root: Path) -> Fingerprint:
    result = load(str(root), {}, DESKTOP)
    assert isinstance(result, Loaded), result
    return result.fingerprint


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


CHANGES: dict[str, Callable[[Path], None]] = {
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
    """mtime alone, a document's content, a file beside the ledger that nothing includes: the next load reads
    the same inputs, so the mirror stays fresh and a save is not refused."""
    root = book(tmp_path)
    before = fingerprint_of(root)
    os.utime(tmp_path / "includes" / "accounts.beancount", (1, 1))
    statement = tmp_path / "documents" / "docs" / "Assets" / "Bank" / "Checking" / "2024-01-05.statement.txt"
    statement.write_bytes(statement.read_bytes() + b"more")
    (tmp_path / "includes" / "years" / "notes.txt").write_text("not a ledger", encoding="utf-8")
    (tmp_path / "includes" / "unrelated.beancount").write_text("", encoding="utf-8")
    assert observe(before.ledger_set) == before


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
        "/books/locked.beancount": Unreadable("EACCES"),
    },
    {"/books/years/*.beancount": ("/books/years/2024.beancount", "/books/years/2023.beancount")},
    {"/books/docs": ("Assets/Cash/2024-01-05.receipt.pdf",), "/books/gone": None},
)
GOLDEN_ENCODING = (
    b'{"documents":[["/books/docs",["Assets/Cash/2024-01-05.receipt.pdf"]],["/books/gone",null]],'
    b'"files":[["/books/later.beancount",["absent"]],["/books/locked.beancount",["unreadable","EACCES"]],'
    b'["/books/main.beancount",["sha256","abababababababababababababababababababababababababababababababab"]]],'
    b'"globs":[["/books/years/*.beancount",["/books/years/2024.beancount","/books/years/2023.beancount"]]],'
    b'"root":"/books/main.beancount","v":1}'
)
GOLDEN_DIGEST = "sha256:c50eb90ac32fceb8cdcf29e9917d1e4558e8b6defabce07ee3186fcebd144be3"


def test_the_encoding_is_pinned() -> None:
    """A change here changes every stored digest: the next open of every workspace rebuilds its mirror (a cache,
    ADR-0015). Bump `v` with any change of meaning."""
    assert encode(GOLDEN) == GOLDEN_ENCODING


def test_the_digest_is_the_sha256_of_the_encoding() -> None:
    assert digest(GOLDEN) == GOLDEN_DIGEST


def test_a_fingerprint_is_built_only_in_canonical_order() -> None:
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (("/r/b", Absent()), ("/r/a", Absent())), (), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (("/r/a", Absent()), ("/r/a", Absent())), (), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (), (("/r/*", ()), ("/r/*", ())), ())
    with pytest.raises(ValueError, match="not canonical"):
        Fingerprint("/r", (), (), (("/r/d", ("b", "a")),))


def test_a_content_state_is_a_sha256() -> None:
    with pytest.raises(ValueError, match="not canonical: sha256"):
        Content("not-a-hash")
    with pytest.raises(ValueError, match="not canonical: sha256"):
        Content("ABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABAB")


# Properties. Paths include quotes, backslashes, NUL, surrogates (undecodable names on Linux) and non-ASCII.
paths = st.text(min_size=1, max_size=12)
hexes = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
states: st.SearchStrategy[FileState] = st.one_of(
    hexes.map(Content), st.just(Absent()), st.sampled_from(["EACCES", "EIO", "ELOOP"]).map(Unreadable)
)
fingerprints = st.builds(
    Fingerprint.of,
    paths,
    st.dictionaries(paths, states, max_size=4),
    st.dictionaries(paths, st.lists(paths, max_size=3).map(tuple), max_size=3),
    st.dictionaries(paths, st.one_of(st.none(), st.lists(paths, max_size=3, unique=True).map(tuple)), max_size=3),
)


def decode(data: bytes) -> Fingerprint:
    """The inverse of `encode`, here only: if every encoding decodes back to its value, no two values share one."""
    raw = json.loads(data)
    assert set(raw) == {"v", "root", "files", "globs", "documents"}
    assert raw["v"] == 1

    def state(s: list[str]) -> FileState:
        match s:
            case ["sha256", h]:
                return Content(h)
            case ["absent"]:
                return Absent()
            case ["unreadable", e]:
                return Unreadable(e)
            case _:
                raise AssertionError(s)

    return Fingerprint(
        raw["root"],
        tuple((p, state(s)) for p, s in raw["files"]),
        tuple((p, tuple(m)) for p, m in raw["globs"]),
        tuple((p, None if m is None else tuple(m)) for p, m in raw["documents"]),
    )


@given(fingerprints)
def test_every_encoding_decodes_back_to_its_fingerprint(fp: Fingerprint) -> None:
    assert decode(encode(fp)) == fp


@given(fingerprints, st.randoms())
def test_the_order_of_the_given_paths_does_not_matter(fp: Fingerprint, rnd: random.Random) -> None:
    files, globs, documents = list(fp.files), list(fp.globs), list(fp.documents)
    for items in (files, globs, documents):
        rnd.shuffle(items)
    again = Fingerprint.of(
        fp.root, dict(files), dict(globs), {k: v and tuple(rnd.sample(v, len(v))) for k, v in documents}
    )
    assert again == fp
    assert digest(again) == digest(fp)


@given(fingerprints, paths, paths)
def test_a_glob_matches_order_is_kept(fp: Fingerprint, a: str, b: str) -> None:
    """The loader parses glob matches in the order the glob returns them; that order can change entry order."""
    if a == b:
        return
    one = Fingerprint.of(fp.root, dict(fp.files), {"/g/*": (a, b)}, dict(fp.documents))
    two = Fingerprint.of(fp.root, dict(fp.files), {"/g/*": (b, a)}, dict(fp.documents))
    assert digest(one) != digest(two)


@given(fingerprints)
def test_the_ledger_set_is_the_fingerprints_keys(fp: Fingerprint) -> None:
    ls = fp.ledger_set
    assert ls.root == fp.root
    assert ls.files == tuple(p for p, _ in fp.files)
    assert ls.globs == tuple(p for p, _ in fp.globs)
    assert ls.documents == tuple(p for p, _ in fp.documents)
