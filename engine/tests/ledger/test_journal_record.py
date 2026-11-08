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
    Record,
    State,
    Unreadable,
    backup_name,
    decode,
    encode,
    file_name,
    is_kept_name,
    kept_name,
    parse_file_name,
    record_name,
    restore_staging_name,
    restore_temp_name,
    scratch_names,
    staging_name,
    temp_name,
    volumes,
)

pytestmark = pytest.mark.inv("INV-32")

VOL = "6d3f0a52-8b1c-4e7a-9f20-3c5b7d9e1a40"  # the drive /w/L is on (S0089, BL-0188)
OTHER_VOL = "0b9e2c61-4d7f-4a38-8e15-92a6f0c3d7b4"

RECORD = Record(
    session="s1",
    job="j7",
    workspace_key="k1",
    lineage="lin1",
    engine_build="build1",
    backup_dir="/app/local/k1/backups/s1-j7",
    files=(
        FileEntry("/w/L/inc/a.bean", "/w/l/inc/a.bean", "1" * 64, "2" * 64, "0-a.bean", ".a.bean.ft-s1-j7.tmp", VOL),
        FileEntry(
            "/w/L/y2026/Caf\u00e9.bean",
            "/w/l/y2026/cafe\u0301.bean",
            None,
            "3" * 64,
            None,
            ".Caf\u00e9.bean.ft-s1-j7.tmp",
            VOL,
        ),
    ),
    created_dirs=(DirEntry("/w/L/y2026", "/w/l/y2026", VOL),),
)


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


def test_format_5_has_no_prediction_and_older_formats_are_unreadable() -> None:
    """ADR-0068 D1 (S0070): format 4 dropped the post fingerprint and the adopted digest; format 5 (S0089) adds the
    drive of every folder the save writes in. A format 4 record (no drive), a format 3 one (S0067, with the
    prediction) or a format 2 one (S0066, with a parse digest too) is never read as a record of this format."""
    obj = json.loads(encode(RECORD))
    assert (VERSION, "post_fingerprint" in obj, "adopted_digest" in obj) == (5, False, False)
    four = {**obj, "v": 4, "files": [{k: v for k, v in f.items() if k != "volume"} for f in obj["files"]]}
    four["created_dirs"] = [{k: v for k, v in d.items() if k != "volume"} for d in obj["created_dirs"]]
    older = {**four, "v": 3, "post_fingerprint": "{}", "adopted_digest": "sha256:" + "a" * 64}
    for o in (four, older, {**older, "v": 2, "parse_digest": "sha256:" + "b" * 64}, {**obj, "v": 3}):
        out = decode((json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
        assert out == Unreadable("unknown version")
    for o in ({**four, "v": 5}, {**older, "v": 5}):  # an older record under the new version: not a whole record
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
    # S0089 (BL-0188; owner, 2026-11-08: "one without it is refused the same way"): every file and created folder
    # names its drive by a volume UUID, in lower case, never the nil UUID
    lambda o: o["files"][0].pop("volume"),
    lambda o: o["created_dirs"][0].pop("volume"),
    lambda o: o["files"][0].__setitem__("volume", None),
    lambda o: o["files"][0].__setitem__("volume", ""),
    lambda o: o["files"][0].__setitem__("volume", VOL.upper()),
    lambda o: o["files"][0].__setitem__("volume", VOL.replace("-", "")),
    lambda o: o["files"][0].__setitem__("volume", "00000000-0000-0000-0000-000000000000"),
    lambda o: o["files"][0].__setitem__("volume", VOL + "0"),
    lambda o: o["created_dirs"][0].__setitem__("volume", OTHER_VOL),  # a created folder not on its file's drive
    lambda o: o["files"][1].__setitem__("volume", OTHER_VOL),  # a file in a created folder on another drive
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
    twin = FileEntry("/w/L/inc/A.bean", "/w/L/inc/A.bean", "1" * 64, "2" * 64, "1-A.bean", ".A.bean.ft-s1-j7.tmp", VOL)
    with pytest.raises(ValueError, match="duplicate_temp"):
        encode(replace(RECORD, files=(a, twin), created_dirs=()))
    at_temp = FileEntry(
        "/w/L/inc/.a.bean.ft-s1-j7.tmp",
        "/w/l/inc/.a.bean.ft-s1-j7.tmp",
        None,
        "4" * 64,
        None,
        "..a.bean.ft-s1-j7.tmp.ft-s1-j7.tmp",
        VOL,
    )
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(replace(RECORD, files=(a, at_temp), created_dirs=()))
    for bad in (
        replace(RECORD, files=(a, twin), created_dirs=()),
        replace(RECORD, files=(a, at_temp), created_dirs=()),
    ):
        obj = json.loads(encode(replace(bad, files=bad.files[:1])))
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
    at_rtemp = FileEntry(f"/w/L/inc/{rtemp}", f"/w/l/inc/{rtemp}", None, "4" * 64, None, temp_name(rtemp, "s1-j7"), VOL)
    bad = replace(RECORD, files=(a, at_rtemp), created_dirs=())
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(bad)
    obj = json.loads(encode(replace(bad, files=bad.files[:1])))
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
    at_kept = FileEntry(f"/w/L/inc/{k}", f"/w/l/inc/{k}", None, "4" * 64, None, temp_name(k, "s1-j7"), VOL)
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
    at_sn = FileEntry(f"/w/L/inc/{sn}", f"/w/l/inc/{sn}", None, "4" * 64, None, temp_name(sn, "s1-j7"), VOL)
    bad = replace(RECORD, files=(a, at_sn), created_dirs=())
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(bad)
    obj = json.loads(encode(replace(bad, files=bad.files[:1])))
    f = bad.files[1]
    obj["files"].append(
        {"canonical_path": f.canonical_path, "path_key": f.path_key, "pre": f.pre, "post": f.post, "backup": None}
        | {"temp": f.temp}
    )
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    assert isinstance(decode(data), Unreadable)
    inside = FileEntry(
        f"/w/L/inc/{sn}/x.bean", f"/w/l/inc/{sn}/x.bean", None, "5" * 64, None, temp_name("x.bean", "s1-j7"), VOL
    )
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(replace(RECORD, files=(a, inside), created_dirs=(DirEntry(f"/w/L/inc/{sn}", f"/w/l/inc/{sn}", VOL),)))


def test_a_record_with_a_created_folder_at_a_files_kept_name_is_unreadable() -> None:
    """S0061 red team r2 L2."""
    f = FileEntry("/w/L/new/x.bean", "/w/l/new/x.bean", None, "4" * 64, None, temp_name("x.bean", "s1-j7"), VOL)
    k = kept_name("a.bean", RECORD.name)
    g = FileEntry(
        f"/w/L/inc/{k}/y.bean", f"/w/l/inc/{k}/y.bean", None, "5" * 64, None, temp_name("y.bean", "s1-j7"), VOL
    )
    with pytest.raises(ValueError, match="temp_is_a_path"):
        encode(
            replace(RECORD, files=(RECORD.files[0], g), created_dirs=(DirEntry(f"/w/L/inc/{k}", f"/w/l/inc/{k}", VOL),))
        )
    assert f.temp.startswith(".x.bean")


def test_each_file_and_created_folder_names_its_drive() -> None:
    """S0089 (BL-0188): the record names the drive of each file's folder and of each folder the save makes, so the
    undo can tell the drive at those paths now from the one the save wrote on. Files in different folders may be on
    different drives (an `include` on another drive); one folder is on one drive."""
    obj = json.loads(encode(RECORD))
    assert [f["volume"] for f in obj["files"]] == [VOL, VOL]
    assert [d["volume"] for d in obj["created_dirs"]] == [VOL]
    elsewhere = replace(RECORD.files[0], volume=OTHER_VOL)  # inc/ on another drive mounted there
    rec = replace(RECORD, files=(elsewhere, RECORD.files[1]))
    assert decode(encode(rec)) == rec
    assert volumes(rec) == {"/w/L/inc": OTHER_VOL, "/w/L/y2026": VOL, "/w/L": VOL}


def test_one_folder_on_two_drives_is_refused() -> None:
    """Two files of one folder, or a created folder and the files under it, naming different drives cannot come from
    one save: the record is refused (`volume_conflict`), so the undo never trusts either."""
    a = RECORD.files[0]
    b = FileEntry(
        "/w/L/inc/b.bean", "/w/l/inc/b.bean", "1" * 64, "2" * 64, "1-b.bean", temp_name("b.bean", "s1-j7"), OTHER_VOL
    )
    with pytest.raises(ValueError, match="volume_conflict"):
        encode(replace(RECORD, files=(a, b), created_dirs=()))
    with pytest.raises(ValueError, match="volume_conflict"):
        encode(replace(RECORD, created_dirs=(replace(RECORD.created_dirs[0], volume=OTHER_VOL),)))


def test_a_file_deep_under_a_created_folder_on_another_drive_is_refused() -> None:
    """A file two levels under a folder the save made is on that folder's drive, even when the folder between them is
    not named (a record no save of ours writes): refused `volume_conflict` (S0089 mutant `created-dir-conflict`)."""
    deep = FileEntry(
        "/w/L/y2026/q/z.bean", "/w/l/y2026/q/z.bean", None, "5" * 64, None, temp_name("z.bean", "s1-j7"), OTHER_VOL
    )
    with pytest.raises(ValueError, match="volume_conflict"):
        encode(replace(RECORD, files=(RECORD.files[0], deep)))
    assert decode(encode(replace(RECORD, files=(RECORD.files[0], replace(deep, volume=VOL)))))
