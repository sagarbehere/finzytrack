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
    parse_file_name,
    record_name,
    restore_temp_name,
    temp_name,
)

pytestmark = pytest.mark.inv("INV-32")

RECORD = Record(
    session="s1",
    job="j7",
    workspace_key="k1",
    lineage="lin1",
    engine_build="build1",
    post_fingerprint="sha256:" + "f" * 64,
    adopted_digest="sha256:" + "a" * 64,
    parse_digest="sha256:" + "b" * 64,
    backup_dir="/app/local/k1/backups/s1-j7",
    files=(
        FileEntry("/w/L/inc/a.bean", "/w/l/inc/a.bean", "1" * 64, "2" * 64, "0-a.bean", ".a.bean.ft-j7.tmp"),
        FileEntry(
            "/w/L/y2026/Caf\u00e9.bean", "/w/l/y2026/cafe\u0301.bean", None, "3" * 64, None, ".Caf\u00e9.bean.ft-j7.tmp"
        ),
    ),
    created_dirs=(DirEntry("/w/L/y2026", "/w/l/y2026"),),
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
        "s1-j7.done",
        "s1-j7.mismatch",
        "s1-j7.resolved",
        "s1-j7.kept",
    ]
    for s in State:
        assert parse_file_name(file_name("s1-j7", s)) == ("s1-j7", s)
    for other in ("s1-j7", "s1-j7.tmp", "s1.open", ".s1-j7.open", "s 1-j7.open", "s1-j-7.open", "-j7.open"):
        assert parse_file_name(other) is None, other
    assert temp_name("main.beancount", "j7") == ".main.beancount.ft-j7.tmp"


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
    for derived in (temp_name(long, "j7"), backup_name(10, long)):
        assert len(derived.encode()) <= 255
        assert "é" not in derived  # shortened: the full name would pass 255 bytes
    assert backup_name(3, long) == f"3-{long}"  # 255 bytes: fits, kept readable
    assert temp_name(long, "j7") == temp_name(long, "j7")
    assert backup_name(0, "a.bean") == "0-a.bean"
    rec = replace(
        RECORD,
        files=(
            replace(
                RECORD.files[0],
                canonical_path=f"/w/L/inc/{long}",
                path_key=f"/w/l/inc/{long}",
                temp=temp_name(long, "j7"),
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
    twin = FileEntry("/w/L/inc/A.bean", "/w/L/inc/A.bean", "1" * 64, "2" * 64, "1-A.bean", ".A.bean.ft-j7.tmp")
    with pytest.raises(ValueError, match="duplicate_temp"):
        encode(replace(RECORD, files=(a, twin), created_dirs=()))
    at_temp = FileEntry(
        "/w/L/inc/.a.bean.ft-j7.tmp", "/w/l/inc/.a.bean.ft-j7.tmp", None, "4" * 64, None, "..a.bean.ft-j7.tmp.ft-j7.tmp"
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
    """S0061: recovery deletes restore temps without a check (§6.4), so no file or folder of a record may sit at one."""
    a = RECORD.files[0]
    rtemp = restore_temp_name("a.bean", RECORD.job)
    assert rtemp == ".a.bean.ft-restore-j7.tmp"
    at_rtemp = FileEntry(f"/w/L/inc/{rtemp}", f"/w/l/inc/{rtemp}", None, "4" * 64, None, temp_name(rtemp, "j7"))
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
    assert restore_temp_name("main.bean", "j7") == ".main.bean.ft-restore-j7.tmp"
    long = "a" * 250
    assert len(restore_temp_name(long, "j7").encode()) <= 255
    assert restore_temp_name(long, "j7") == restore_temp_name(long, "j7")
    assert restore_temp_name(long, "j7") != temp_name(long, "j7")
