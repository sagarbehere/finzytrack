"""The in-memory file system's own semantics (`tests/fakes/memfs.py`; S0060), row by row from
`design/specs/engine-jobs.md` §13 "File-system assumptions": the write path's crash tests are only as strong as this
fake is faithful, in both directions (it must never make durable what a real disk could lose, and must reach every
state a power loss may leave)."""

from __future__ import annotations

import errno

import pytest

from tests.fakes.memfs import Crash, MemFS


def fs_with(*dirs: str, ci: bool = True) -> MemFS:
    fs = MemFS(case_insensitive=ci)
    for d in dirs:
        fs.outside_mkdirs(d)
    return fs


def outcomes(fs: MemFS, *paths: str) -> set[tuple[bytes | None, ...]]:
    return {tuple(s.file(p) for p in paths) for s in fs.power_loss_states()}


def test_a_created_file_is_durable_only_after_its_folders_sync() -> None:
    fs = fs_with("/d")
    with fs.open_dir("/d") as d:
        d.create("f", b"data", 0o644)
        assert outcomes(fs, "/d/f") == {(None,), (b"data",)}  # the entry kept or dropped; its data synced
        d.sync()
    assert outcomes(fs, "/d/f") == {(b"data",)}


def test_an_entry_can_be_kept_before_its_data() -> None:
    """A crash after the entry and before the data sync: a power loss may keep the entry with no or cut data."""
    fs = fs_with("/d")
    fs.inject(2, crash=True)  # create's effects: 0 entry, 1 write, 2 data sync
    with fs.open_dir("/d") as d, pytest.raises(Crash):
        d.create("f", b"data", 0o644)
    assert outcomes(fs, "/d/f") == {(None,), (b"",), (b"data",), (b"da",)}


def test_a_folders_sync_makes_every_earlier_operation_in_it_durable_and_none_in_another_folder() -> None:
    fs = fs_with("/a", "/b")
    with fs.open_dir("/a") as a, fs.open_dir("/b") as b:
        a.create("x", b"1", 0o644)
        b.create("y", b"2", 0o644)
        a.create("z", b"3", 0o644)
        a.sync()
    assert outcomes(fs, "/a/x", "/a/z", "/b/y") == {(b"1", b"3", None), (b"1", b"3", b"2")}


def test_unsynced_operations_are_kept_or_dropped_independently_of_their_order() -> None:
    """§13: no order between unsynced operations in one folder is assumed: a later one may survive an earlier one."""
    fs = fs_with("/d")
    with fs.open_dir("/d") as d:
        d.create("first", b"1", 0o644)
        d.create("second", b"2", 0o644)
    assert outcomes(fs, "/d/first", "/d/second") == {(None, None), (b"1", None), (None, b"2"), (b"1", b"2")}


def test_a_swap_is_kept_or_dropped_whole() -> None:
    fs = fs_with("/d")
    fs.outside_write("/d/a", b"A")
    fs.outside_write("/d/b", b"B")
    with fs.open_dir("/d") as d:
        d.swap("a", "b")
        assert fs.file("/d/a") == b"B"
    assert outcomes(fs, "/d/a", "/d/b") == {(b"A", b"B"), (b"B", b"A")}


def test_an_exclusive_rename_is_kept_or_dropped_whole_and_refuses_an_existing_name() -> None:
    fs = fs_with("/d")
    fs.outside_write("/d/t", b"T")
    fs.outside_write("/d/taken", b"X")
    with fs.open_dir("/d") as d:
        with pytest.raises(FileExistsError):
            d.rename_excl("t", "taken")
        d.rename_excl("t", "new")
    assert outcomes(fs, "/d/t", "/d/new") == {(b"T", None), (None, b"T")}


def test_a_rename_of_an_entry_whose_creation_was_dropped_does_nothing() -> None:
    """The journal's `.part` → `.open`: a power loss keeps `.open` only with its synced data."""
    fs = fs_with("/d")
    with fs.open_dir("/d") as d:
        d.create("j.part", b"record", 0o600)
        d.rename_excl("j.part", "j.open")
    assert outcomes(fs, "/d/j.part", "/d/j.open") == {(None, None), (b"record", None), (None, b"record")}


def test_removes_and_mkdirs_are_durable_only_after_the_sync() -> None:
    fs = fs_with("/d")
    fs.outside_write("/d/old", b"O")
    with fs.open_dir("/d") as d:
        d.remove("old")
        d.mkdir("sub")
    states = list(fs.power_loss_states())
    assert {(s.file("/d/old"), "/d/sub" in s.snapshot()) for s in states} == {
        (b"O", False),
        (None, False),
        (b"O", True),
        (None, True),
    }


def test_a_file_written_in_place_unsynced_may_be_old_new_or_cut() -> None:
    fs = fs_with("/d")
    fs.inject(1, crash=True, cut=2)
    with fs.open_dir("/d") as d, pytest.raises(Crash):
        d.create("f", b"abcdef", 0o644)
    assert fs.file("/d/f") == b"ab"  # the write cut at the offset
    assert outcomes(fs, "/d/f") == {(None,), (b"",), (b"ab",), (b"a",)}


def test_an_io_error_happens_before_its_effect_or_after_a_cut_write() -> None:
    fs = fs_with("/d")
    fs.inject(0, crash=False, err=errno.ENOSPC)
    with fs.open_dir("/d") as d:
        with pytest.raises(OSError, match="injected") as e:
            d.create("f", b"x", 0o644)
        assert e.value.errno == errno.ENOSPC
        assert d.read("f") is None
        fs.inject(len(fs.effects) + 1, crash=False, cut=1)
        with pytest.raises(OSError, match="injected"):
            d.create("g", b"xyz", 0o644)
        assert d.read("g") == b"x"


def test_a_crash_is_not_an_exception_the_code_under_test_could_catch() -> None:
    assert not issubclass(Crash, Exception)


def test_every_effect_is_numbered_and_labelled() -> None:
    fs = fs_with("/d")
    with fs.open_dir("/d") as d:
        d.create("f", b"x", 0o644)
        d.sync()
    assert fs.effects == ["create /d:f", "write /d:f", "fsync /d:f", "sync /d:"]


def test_names_compare_by_nfd_and_casefold_on_a_case_insensitive_volume_and_keep_the_stored_spelling() -> None:
    fs = fs_with("/d")
    fs.outside_write("/d/Café.bean", b"x")  # NFC é
    with fs.open_dir("/d") as d:
        assert d.read("CAFÉ.BEAN") == b"x"  # NFD, other case
        assert d.names() == ["Café.bean"]
        with pytest.raises(FileExistsError):
            d.create("café.bean", b"y", 0o644)
    assert fs.resolve("/D/café.bean").existing == ("d", "Café.bean")


def test_a_case_sensitive_volume_keeps_case_apart() -> None:
    fs = fs_with("/d", ci=False)
    fs.outside_write("/d/A", b"1")
    fs.outside_write("/d/a", b"2")
    assert (fs.file("/d/A"), fs.file("/d/a")) == (b"1", b"2")


def test_links_are_followed_by_resolve_and_open_dir_and_never_by_a_handles_read() -> None:
    fs = fs_with("/real")
    fs.outside_write("/real/f", b"x")
    fs.outside_symlink("/ln", "/real")
    fs.outside_symlink("/real/lf", "/real/f")
    assert fs.resolve("/ln/f").existing == ("real", "f")
    with fs.open_dir("/ln") as d:
        assert d.path() == "/real"
        with pytest.raises(OSError, match="link"):
            d.read("lf")


def test_a_moved_folder_changes_its_handles_path() -> None:
    fs = fs_with("/a/b")
    with fs.open_dir("/a/b") as d:
        fs.outside_move("/a/b", "/c")
        assert d.path() == "/c"


def test_resolve_stops_at_the_deepest_existing_ancestor() -> None:
    fs = fs_with("/a")
    r = fs.resolve("/A/new/f")
    assert (r.existing, r.rest) == (("a",), ("new", "f"))


def test_a_power_loss_state_is_itself_fully_durable() -> None:
    fs = fs_with("/d")
    with fs.open_dir("/d") as d:
        d.create("f", b"x", 0o644)
    for s in fs.power_loss_states():
        assert len(list(s.power_loss_states())) == 1
