"""The engine's path model per operating system (`ledger_engine.fs.pathmodel`; `design/specs/engine-jobs.md` §6.3;
S0127, BL-0279).

Requirements (the brief and S0126's probe on Windows Server 2022): Windows paths have a drive letter or a UNC root,
`\\` separators, and the `\\\\?\\` prefix where Windows needs it; names Windows cannot hold safely are refused before
anything is written, with a named reason (without the prefix Windows opens the device for `aux.txt` and drops a
trailing dot or space: probed); `path_key` folds by ordinal upper case unless the folder is case-sensitive; the path
guard's `same_path` compares as Windows gives paths back. The POSIX model is exactly today's functions
(`tests/test_posix_path_pins.py` pins their outputs). Pure: these run on every OS."""

from __future__ import annotations

import sys
import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_engine.fs import pathmodel
from ledger_engine.fs.pathmodel import POSIX, WINDOWS, FoldRule, MalformedPathError, fold, ordinal_upper
from ledger_engine.fs.port import Resolved
from ledger_engine.ledger.core import paths as core_paths

pytestmark = pytest.mark.inv("INV-32")

B = "\\"
UPPER = FoldRule(nfd=False, casefold=False, upper=True)
EXACT = FoldRule(nfd=False, casefold=False)


# -- selection --------------------------------------------------------------------------------------------------------


def test_each_os_gets_its_own_model() -> None:
    assert pathmodel.for_platform("win32") is WINDOWS
    assert pathmodel.for_platform("darwin") is POSIX
    assert pathmodel.for_platform("linux") is POSIX
    assert pathmodel.HOST is pathmodel.for_platform(sys.platform)
    assert pathmodel.host() is pathmodel.HOST


def test_simulating_another_model_is_scoped() -> None:
    other = WINDOWS if pathmodel.HOST is POSIX else POSIX
    with pathmodel.simulating(other):
        assert pathmodel.host() is other
    assert pathmodel.host() is pathmodel.HOST


# -- Windows: absolute paths ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "parts"),
    [
        ("C:\\", ("C:",)),
        ("C:\\Books\\a\\L.beancount", ("C:", "Books", "a", "L.beancount")),
        ("c:\\x", ("C:", "x")),  # the drive letter in Windows' own spelling (upper case)
        ("C:/x/y", ("C:", "x", "y")),  # Windows takes `/` as a separator
        ("C:\\x\\\\y\\", ("C:", "x", "y")),  # empty components dropped, as POSIX drops them
        ("\\\\server\\share", ("\\\\server\\share",)),
        ("\\\\server\\share\\a\\b", ("\\\\server\\share", "a", "b")),
        ("//server/share/a", ("\\\\server\\share", "a")),
        ("\\\\?\\C:\\a", ("C:", "a")),  # the long-path prefix is a spelling, not another place
        ("\\\\?\\c:", ("C:",)),
        ("\\\\?\\UNC\\server\\share\\a", ("\\\\server\\share", "a")),
        (
            "\\\\?\\Volume{0b9e2c61-4d7f-4a38-8e15-92a6f0c3d7b4}\\a",
            ("\\\\?\\Volume{0b9e2c61-4d7f-4a38-8e15-92a6f0c3d7b4}", "a"),
        ),
    ],
)
def test_windows_splits_a_drive_unc_or_prefixed_path_into_its_root_and_names(path: str, parts: tuple[str, ...]) -> None:
    assert WINDOWS.split(path) == parts


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("", "not_absolute"),
        ("a\\b", "not_absolute"),
        ("\\a", "not_absolute"),  # rooted on whatever the current drive is
        ("C:a", "not_absolute"),  # relative to the drive's current folder
        ("C:", "not_absolute"),
        ("/a/b", "not_absolute"),  # a POSIX path names no Windows place
        ("\\\\server", "unc_without_share"),
        ("\\\\server\\", "unc_without_share"),
        ("\\\\.\\C:\\a", "device_path"),
        ("\\\\.\\PhysicalDrive0", "device_path"),
        ("\\\\?\\GLOBALROOT\\Device\\x", "device_path"),
        ("\\??\\C:\\a", "device_path"),
        ("\\\\?\\C:/a", "invalid_character"),  # under the prefix `/` is no separator
        ("C:\\a\\.\\b", "not_one_component"),
        ("C:\\a\\..\\b", "not_one_component"),
        ("C:\\a\0b", "not_one_component"),
        ("C:\\a\\aux.txt", "reserved_name"),
        ("\\\\server\\con\\a", "reserved_name"),
        ("C:\\a.\\b", "trailing_dot_or_space"),
        ("C:\\a\\b:stream", "invalid_character"),
    ],
)
def test_windows_refuses_what_is_not_an_absolute_path_it_can_hold(path: str, reason: str) -> None:
    with pytest.raises(MalformedPathError, match=reason):
        WINDOWS.split(path)


@pytest.mark.parametrize(
    "name",
    [
        "con",
        "CON",
        "prn",
        "aux",
        "nul",
        "Nul.txt",
        "aux.tar.gz",
        "aux .txt",  # Windows drops the spaces before the extension
        "nul ",
        *(f"com{i}" for i in range(10)),
        *(f"LPT{i}.log" for i in range(10)),
        "com\u00b9",
        "lpt\u00b3.x",
        "conin$",
        "CONOUT$.txt",
    ],
)
def test_windows_refuses_reserved_device_names_with_or_without_an_extension(name: str) -> None:
    with pytest.raises(MalformedPathError, match="reserved_name"):
        WINDOWS.check_name(name)


@pytest.mark.parametrize("name", ["a.", "a ", "a. ", "a .", "..."])
def test_windows_refuses_a_name_ending_in_a_dot_or_a_space(name: str) -> None:
    with pytest.raises(MalformedPathError, match="trailing_dot_or_space"):
        WINDOWS.check_name(name)


@pytest.mark.parametrize("ch", [*'<>:"|?*', *(chr(c) for c in range(1, 32))])
def test_windows_refuses_characters_windows_names_cannot_hold(ch: str) -> None:
    with pytest.raises(MalformedPathError, match="invalid_character"):
        WINDOWS.check_name(f"a{ch}b")


@pytest.mark.parametrize(
    "name", ["console", "auxiliary.txt", "com10", "lpt", "a.b", ".hidden", " lead", "nul_", "com1x", "Caf\u00e9"]
)
def test_windows_takes_names_that_only_look_like_reserved_ones(name: str) -> None:
    assert WINDOWS.check_name(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "a\\b", "a/b", "a\0b"])
def test_windows_refuses_what_is_not_one_name(name: str) -> None:
    with pytest.raises(MalformedPathError, match="not_one_component"):
        WINDOWS.check_name(name)


@pytest.mark.parametrize("name", ["con", "aux.txt", "a.", "a ", "a:b", "a\\b", "a|b"])
def test_the_posix_model_takes_every_name_it_took_before(name: str) -> None:
    """The reserved-name check runs on Windows only (S0127 decision): macOS and Linux keep these names, and a ledger
    there that names one keeps saving exactly as before."""
    assert POSIX.check_name(name) == name


# -- Windows: joining, normal form, the prefix ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("parts", "path"),
    [
        (("C:",), "C:\\"),
        (("C:", "a", "b"), "C:\\a\\b"),
        (("\\\\s\\sh",), "\\\\s\\sh\\"),
        (("\\\\s\\sh", "a"), "\\\\s\\sh\\a"),
    ],
)
def test_windows_joins_a_root_and_names(parts: tuple[str, ...], path: str) -> None:
    assert WINDOWS.join(parts) == path
    assert WINDOWS.split(path) == parts


@given(st.lists(st.text(alphabet="abcXYZ\u00e9 ._-", min_size=1, max_size=6), max_size=5), st.sampled_from("CDz"))
def test_windows_join_and_split_are_inverse_on_names_it_takes(names: list[str], drive: str) -> None:
    try:
        ok = tuple(WINDOWS.check_name(n) for n in names)
    except MalformedPathError:
        return
    parts = (f"{drive.upper()}:", *ok)
    assert WINDOWS.split(WINDOWS.join(parts)) == parts


@pytest.mark.parametrize(
    ("path", "normal"),
    [
        ("C:\\a\\b", "C:\\a\\b"),
        ("C:/a/b", "C:\\a\\b"),
        ("c:\\a\\.\\b\\..\\c", "C:\\a\\c"),
        ("C:\\a\\\\b\\", "C:\\a\\b"),
        ("\\\\?\\C:\\a", "C:\\a"),
        ("\\\\?\\UNC\\s\\sh\\a", "\\\\s\\sh\\a"),
        ("C:\\", "C:\\"),
    ],
)
def test_windows_normal_form(path: str, normal: str) -> None:
    assert WINDOWS.normpath(path) == normal
    assert WINDOWS.normpath(normal) == normal


@pytest.mark.parametrize(
    ("path", "long"),
    [
        ("C:\\a", "\\\\?\\C:\\a"),
        ("C:\\", "\\\\?\\C:\\"),
        ("\\\\s\\sh\\a", "\\\\?\\UNC\\s\\sh\\a"),
        (
            "\\\\?\\Volume{0b9e2c61-4d7f-4a38-8e15-92a6f0c3d7b4}\\a",
            "\\\\?\\Volume{0b9e2c61-4d7f-4a38-8e15-92a6f0c3d7b4}\\a",
        ),
    ],
)
def test_windows_gives_the_prefixed_form_for_the_systems_calls(path: str, long: str) -> None:
    assert WINDOWS.extended(path) == long
    assert WINDOWS.split(long) == WINDOWS.split(path)


@pytest.mark.parametrize(
    ("folder", "name", "path"),
    [
        ("C:\\", "a", "C:\\a"),
        ("C:\\a", "b", "C:\\a\\b"),
        ("C:\\a", "b/c", "C:\\a\\b\\c"),
        ("\\\\s\\sh\\", "a", "\\\\s\\sh\\a"),
    ],
)
def test_windows_child_takes_a_name_or_a_plans_relative_path(folder: str, name: str, path: str) -> None:
    """A plan's relative paths use `/` on every OS (S0127 decision); the child is in the OS's own form."""
    assert WINDOWS.child(folder, name) == path


@pytest.mark.parametrize(
    ("path", "parent", "name"),
    [("C:\\a\\b", "C:\\a", "b"), ("C:\\a", "C:\\", "a"), ("\\\\s\\sh\\a", "\\\\s\\sh\\", "a")],
)
def test_windows_parent_and_name(path: str, parent: str, name: str) -> None:
    assert WINDOWS.parent_and_name(path) == (parent, name)


@pytest.mark.parametrize(
    ("path", "folder", "under", "strictly"),
    [
        ("C:\\a\\b", "C:\\a", True, True),
        ("C:\\a", "C:\\a", True, False),
        ("C:\\ab", "C:\\a", False, False),
        ("C:\\a", "C:\\", True, True),
        ("D:\\a", "C:\\", False, False),
    ],
)
def test_windows_under(path: str, folder: str, under: bool, strictly: bool) -> None:
    assert WINDOWS.is_under(path, folder) is under
    assert WINDOWS.is_under(path, folder, strict=True) is strictly


@pytest.mark.parametrize(
    ("text", "absolute"),
    [
        ("C:\\a", True),
        ("C:/a", True),
        ("\\\\s\\sh", True),
        ("\\\\?\\C:\\a", True),
        ("/a", False),
        ("\\a", False),
        ("C:a", False),
        ("a", False),
        ("", False),
    ],
)
def test_windows_absolute_text(text: str, absolute: bool) -> None:
    assert WINDOWS.is_absolute(text) is absolute


# -- Windows: keys and the path guard ---------------------------------------------------------------------------------


def test_ordinal_upper_case_folds_one_character_at_a_time() -> None:
    assert ordinal_upper("Ledger.beancount") == "LEDGER.BEANCOUNT"
    assert ordinal_upper("stra\u00dfe") == "STRA\u00dfE"  # no full mapping: `ß` has no one-character upper case
    assert ordinal_upper("\u1f80") == "\u1f88"  # the simple (title) mapping where the full one is two characters
    assert ordinal_upper("Caf\u00e9") == "CAF\u00c9"
    assert ordinal_upper("e\u0301") == "E\u0301"  # no normalization: NTFS keeps both spellings apart
    assert ordinal_upper("\U00010428") == "\U00010428"  # beyond the 16-bit range NTFS's table covers


def test_windows_folds_by_ordinal_upper_case_unless_the_folder_is_case_sensitive() -> None:
    assert fold("Caf\u00e9.Bean", UPPER) == "CAF\u00c9.BEAN"
    assert fold("Caf\u00e9.Bean", EXACT) == "Caf\u00e9.Bean"
    assert fold("Caf\u00e9", UPPER) != fold(unicodedata.normalize("NFD", "Caf\u00e9"), UPPER)


def test_windows_keys_one_file_under_any_spelling_windows_treats_as_one() -> None:
    r1 = Resolved(("C:", "Books", "Ledger"), ("Prices.beancount",), (UPPER, UPPER, UPPER, UPPER))
    r2 = Resolved(("C:", "Books", "Ledger"), ("PRICES.beancount",), (UPPER, UPPER, UPPER, UPPER))
    with pathmodel.simulating(WINDOWS):
        assert core_paths.canonical_path(r1) == "C:\\Books\\Ledger\\Prices.beancount"
        assert core_paths.path_key(r1) == core_paths.path_key(r2) == "C:\\BOOKS\\LEDGER\\PRICES.BEANCOUNT"
        case_sensitive = Resolved(("C:", "src"), ("A", "a"), (UPPER, UPPER, EXACT, EXACT))
        assert core_paths.path_key(case_sensitive) == "C:\\SRC\\A\\a"


def test_windows_keys_a_unc_root_without_case() -> None:
    r = Resolved(("\\\\Server\\Share", "L"), (), (UPPER, UPPER))
    with pathmodel.simulating(WINDOWS):
        assert core_paths.canonical_path(r) == "\\\\Server\\Share\\L"
        assert core_paths.path_key(r) == "\\\\SERVER\\SHARE\\L"


def test_the_widest_rule_on_windows_takes_every_pair_ordinal_upper_case_makes_one() -> None:
    """The journal's widest rule (`PathModel.wide`), which refuses names of ours that might collide, takes every pair
    of names ordinal upper case makes one (per character, over the 16-bit range), so no collision on Windows passes
    it. POSIX's widest rule (NFD and casefold) alone would miss some (U+0131, dotless i, and `I`),
    hence one per model."""

    def missed(rule: FoldRule) -> list[int]:
        return [
            c
            for c in range(0x10000)
            if not 0xD800 <= c <= 0xDFFF and fold(chr(c), rule) != fold(ordinal_upper(chr(c)), rule)
        ]

    assert missed(WINDOWS.wide) == []
    assert 0x131 in missed(POSIX.wide)
    assert POSIX.wide == FoldRule(nfd=True, casefold=True)  # unchanged on macOS and Linux


@pytest.mark.parametrize(
    ("now", "recorded", "same"),
    [
        ("C:\\Books\\L", "C:\\Books\\L", True),
        ("\\\\?\\C:\\Books\\L", "C:\\Books\\L", True),  # GetFinalPathNameByHandleW's form
        ("c:\\Books\\L", "C:\\Books\\L", True),  # the drive letter has no case
        ("\\\\?\\UNC\\SERVER\\share\\L", "\\\\server\\Share\\L", True),  # nor the server and share
        ("C:\\users\\L", "C:\\Books\\L", False),  # a folder renamed in case only has moved (fails closed)
        ("C:\\Books\\L2", "C:\\Books\\L", False),
        ("D:\\Books\\L", "C:\\Books\\L", False),
        ("C:\\Caf\u00e9", unicodedata.normalize("NFD", "C:\\Caf\u00e9"), False),  # NTFS keeps both spellings
        ("not a path", "C:\\Books\\L", False),
    ],
)
def test_windows_same_path_compares_as_windows_gives_paths_back(now: str, recorded: str, same: bool) -> None:
    assert WINDOWS.same_path(now, recorded) is same


def test_windows_rules_a_journal_key_may_be_folded_by() -> None:
    assert set(WINDOWS.rules) == {UPPER, EXACT}
    assert all(not r.upper for r in POSIX.rules)
