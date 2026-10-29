"""`canonical_path` and `path_key` (`design/specs/engine-jobs.md` §6.3, §14 "Paths"; S0060).

The requirement: one file has one key, whatever spelling names it and whether it exists yet (an absent
`Prices.beancount` and the file a save then creates as `prices.beancount` on a case-insensitive volume share every
lock, fingerprint entry and journal match), and two different files never share a key on a volume that keeps their
names apart, except where Python's folding merges what the volume keeps apart (§15: merge, never split).
The corpus runs on the real file system (macOS) and over the fake for a case-sensitive volume.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pytest

from ledger_engine.fs.port import FileSystem, FoldRule, Resolved
from ledger_engine.ledger.core import paths as core
from ledger_engine.ledger.paths import canonical_path, identify, path_key
from tests.fakes.memfs import MemFS

pytestmark = pytest.mark.inv("INV-32")
macos = pytest.mark.skipif(sys.platform != "darwin", reason="the real adapter is macOS-only until BL-0087")

NFC = "Café.beancount"
NFD = unicodedata.normalize("NFD", NFC)


def test_fold_is_nfd_then_casefold_when_the_volume_is_case_insensitive() -> None:
    ci, cs = FoldRule(nfd=True, casefold=True), FoldRule(nfd=True, casefold=False)
    assert core.fold(NFC, ci) == core.fold(NFD.upper(), ci) == unicodedata.normalize("NFD", "café.beancount")
    assert core.fold(NFC, cs) == core.fold(NFD, cs) != core.fold(NFC.upper(), cs)
    assert core.fold("Straße", ci) == core.fold("STRASSE", ci)  # casefold, not lower
    assert core.fold(NFC, FoldRule(nfd=False, casefold=False)) == NFC  # Linux: none


def test_the_key_folds_each_component_by_its_own_directorys_rule() -> None:
    r = Resolved(
        existing=("Vol", "Ledger"),
        rest=("New",),
        rules=(FoldRule(True, True), FoldRule(True, False), FoldRule(True, False)),
    )
    assert core.canonical_path(r) == "/Vol/Ledger/New"
    assert core.path_key(r) == "/vol/Ledger/New"


def test_one_rule_per_component_is_required() -> None:
    with pytest.raises(ValueError, match="rule"):
        core.path_key(Resolved(existing=("a",), rest=(), rules=()))


@dataclass(frozen=True)
class Real:
    root: Path
    fs: FileSystem


@pytest.fixture
def real(tmp_path: Path) -> Real:
    from ledger_engine.fs.darwin import DarwinFS

    if os.pathconf(tmp_path, 11) == 1:  # _PC_CASE_SENSITIVE
        pytest.skip("the temp volume is case-sensitive; this corpus is for the default case-insensitive APFS")
    (tmp_path / "Ledger").mkdir()
    (tmp_path / "Ledger" / NFC).write_bytes(b"x")
    (tmp_path / "Ledger" / "Main.beancount").write_bytes(b"x")
    (tmp_path / "linked").symlink_to(tmp_path / "Ledger")
    (tmp_path / "Ledger" / "alias.beancount").symlink_to(tmp_path / "Ledger" / "Main.beancount")
    return Real(tmp_path, DarwinFS())


@macos
def test_case_and_normalization_spellings_of_one_file_share_its_canonical_path_and_key(real: Real) -> None:
    fs, root = real.fs, real.root
    base = canonical_path(fs, str(root))
    spellings = [f"{root}/Ledger/{NFC}", f"{root}/LEDGER/{NFD}", f"{root}/ledger/{NFC.upper()}"]
    ids = {identify(fs, p) for p in spellings}
    assert len(ids) == 1
    (only,) = ids
    (stored,) = [n for n in os.listdir(root / "Ledger") if unicodedata.normalize("NFC", n) == NFC]
    assert only.canonical == f"{base}/Ledger/{stored}"  # exactly the volume's stored spelling


@macos
def test_a_symlinked_file_and_a_symlinked_folder_resolve_to_the_real_path(real: Real) -> None:
    fs, root = real.fs, real.root
    main = identify(fs, f"{root}/Ledger/Main.beancount")
    assert identify(fs, f"{root}/Ledger/alias.beancount") == main
    assert identify(fs, f"{root}/linked/main.beancount") == main
    assert "/private/" in main.canonical or not str(root).startswith("/var/")  # /var is a link to /private/var


@macos
def test_the_key_of_an_absent_path_equals_the_key_of_the_file_after_it_is_created_under_any_equal_spelling(
    real: Real,
) -> None:
    fs, root = real.fs, real.root
    absent = [f"{root}/Ledger/Prices.beancount", f"{root}/linked/PRICES.beancount", f"{root}/Ledger/new/Sub.bean"]
    before = [path_key(fs, p) for p in absent]
    (root / "Ledger" / "prices.BEANCOUNT").write_bytes(b"created under another spelling")
    (root / "Ledger" / "NEW").mkdir()
    (root / "Ledger" / "NEW" / "sub.BEAN").write_bytes(b"x")
    after = [path_key(fs, p) for p in absent]
    assert before[0] == before[1] == after[0] == after[1]
    assert before[2] == after[2]
    assert canonical_path(fs, absent[0]).endswith("/Ledger/prices.BEANCOUNT")  # the stored spelling once it exists


@macos
def test_two_different_files_have_two_keys(real: Real) -> None:
    fs, root = real.fs, real.root
    assert path_key(fs, f"{root}/Ledger/Main.beancount") != path_key(fs, f"{root}/Ledger/{NFC}")


def fake_tree(*, ci: bool) -> MemFS:
    fs = MemFS(case_insensitive=ci)
    fs.outside_mkdirs("/v/Ledger")
    fs.outside_write("/v/Ledger/Main.beancount", b"x")
    fs.outside_symlink("/v/linked", "/v/Ledger")
    return fs


def test_on_a_case_sensitive_volume_case_spellings_are_different_files_and_keys() -> None:
    fs = fake_tree(ci=False)
    assert path_key(fs, "/v/Ledger/Main.beancount") != path_key(fs, "/v/Ledger/main.beancount")
    assert identify(fs, "/v/linked/Main.beancount") == identify(fs, "/v/Ledger/Main.beancount")
    # still NFD on macOS: normalization never splits one name into two keys
    assert path_key(fs, f"/v/Ledger/{NFC}") == path_key(fs, f"/v/Ledger/{NFD}")


def test_on_a_case_sensitive_volume_an_absent_path_keeps_its_key_once_created() -> None:
    fs = fake_tree(ci=False)
    before = path_key(fs, "/v/Ledger/New/Prices.beancount")
    fs.outside_mkdirs("/v/Ledger/New")
    fs.outside_write("/v/Ledger/New/Prices.beancount", b"x")
    assert path_key(fs, "/v/Ledger/New/Prices.beancount") == before


def test_a_case_insensitive_folder_inside_a_case_sensitive_volume_folds_only_its_own_names() -> None:
    fs = MemFS(case_insensitive=False)
    fs.outside_mkdirs("/v/CI", case_insensitive=True)
    fs.outside_write("/v/CI/File", b"x")
    assert path_key(fs, "/v/CI/FILE") == path_key(fs, "/v/CI/file") == "/v/CI/file"


def test_relative_paths_and_dot_components_are_refused() -> None:
    fs = fake_tree(ci=True)
    for bad in ("v/Ledger", "/v/./Ledger", "/v/Ledger/../x"):
        with pytest.raises(ValueError, match=r"path|component"):
            path_key(fs, bad)


@macos
def test_a_hard_linked_file_has_one_identity_and_each_name_its_own_canonical_path(real: Real) -> None:
    """S0060 red team 1: the canonical path of a hard-linked file is the name asked for, however recently the other
    name was looked up; the identity (`st_dev`, `st_ino`) is the same for both."""
    fs, root = real.fs, real.root
    (root / "Other").mkdir()
    os.link(root / "Ledger" / "Main.beancount", root / "Other" / "copy.beancount")
    for _ in range(10):
        os.stat(root / "Other" / "copy.beancount")
        main = fs.resolve(f"{root}/Ledger/main.beancount")
        assert main.existing[-2:] == ("Ledger", "Main.beancount")
        os.stat(root / "Ledger" / "Main.beancount")
        other = fs.resolve(f"{root}/Other/copy.beancount")
        assert other.existing[-2:] == ("Other", "copy.beancount")
        assert main.identity is not None
        assert main.identity == other.identity


def test_on_the_fake_a_hard_linked_file_has_one_identity_and_each_name_its_own_path() -> None:
    fs = fake_tree(ci=True)
    fs.outside_mkdirs("/v/Other")
    fs.outside_link("/v/Ledger/Main.beancount", "/v/Other/copy.beancount")
    a, b = fs.resolve("/v/Ledger/main.beancount"), fs.resolve("/v/Other/copy.beancount")
    assert (a.existing, b.existing) == (("v", "Ledger", "Main.beancount"), ("v", "Other", "copy.beancount"))
    assert a.identity is not None
    assert a.identity == b.identity


@macos
def test_a_dangling_link_resolves_as_absent_with_its_own_name_missing(real: Real) -> None:
    """@regression-guard (S0060 verifier F3: the adapter already did this; the fake now agrees, its row is in
    `tests/fakes/test_memfs.py`)."""
    fs, root = real.fs, real.root
    (root / "Ledger" / "new.beancount").symlink_to(root / "nowhere" / "x.beancount")
    r = fs.resolve(f"{root}/Ledger/new.beancount")
    assert r.rest == ("new.beancount",)
    assert r.existing[-1] == "Ledger"
