"""The write-intent journal's record with Windows paths (S0127, BL-0279; `design/specs/engine-jobs.md` §6.3, §6.5).

Requirements: on Windows the record names files and folders by their Windows canonical paths (a drive or UNC root,
`\\`), each key folded from its path by Windows' rule (ordinal upper case, or none in a case-sensitive folder), and a
record that names a path Windows cannot hold safely (a reserved device name, a trailing dot or space) is not a record
of ours. The format is the same: no new field, no new version (the POSIX bytes: `tests/test_posix_path_pins.py`).
Pure: the Windows model is simulated, so these run on every OS."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from ledger_engine.fs import pathmodel
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import (
    VERSION,
    DirEntry,
    FileEntry,
    FolderEntry,
    Record,
    Unreadable,
    decode,
    encode,
    needed_folders,
    temp_name,
)

pytestmark = pytest.mark.inv("INV-32")

VOL = "6d3f0a52-8b1c-4e7a-9f20-3c5b7d9e1a40"
BORN = 1_794_157_282_123_456_789
TAG = "s1-j7"


@pytest.fixture(autouse=True)
def _windows() -> Iterator[None]:
    with pathmodel.simulating(pathmodel.WINDOWS):
        yield


def _file(canonical: str, key: str, index: int = 0, *, pre: bool = True) -> FileEntry:
    name = canonical.rpartition("\\")[2]
    return FileEntry(
        canonical,
        key,
        "1" * 64 if pre else None,
        "2" * 64,
        f"{index}-{name}" if pre else None,
        temp_name(name, TAG),
    )


def record(files: tuple[FileEntry, ...], dirs: tuple[DirEntry, ...] = ()) -> Record:
    made = {d.canonical_path for d in dirs}
    need = sorted(needed_folders(files, dirs))
    return Record(
        session="s1",
        job="j7",
        workspace_key="k1",
        lineage="lin1",
        engine_build="build1",
        backup_dir="C:\\Books\\a\\AppData\\Local\\Finzytrack\\local\\k1\\backups\\s1-j7",
        files=files,
        created_dirs=dirs,
        folders=tuple(FolderEntry(p, VOL, "NTFS", None if p in made else BORN) for p in need),
    )


def _record() -> Record:
    """Built under the Windows model (the module's fixture), as a Windows save would record it."""
    return record(
        (
            _file("C:\\Books\\a\\Ledger\\inc\\a.bean", "C:\\BOOKS\\A\\LEDGER\\INC\\A.BEAN"),
            _file(
                "C:\\Books\\a\\Ledger\\y2026\\Caf\u00e9.bean", "C:\\BOOKS\\A\\LEDGER\\Y2026\\CAF\u00c9.BEAN", pre=False
            ),
        ),
        (DirEntry("C:\\Books\\a\\Ledger\\y2026", "C:\\BOOKS\\A\\LEDGER\\Y2026"),),
    )


def test_a_record_with_windows_paths_round_trips_in_the_same_format() -> None:
    rec = _record()
    data = encode(rec)
    assert decode(data) == rec
    assert f'"v":{VERSION},' in data.decode()
    assert '"canonical_path":"C:\\\\Books\\\\a\\\\Ledger\\\\inc\\\\a.bean"' in data.decode()


def test_the_folders_of_a_windows_record_are_named_as_windows_names_them() -> None:
    rec = _record()
    assert needed_folders(rec.files, rec.created_dirs) == [
        "C:\\Books\\a\\Ledger\\inc",
        "C:\\Books\\a\\Ledger\\y2026",
        "C:\\Books\\a\\Ledger",
    ]
    top = record((_file("C:\\a.bean", "C:\\A.BEAN"),))
    assert needed_folders(top.files, top.created_dirs) == ["C:\\"]
    assert decode(encode(top)) == top


def test_a_unc_record_round_trips() -> None:
    r = record((_file("\\\\nas\\Books\\L\\a.bean", "\\\\NAS\\BOOKS\\L\\A.BEAN"),))
    assert decode(encode(r)) == r


def test_a_key_in_a_case_sensitive_windows_folder_keeps_its_case() -> None:
    r = record((_file("C:\\src\\Ledger\\a.bean", "C:\\SRC\\Ledger\\a.bean"),))
    assert decode(encode(r)) == r


@pytest.mark.parametrize(
    "key",
    [
        "c:\\books\\a\\ledger\\inc\\a.bean",  # casefold: macOS's rule, not Windows'
        "C:\\BOOKS\\A\\LEDGER\\INC\\B.BEAN",  # another file
    ],
)
def test_a_key_not_folded_by_windows_rule_is_unreadable(key: str) -> None:
    rec = _record()
    bad = replace(rec, files=(replace(rec.files[0], path_key=key), rec.files[1]))
    with pytest.raises(ValueError, match="bad journal record"):
        encode(bad)
    good = encode(rec).decode()
    assert isinstance(
        decode(good.replace("C:\\\\BOOKS\\\\A\\\\LEDGER\\\\INC\\\\A.BEAN", key.replace("\\", "\\\\")).encode()),
        Unreadable,
    )


@pytest.mark.parametrize(
    ("canonical", "key"),
    [
        ("C:\\Books\\a\\Ledger\\aux.bean", "C:\\BOOKS\\A\\LEDGER\\AUX.BEAN"),
        ("C:\\Books\\a\\con\\a.bean", "C:\\BOOKS\\A\\CON\\A.BEAN"),
        ("C:\\Books\\a\\Ledger.\\a.bean", "C:\\BOOKS\\A\\LEDGER.\\A.BEAN"),
        ("C:\\Books\\a\\Ledger \\a.bean", "C:\\BOOKS\\A\\LEDGER \\A.BEAN"),
        ("/Books/a/Ledger/a.bean", "/BOOKS/A/LEDGER/A.BEAN"),
    ],
)
def test_a_record_naming_a_path_windows_cannot_hold_safely_is_refused(canonical: str, key: str) -> None:
    with pytest.raises(ValueError, match=r"malformed path|bad journal record"):
        encode(record((_file(canonical, key),)))


def test_the_widest_folding_on_windows_takes_ordinal_upper_case_too() -> None:
    """Two files of a case-sensitive folder (keys exact) whose names of ours only Windows' upper case makes one (U+0131,
    dotless i, and `I`; casefold keeps them apart) collide: refused, as on a case-sensitive macOS volume (BL-0162)."""
    r = record(
        (
            _file("C:\\L\\\u0131.bean", "C:\\L\\\u0131.bean"),
            _file("C:\\L\\I.bean", "C:\\L\\I.bean", 1),
        )
    )
    with pytest.raises(ValueError, match="duplicate_temp"):
        encode(r)
    assert journal.case_twin(r) is not None
