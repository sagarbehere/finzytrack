"""The write-intent journal's record (`design/specs/engine-jobs.md` §6.1 step 8.2, §6.5; ADR-0012; S0060).

Requirements: the record names everything recovery and the closed record need (§6.1 step 8.2), and `decode` never
returns a partial record: a record cut off at any length (a power loss keeps an entry before its data), a foreign file,
a newer version (an older app refuses a newer journal, ADR-0012), a missing, extra or malformed field are all
`Unreadable`, which recovery treats as blocking every open (§8.1)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_engine.ledger.core.journal import (
    VERSION,
    DirEntry,
    FileEntry,
    FolderEntry,
    Record,
    State,
    Unreadable,
    backup_name,
    decode,
    encode,
    file_name,
    folders,
    is_kept_name,
    kept_name,
    needed_folders,
    parse_file_name,
    record_name,
    restore_staging_name,
    restore_temp_name,
    scratch_names,
    staging_name,
    temp_name,
)

pytestmark = pytest.mark.inv("INV-32")

VOL = "6d3f0a52-8b1c-4e7a-9f20-3c5b7d9e1a40"  # the drive /w/L is on (S0089, BL-0188)
OTHER_VOL = "0b9e2c61-4d7f-4a38-8e15-92a6f0c3d7b4"
BORN = 1_794_157_282_123_456_789  # a folder's creation time, ns

RECORD = Record(
    session="s1",
    job="j7",
    workspace_key="k1",
    lineage="lin1",
    engine_build="build1",
    backup_dir="/app/local/k1/backups/s1-j7",
    files=(
        FileEntry("/w/L/inc/a.bean", "/w/l/inc/a.bean", "1" * 64, "2" * 64, "0-a.bean", ".a.bean.ft-s1-j7.tmp"),
        FileEntry(
            "/w/L/y2026/Caf\u00e9.bean",
            "/w/l/y2026/cafe\u0301.bean",
            None,
            "3" * 64,
            None,
            ".Caf\u00e9.bean.ft-s1-j7.tmp",
        ),
    ),
    created_dirs=(DirEntry("/w/L/y2026", "/w/l/y2026"),),
    folders=(
        FolderEntry("/w/L", VOL, "apfs", BORN),
        FolderEntry("/w/L/inc", VOL, "apfs", BORN + 1),
        FolderEntry("/w/L/y2026", VOL, "apfs", None),  # made by the save, after the record
    ),
)


def refit(r: Record) -> Record:
    """`r` with the `folders` its files and created folders need (on `VOL`), as a save would record them."""
    made = {d.canonical_path for d in r.created_dirs}
    need = sorted(needed_folders(r.files, r.created_dirs))
    return replace(r, folders=tuple(FolderEntry(p, VOL, "apfs", None if p in made else BORN) for p in need))


def test_every_field_round_trips() -> None:
    assert decode(encode(RECORD)) == RECORD


def test_the_encoding_is_canonical_with_its_format_and_version() -> None:
    data = encode(RECORD)
    obj = json.loads(data)
    assert (obj["format"], obj["v"]) == ("finzytrack-save-journal", VERSION)
    assert data == (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def test_a_record_cut_off_at_any_length_is_unreadable() -> None:
    data = encode(RECORD)
    for n in range(len(data)):
        assert isinstance(decode(data[:n]), Unreadable), n


def test_format_6_names_every_folder_and_older_formats_are_unreadable() -> None:
    """ADR-0068 D1 (S0070): format 4 dropped the post fingerprint and the adopted digest. Format 6 (S0089 round 1)
    names every folder the undo may write in with its drive, file system and creation time. A format 5 record (S0089's
    first commit: a UUID per file), a format 4 one (no drive), a format 3 one (S0067, with the prediction) or a format 2
    one (S0066, with a parse digest too) is never read as a record of this format."""
    obj = json.loads(encode(RECORD))
    assert (VERSION, "post_fingerprint" in obj, "adopted_digest" in obj) == (6, False, False)
    four = {k: v for k, v in obj.items() if k != "folders"} | {"v": 4}
    five = four | {"v": 5, "files": [f | {"volume": VOL} for f in obj["files"]]}
    older = four | {"v": 3, "post_fingerprint": "{}", "adopted_digest": "sha256:" + "a" * 64}
    for o in (five, four, older, older | {"v": 2, "parse_digest": "sha256:" + "b" * 64}, obj | {"v": 3}):
        out = decode((json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
        assert out == Unreadable("unknown version")
    for o in (five | {"v": 6}, four | {"v": 6}, older | {"v": 6}):  # an older record under the new version
        out = decode((json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
        assert out == Unreadable("malformed")


def test_a_newer_version_is_refused() -> None:
    obj = json.loads(encode(RECORD))
    obj["v"] = VERSION + 1
    out = decode((json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
    assert out == Unreadable("newer version")


MUTATIONS: list[Callable[[dict[str, Any]], object]] = [
    lambda o: o.pop("lineage"),
    lambda o: o.__setitem__("extra", 1),
    lambda o: o.__setitem__("format", "something-else"),
    lambda o: o.__setitem__("v", True),
    lambda o: o["files"][0].pop("pre"),
    lambda o: o["files"][0].__setitem__("post", "not a sha"),
    lambda o: o["files"][0].__setitem__("canonical_path", "relative/a.bean"),
    lambda o: o["files"][1].__setitem__("backup", "0-x"),  # a backup for a created file
    lambda o: o["files"][0].__setitem__("backup", None),  # no backup for a replaced file
    lambda o: o.__setitem__("files", []),
    lambda o: o.__setitem__("files", [o["files"][0], o["files"][0]]),  # one path_key twice
    lambda o: o.__setitem__("session", "s-1"),
    lambda o: o.__setitem__("created_dirs", {}),
    # S0089 (BL-0188; owner, 2026-11-08: "one without it is refused the same way", then "Drive ID + folder birth"):
    # every folder the undo may write in is named, once, sorted, with a lower-case non-nil UUID, a type name, and a
    # creation time exactly when the save did not make it
    lambda o: o.pop("folders"),
    lambda o: o["folders"].pop(0),  # a file's folder missing
    lambda o: o["folders"].pop(2),  # a created folder missing
    lambda o: o["folders"].append({**o["folders"][0], "canonical_path": "/w/Other"}),  # a folder not needed
    lambda o: o["folders"].reverse(),
    lambda o: o["folders"].insert(0, o["folders"][0]),
    lambda o: o["folders"][0].pop("volume"),
    lambda o: o["folders"][0].pop("born"),
    lambda o: o["folders"][0].__setitem__("extra", 1),
    lambda o: o["folders"][0].__setitem__("volume", None),
    lambda o: o["folders"][0].__setitem__("volume", ""),
    lambda o: o["folders"][0].__setitem__("volume", VOL.upper()),
    lambda o: o["folders"][0].__setitem__("volume", VOL.replace("-", "")),
    lambda o: o["folders"][0].__setitem__("volume", "00000000-0000-0000-0000-000000000000"),
    lambda o: o["folders"][0].__setitem__("volume", VOL + "0"),
    lambda o: o["folders"][0].__setitem__("fs_type", ""),
    lambda o: o["folders"][0].__setitem__("fs_type", "a/b"),
    lambda o: o["folders"][0].__setitem__("fs_type", "x" * 16),
    lambda o: o["folders"][0].__setitem__("born", None),  # a folder that was there has a creation time
    lambda o: o["folders"][0].__setitem__("born", 1.5),
    lambda o: o["folders"][0].__setitem__("born", True),
    lambda o: o["folders"][0].__setitem__("born", "1"),
    lambda o: o["folders"][2].__setitem__("born", BORN),  # a folder the save makes has none
    lambda o: o["folders"][2].__setitem__("volume", OTHER_VOL),  # made in /w/L, so on its drive
    lambda o: o["folders"][2].__setitem__("fs_type", "hfs"),
]


@pytest.mark.parametrize("mutate", MUTATIONS)
def test_a_malformed_record_is_unreadable(mutate: Callable[[dict[str, Any]], object]) -> None:
    obj = json.loads(encode(RECORD))
    mutate(obj)
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    assert isinstance(decode(data), Unreadable)


def test_a_non_canonical_but_equal_record_is_unreadable() -> None:
    """Only our own encoder's bytes are a record: a hand-edited or re-encoded file is not trusted."""
    obj = json.loads(encode(RECORD))
    data = (json.dumps(obj, indent=1) + "\n").encode()
    assert decode(data) == Unreadable("not canonical")


@given(st.binary(max_size=200))
def test_arbitrary_bytes_never_decode_into_a_record(data: bytes) -> None:
    assert isinstance(decode(data), Unreadable)


def test_encode_refuses_a_record_that_breaks_the_rules() -> None:
    with pytest.raises(ValueError, match="sha256"):
        encode(replace(RECORD, files=(replace(RECORD.files[0], pre="x"),)))
    with pytest.raises(ValueError, match="id"):
        encode(replace(RECORD, job="j-7"))


def test_names_and_suffixes() -> None:
    assert record_name("s1", "j7") == "s1-j7"
    assert [file_name("s1-j7", s) for s in State] == [
        "s1-j7.part",
        "s1-j7.open",
        "s1-j7.closed",
        "s1-j7.resolved",
        "s1-j7.kept",
    ]
    for s in State:
        assert parse_file_name(file_name("s1-j7", s)) == ("s1-j7", s)
    # S0068: the R4 gate's states are gone; a development build's file is not a record (it gates nothing)
    for other in (
        "s1-j7.done",
        "s1-j7.mismatch",
        "s1-j7.r4-attempt-1",
        "s1-j7",
        "s1-j7.tmp",
        "s1.open",
        ".s1-j7.open",
        "s 1-j7.open",
        "s1-j-7.open",
        "-j7.open",
    ):
        assert parse_file_name(other) is None, other
    assert temp_name("main.beancount", "s1-j7") == ".main.beancount.ft-s1-j7.tmp"


# S0060 red team 4, verifier Q2: the writer's naming rules are part of the format, so recovery never acts on a name
# the writer would not have written.
NAME_RULES: list[Callable[[dict[str, Any]], object]] = [
    lambda o: o["files"][0].__setitem__("temp", "../../../w/L/inc/a.bean"),
    lambda o: o["files"][0].__setitem__("temp", ".a.bean.ft-j8.tmp"),  # another job's temp
    lambda o: o["files"][0].__setitem__("backup", "../../../etc/x"),
    lambda o: o["files"][0].__setitem__("backup", "1-a.bean"),  # not its index
    lambda o: o["files"][0].__setitem__("canonical_path", "/w/L/../../etc/a.bean"),
    lambda o: o["files"][0].__setitem__("canonical_path", "/w/L/./inc/a.bean"),
    lambda o: o["files"][0].__setitem__("path_key", "/unrelated/inc/a.bean"),
    lambda o: o["files"][0].__setitem__("path_key", "/w/l/a.bean"),  # another component count
    lambda o: o.__setitem__("backup_dir", "/"),
    lambda o: o.__setitem__("backup_dir", "/app/local/k1/backups/s1-j8"),  # another save's folder
    lambda o: o.__setitem__("created_dirs", [{"canonical_path": "/", "path_key": "/"}]),
    lambda o: o.__setitem__("created_dirs", [{"canonical_path": "/w/M", "path_key": "/w/m"}]),  # no file in it
    lambda o: o.__setitem__("created_dirs", [o["created_dirs"][0], o["created_dirs"][0]]),  # one key twice
]


@pytest.mark.parametrize("mutate", NAME_RULES)
def test_a_record_that_breaks_the_writers_naming_rules_is_unreadable(
    mutate: Callable[[dict[str, Any]], object],
) -> None:
    obj = json.loads(encode(RECORD))
    mutate(obj)
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    assert isinstance(decode(data), Unreadable)


def test_derived_names_stay_within_name_max_and_are_stable() -> None:
    """Red team 11: a base name near NAME_MAX gets a hashed temp and backup name (deterministic, so decode can check
    it), and a short one keeps its readable name."""
    long = "é" * 124 + ".bean"
    assert len(long.encode()) == 253
    for derived in (temp_name(long, "s1-j7"), backup_name(10, long)):
        assert len(derived.encode()) <= 255
        assert "é" not in derived  # shortened: the full name would pass 255 bytes
    assert backup_name(3, long) == f"3-{long}"  # 255 bytes: fits, kept readable
    assert temp_name(long, "s1-j7") == temp_name(long, "s1-j7")
    assert backup_name(0, "a.bean") == "0-a.bean"
    rec = replace(
        RECORD,
        files=(
            replace(
                RECORD.files[0],
                canonical_path=f"/w/L/inc/{long}",
                path_key=f"/w/l/inc/{long}",
                temp=temp_name(long, "s1-j7"),
                backup=backup_name(0, long),
            ),
            RECORD.files[1],
        ),
    )
    assert decode(encode(rec)) == rec


def test_a_record_whose_temps_collide_is_unreadable() -> None:
    """S0060 red team r2 L1: every temp is its own name, never a file's or a created folder's path (compared folded
    the widest way)."""
    a = RECORD.files[0]
    twin = FileEntry("/w/L/inc/A.bean", "/w/L/inc/A.bean", "1" * 64, "2" * 64, "1-A.bean", ".A.bean.ft-s1-j7.tmp")
    with pytest.raises(ValueError, match="duplicate_temp"):
        encode(replace(RECORD, files=(a, twin), created_dirs=()))
    at_temp = FileEntry(
        "/w/L/inc/.a.bean.ft-s1-j7.tmp",
        "/w/l/inc/.a.bean.ft-s1-j7.tmp",
        None,
        "4" * 64,
        None,
        "..a.bean.ft-s1-j7.tmp.ft-s1-j7.tmp",
    )
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(replace(RECORD, files=(a, at_temp), created_dirs=()))
    for bad in (
        replace(RECORD, files=(a, twin), created_dirs=()),
        replace(RECORD, files=(a, at_temp), created_dirs=()),
    ):
        obj = json.loads(encode(refit(replace(bad, files=bad.files[:1]))))
        obj["files"].append(
            {
                "canonical_path": bad.files[1].canonical_path,
                "path_key": bad.files[1].path_key,
                "pre": bad.files[1].pre,
                "post": bad.files[1].post,
                "backup": bad.files[1].backup,
                "temp": bad.files[1].temp,
            }
        )
        data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        assert isinstance(decode(data), Unreadable)


def test_a_record_with_a_file_at_another_files_restore_temp_is_unreadable() -> None:
    """S0061: recovery deletes our restore temps (§6.4), so no file or folder of a record may sit at one."""
    a = RECORD.files[0]
    rtemp = restore_temp_name("a.bean", RECORD.name)
    assert rtemp == ".a.bean.ft-restore-s1-j7.tmp"
    at_rtemp = FileEntry(f"/w/L/inc/{rtemp}", f"/w/l/inc/{rtemp}", None, "4" * 64, None, temp_name(rtemp, "s1-j7"))
    bad = replace(RECORD, files=(a, at_rtemp), created_dirs=())
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(bad)
    obj = json.loads(encode(refit(replace(bad, files=bad.files[:1]))))
    f = bad.files[1]
    obj["files"].append(
        {"canonical_path": f.canonical_path, "path_key": f.path_key, "pre": f.pre, "post": f.post, "backup": None}
        | {"temp": f.temp}
    )
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    assert isinstance(decode(data), Unreadable)


def test_restore_temp_names_are_stable_and_within_name_max() -> None:
    assert restore_temp_name("main.bean", "s1-j7") == ".main.bean.ft-restore-s1-j7.tmp"
    long = "a" * 250
    assert len(restore_temp_name(long, "s1-j7").encode()) <= 255
    assert restore_temp_name(long, "s1-j7") == restore_temp_name(long, "s1-j7")
    assert restore_temp_name(long, "s1-j7") != temp_name(long, "s1-j7")


def test_kept_names_are_visible_numbered_within_name_max_and_recognised() -> None:
    """§6.4 as decided on 2026-10-29 (the owner's kept-name design): `<name>.kept-<job>`, then `-2` and on."""
    assert kept_name("main.bean", "s1-j7") == "main.bean.kept-s1-j7"
    assert kept_name("main.bean", "s1-j7", 2) == "main.bean.kept-s1-j7-2"
    long = "a" * 250
    assert len(kept_name(long, "s1-j7", 999).encode()) <= 255
    assert is_kept_name(kept_name(long, "s1-j7", 12), long, "s1-j7")
    assert is_kept_name("MAIN.BEAN.kept-s1-j7", "main.bean", "s1-j7")  # as a case-insensitive volume may spell it
    for other in (
        "main.bean",
        "main.bean.kept-s1-j8",
        "main.bean.kept-s1-j77",
        "main.bean.kept-s1-j7-x",
        "x.kept-s1-j7",
    ):
        assert not is_kept_name(other, "main.bean", "s1-j7"), other
    with pytest.raises(ValueError, match="kept_number"):
        kept_name("main.bean", "s1-j7", 1000)


def test_a_record_with_a_file_at_another_files_kept_name_is_unreadable() -> None:
    """Recovery stops while a kept copy exists, so a record whose file is another file's kept copy would never
    resolve."""
    a = RECORD.files[0]
    k = kept_name("a.bean", RECORD.name)
    at_kept = FileEntry(f"/w/L/inc/{k}", f"/w/l/inc/{k}", None, "4" * 64, None, temp_name(k, "s1-j7"))
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(replace(RECORD, files=(a, at_kept), created_dirs=()))


def test_every_derived_name_carries_the_session_and_the_job() -> None:
    """S0061 verifier r2 F1: job numbers restart in each session; a kept copy outlives its journal (and a temp
    outlives a journal the user chose to keep), so a name keyed by the job alone would meet a later session's save."""
    for name in (
        temp_name("main.bean", "s1-j7"),
        restore_temp_name("main.bean", "s1-j7"),
        kept_name("main.bean", "s1-j7"),
    ):
        assert "s1-j7" in name
    assert not is_kept_name(kept_name("main.bean", "s1-j7"), "main.bean", "s2-j7")
    assert temp_name("main.bean", "s1-j7") != temp_name("main.bean", "s2-j7")
    for bad in ("j7", "s1-j-7", "-j7", "s1-"):
        with pytest.raises(ValueError, match="tag"):
            temp_name("main.bean", bad)
    for name in (staging_name("main.bean", "s1-j7"), restore_staging_name("main.bean", "s1-j7")):
        assert "s1-j7" in name


def test_staging_names_are_stable_within_name_max_and_distinct_from_every_other_derived_name() -> None:
    """Owner, 2026-10-30: "Staging name (Recommended)": the new bytes are written first under `.<name>.ft-<session>-
    <job>.new` (a restore's under `.<name>.ft-restore-<session>-<job>.new`), never under a name a swap touches."""
    assert staging_name("main.bean", "s1-j7") == ".main.bean.ft-s1-j7.new"
    assert restore_staging_name("main.bean", "s1-j7") == ".main.bean.ft-restore-s1-j7.new"
    assert scratch_names("main.bean", "s1-j7") == (
        ".main.bean.ft-s1-j7.tmp",
        ".main.bean.ft-s1-j7.new",
        ".main.bean.ft-restore-s1-j7.tmp",
        ".main.bean.ft-restore-s1-j7.new",
    )
    for name in ("main.bean", "a" * 250, "a" * 255):
        names = scratch_names(name, "s1-j7")
        assert len(set(names)) == 4, name
        assert all(len(n.encode()) <= 255 for n in names), name
        assert kept_name(name, "s1-j7") not in names
        assert scratch_names(name, "s1-j7") == names


@pytest.mark.parametrize("which", [staging_name, restore_staging_name])
def test_a_record_with_a_file_at_another_files_staging_name_is_unreadable(which: Callable[[str, str], str]) -> None:
    """Recovery deletes what stands at a staging name whatever it holds (§6.4), so no file or folder of a record may
    sit at one."""
    a = RECORD.files[0]
    sn = which("a.bean", RECORD.name)
    at_sn = FileEntry(f"/w/L/inc/{sn}", f"/w/l/inc/{sn}", None, "4" * 64, None, temp_name(sn, "s1-j7"))
    bad = replace(RECORD, files=(a, at_sn), created_dirs=())
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(bad)
    obj = json.loads(encode(refit(replace(bad, files=bad.files[:1]))))
    f = bad.files[1]
    obj["files"].append(
        {"canonical_path": f.canonical_path, "path_key": f.path_key, "pre": f.pre, "post": f.post, "backup": None}
        | {"temp": f.temp}
    )
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    assert isinstance(decode(data), Unreadable)
    inside = FileEntry(
        f"/w/L/inc/{sn}/x.bean", f"/w/l/inc/{sn}/x.bean", None, "5" * 64, None, temp_name("x.bean", "s1-j7")
    )
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(replace(RECORD, files=(a, inside), created_dirs=(DirEntry(f"/w/L/inc/{sn}", f"/w/l/inc/{sn}"),)))


def test_a_record_with_a_created_folder_at_a_files_kept_name_is_unreadable() -> None:
    """S0061 red team r2 L2."""
    f = FileEntry("/w/L/new/x.bean", "/w/l/new/x.bean", None, "4" * 64, None, temp_name("x.bean", "s1-j7"))
    k = kept_name("a.bean", RECORD.name)
    g = FileEntry(f"/w/L/inc/{k}/y.bean", f"/w/l/inc/{k}/y.bean", None, "5" * 64, None, temp_name("y.bean", "s1-j7"))
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(replace(RECORD, files=(RECORD.files[0], g), created_dirs=(DirEntry(f"/w/L/inc/{k}", f"/w/l/inc/{k}"),)))
    assert f.temp.startswith(".x.bean")


def test_every_folder_the_undo_may_write_in_is_named_with_its_drive_and_birth() -> None:
    """S0089 (BL-0188, red team H1): `folders` names each file's folder, each folder the save makes and the folder it
    is made in. Files in different folders may be on different drives (an `include` on another drive)."""
    obj = json.loads(encode(RECORD))
    assert [f["canonical_path"] for f in obj["folders"]] == ["/w/L", "/w/L/inc", "/w/L/y2026"]
    assert folders(RECORD) == {
        "/w/L/inc": RECORD.folders[1],
        "/w/L/y2026": RECORD.folders[2],
        "/w/L": RECORD.folders[0],
    }
    assert next(iter(folders(RECORD))) == "/w/L/inc"  # the files' folders first, in the record's order
    elsewhere = replace(
        RECORD,
        folders=(RECORD.folders[0], replace(RECORD.folders[1], volume=OTHER_VOL, fs_type="msdos"), RECORD.folders[2]),
    )
    assert decode(encode(elsewhere)) == elsewhere


def test_a_folder_the_save_makes_is_on_the_drive_it_is_made_in() -> None:
    with pytest.raises(ValueError, match="volume_conflict"):
        encode(replace(RECORD, folders=(*RECORD.folders[:2], replace(RECORD.folders[2], volume=OTHER_VOL))))
    deep = FileEntry("/w/L/y2026/q/z.bean", "/w/l/y2026/q/z.bean", None, "5" * 64, None, temp_name("z.bean", "s1-j7"))
    with pytest.raises(ValueError, match="folders"):  # its folder `q` is neither recorded nor a folder the save makes
        encode(replace(RECORD, files=(RECORD.files[0], deep)))


def test_a_folder_made_before_1970_is_recorded() -> None:
    """S0089 round 2, verifier F3: HFS+ and APFS keep creation times before 1970 (negative); the record takes them."""
    rec = replace(RECORD, folders=(replace(RECORD.folders[0], born=-315_619_200_000_000_000), *RECORD.folders[1:]))
    assert decode(encode(rec)) == rec
