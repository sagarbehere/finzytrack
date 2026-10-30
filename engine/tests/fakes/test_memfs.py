"""The in-memory file system's own semantics (`tests/fakes/memfs.py`; S0060), row by row from
`design/specs/engine-jobs.md` §13 "File-system assumptions": the write path's crash tests are only as strong as this
fake is faithful, in both directions (it must never make durable what a real disk could lose, and must reach every
state a power loss may leave)."""

from __future__ import annotations

import errno
from collections.abc import Callable

import pytest

from ledger_engine.fs.port import AclEntry, Right
from tests.fakes.memfs import Crash, MemDir, MemFS, MemLocks


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
    assert outcomes(fs, "/d/f") == {(None,), (b"",), (b"data",), (b"da",), (b"\0\0\0\0",), (b"\0\0ta",)}


def test_a_power_loss_can_tear_an_unsynced_file_other_than_as_a_prefix() -> None:
    """S0061 red team r3 F3: a real volume can keep an unsynced file's length with none of its blocks written (zeros,
    a hole), or its later blocks before its earlier ones; the fake reaches both, beside a cut-off prefix."""
    fs = fs_with("/d")
    fs.inject(2, crash=True)
    with fs.open_dir("/d") as d, pytest.raises(Crash):
        d.create("f", b"0123456789", 0o644)
    torn = {s.file("/d/f") for s in fs.power_loss_states()}
    assert b"\0" * 10 in torn  # the length, no data
    assert b"\0" * 5 + b"56789" in torn  # the tail before the head
    assert b"01234" in torn  # cut off


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
    assert outcomes(fs, "/d/f") == {(None,), (b"",), (b"ab",), (b"a",), (b"\0\0",), (b"\0b",)}


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


def test_a_dangling_link_resolves_as_absent_with_its_own_name_missing_as_on_macos() -> None:
    """S0060 verifier F3: the fake's resolution matches the macOS adapter's (`stat` sees a dangling link as absent)."""
    fs = fs_with("/w/L", "/w/far")
    fs.outside_symlink("/w/L/new.bean", "/w/far/missing.bean")
    r = fs.resolve("/w/L/new.bean")
    assert (r.existing, r.rest) == (("w", "L"), ("new.bean",))
    fs.outside_write("/w/far/missing.bean", b"x")
    assert fs.resolve("/w/L/new.bean").existing == ("w", "far", "missing.bean")


def test_hard_links_share_one_file_and_count_in_stat() -> None:
    fs = fs_with("/a", "/b")
    fs.outside_write("/a/f", b"1")
    fs.outside_link("/a/f", "/b/g")
    with fs.open_dir("/a") as a, fs.open_dir("/b") as b:
        sa, sb = a.stat("f"), b.stat("g")
        assert sa is not None
        assert sb is not None
        assert sa.identity == sb.identity
        assert sa.nlink == 2
    assert fs.resolve("/a/f").identity == fs.resolve("/b/g").identity
    assert fs.resolve("/a/f").existing == ("a", "f")  # each path resolves to itself, as F_GETPATH must not decide


def test_a_locked_file_refuses_swap_rename_and_remove() -> None:
    fs = fs_with("/d")
    fs.outside_write("/d/f", b"1")
    fs.outside_write("/d/g", b"2")
    fs.outside_lock("/d/f")
    with fs.open_dir("/d") as d:
        for act in (lambda: d.swap("f", "g"), lambda: d.rename_excl("f", "h"), lambda: d.remove("f")):
            with pytest.raises(PermissionError):
                act()
        s = d.stat("f")
        assert s is not None
        assert s.locked


def test_create_like_copies_extended_attributes() -> None:
    fs = fs_with("/d")
    fs.outside_write("/d/f", b"1")
    fs.outside_setxattr("/d/f", "k", b"v")
    with fs.open_dir("/d") as d:
        d.create("t", b"2", 0o600, like="f")
    assert fs.xattrs("/d/t") == {"k": b"v"}


def test_an_effect_can_happen_and_still_answer_an_error() -> None:
    fs = fs_with("/d")
    fs.outside_write("/d/a", b"A")
    fs.outside_write("/d/b", b"B")
    fs.inject(0, crash=False, err=errno.EIO, after=True)
    with fs.open_dir("/d") as d, pytest.raises(OSError, match="injected"):
        d.swap("a", "b")
    assert (fs.file("/d/a"), fs.file("/d/b")) == (b"B", b"A")


def test_a_fifo_is_neither_read_nor_opened() -> None:
    fs = fs_with("/d")
    fs.outside_fifo("/d/p")
    with fs.open_dir("/d") as d:
        s = d.stat("p")
        assert s is not None
        assert s.kind.value == "other"
        with pytest.raises(OSError, match="regular"):
            d.read("p")


# ---- S0061: rmdir, several faults, a volume storing NFD, locks -----------------------------------------------------


def test_rmdir_removes_only_an_empty_folder_and_is_durable_after_its_parents_sync() -> None:
    fs = fs_with("/d/empty", "/d/full")
    fs.outside_write("/d/full/.DS_Store", b"x")
    fs.outside_symlink("/d/link", "/d/empty")
    fs.outside_write("/d/file", b"f")
    with fs.open_dir("/d") as d:
        for name, err in (("full", errno.ENOTEMPTY), ("link", errno.ENOTDIR), ("file", errno.ENOTDIR)):
            with pytest.raises(OSError, match=r".") as e:
                d.rmdir(name)
            assert e.value.errno == err
        with pytest.raises(FileNotFoundError):
            d.rmdir("missing")
        d.rmdir("empty")
        assert "/d/empty" not in fs.snapshot()
        assert {"/d/empty" in s.snapshot() for s in fs.power_loss_states()} == {True, False}
        d.sync()
    assert {"/d/empty" in s.snapshot() for s in fs.power_loss_states()} == {False}


def test_several_faults_can_be_injected_each_firing_once() -> None:
    fs = fs_with("/d")
    fs.inject(0, crash=False)
    fs.inject(2, crash=False, err=errno.EIO)
    with fs.open_dir("/d") as d:
        with pytest.raises(OSError, match="injected") as e0:
            d.create("a", b"1", 0o644)  # effect 0: the entry fails, nothing more is done
        assert e0.value.errno == errno.ENOSPC
        with pytest.raises(OSError, match="injected") as e1:
            d.create("b", b"2", 0o644)  # effects 1, 2: the entry, then the write fails
        assert e1.value.errno == errno.EIO
        d.create("c", b"3", 0o644)  # no fault left
    assert fs.effects[:3] == ["create /d:a", "create /d:b", "write /d:b"]
    assert fs.file("/d/c") == b"3"


def test_a_volume_that_stores_nfd_keeps_every_name_in_nfd_and_finds_it_by_either_form() -> None:
    """HFS+ (probed, S0061): a name given in NFC is stored in NFD; a lookup in either form finds it."""
    fs = MemFS(stores_nfd=True)
    fs.outside_mkdirs("/d")
    with fs.open_dir("/d") as d:
        d.mkdir("Caf\u00e9")
        d.create("x\u00e9", b"1", 0o644)
        assert d.names() == ["Cafe\u0301", "xe\u0301"]
        assert d.stat("Cafe\u0301") is not None
        assert d.read("x\u00e9") == b"1"
    with fs.open_dir("/d/Caf\u00e9") as h:
        assert h.path() == "/d/Cafe\u0301"
    assert fs.names_in("/d") == ["Cafe\u0301", "xe\u0301"]


def test_the_fakes_locks_are_taken_without_waiting_and_released() -> None:
    locks = MemLocks()
    a = locks.try_lock("/k/a")
    assert a is not None
    assert locks.held == {"/k/a"}
    assert locks.try_lock("/k/a") is None, "held by ourselves: a second take does not wait either"
    locks.outside_hold("/k/b")
    assert locks.try_lock("/k/b") is None
    a.release()
    assert locks.held == set()
    assert locks.taken == ["/k/a", "/k/a", "/k/b"]


def test_a_removed_folders_handle_answers_its_old_path_as_apfs_does() -> None:
    """S0061 red team L5 (probed on APFS: `F_GETPATH` on a removed folder's descriptor answers the old path, and a sync
    of it succeeds)."""
    fs = fs_with("/d/gone")
    h = fs.open_dir("/d/gone")
    with fs.open_dir("/d") as d:
        d.rmdir("gone")
    assert h.path() == "/d/gone"
    h.sync()


def test_sync_file_makes_an_unsynced_in_place_write_durable_and_refuses_what_is_not_a_regular_file() -> None:
    """S0061 r4 (red team F2): an editor's in-place save not yet synced may be old, new or torn after a power loss;
    `sync_file` (`F_FULLFSYNC` on a read-only descriptor in the real adapter) makes it durable. It is one effect,
    never follows a link, and refuses a folder or a FIFO as `read` does."""
    fs = fs_with("/d", "/d/sub")
    fs.outside_write("/d/f", b"old bytes")
    fs.outside_write("/d/f", b"new bytes")
    fs.outside_unsynced("/d/f")
    assert len(outcomes(fs, "/d/f")) > 1
    fs.outside_symlink("/d/l", "/d/f")
    fs.outside_fifo("/d/p")
    with fs.open_dir("/d") as d:
        d.sync_file("f")
        assert fs.effects[-1] == "sync_file /d:f"
        for name in ("l", "sub", "p"):
            with pytest.raises(OSError, match=r"."):
                d.sync_file(name)
        with pytest.raises(FileNotFoundError):
            d.sync_file("missing")
    assert outcomes(fs, "/d/f") == {(b"new bytes",)}


def _deny(perms: str, flags: str = "", *, ours: bool = True) -> AclEntry:
    return AclEntry(True, ours, frozenset(perms.split(",")), frozenset(f for f in flags.split(",") if f))


def test_an_acl_entry_is_reported_and_new_entries_inherit_it_as_apfs_does() -> None:
    """S0061 r6 (red team overflow 4; probed on APFS): `acl()` answers the folder's entries; a new file gets the
    `file_inherit` ones (acting on it, passing nothing on), a new folder the `directory_inherit` ones (acting on it,
    `only_inherit` dropped); a `file_inherit`-only entry does not reach a subfolder; what existed before is
    untouched."""
    fs = MemFS()
    fs.outside_mkdirs("/d")
    fs.outside_write("/d/old", b"before\n")
    f_entry, d_entry = _deny("delete", "file_inherit"), _deny("search", "directory_inherit,only_inherit")
    with fs.open_dir("/d") as d:
        assert d.acl() == ()
        fs.outside_acl("/d", f_entry)
        fs.outside_acl("/d", d_entry)
        assert d.acl() == (f_entry, d_entry)
        d.create("new", b"x", 0o644)
        d.mkdir("sub")
        assert not d.allowed("new", Right.DELETE)
        assert d.allowed("old", Right.DELETE), "only what is made after it inherits it"
        with pytest.raises(PermissionError):
            d.remove("new")
    with fs.open_dir("/d/sub") as sub:
        assert sub.acl() == (
            AclEntry(True, True, frozenset({"search"}), frozenset({"directory_inherit", "inherited"})),
        )
        with pytest.raises(PermissionError):
            sub.create("x", b"x", 0o644)  # `deny search` acts on the subfolder: nothing can be looked up in it
    with pytest.raises(PermissionError):
        fs.resolve("/d/sub/x")
    fs.outside_acl_clear("/d")
    with fs.open_dir("/d") as d:
        assert d.acl() == ()


@pytest.mark.parametrize(
    ("perms", "flags", "ours", "refused", "rights"),
    [
        ("delete_child", "", True, ("remove", "rename", "rmdir"), (True, False)),
        ("add_file", "", True, ("create", "rename"), (False, True)),
        ("add_subdirectory", "", True, ("mkdir",), (False, True)),
        ("search", "", True, ("stat", "read", "create", "remove", "rename", "rmdir", "mkdir"), (False, False)),
        ("delete_child", "directory_inherit,only_inherit", True, (), (True, True)),  # only for what is made in it
        ("delete_child", "", False, (), (True, True)),  # another user's
    ],
)
def test_a_folders_deny_entry_refuses_what_it_names_and_nothing_else(
    perms: str, flags: str, ours: bool, refused: tuple[str, ...], rights: tuple[bool, bool]
) -> None:
    """The operations a folder's own deny entry refuses (the module's docstring), and the rights `allowed` answers
    as `access(2)` does on APFS (probed, S0061 r6; `rights`: write and search, `_RMFILE_OK`): `deny delete_child`
    leaves write and search allowed but not `_RMFILE_OK`; `deny add_file` the reverse; `deny search` both no."""
    actions: dict[str, Callable[[MemDir], object]] = {
        "remove": lambda d: d.remove("f"),
        "rename": lambda d: d.rename_excl("f", "f2"),
        "rmdir": lambda d: d.rmdir("e"),
        "create": lambda d: d.create("n", b"x", 0o644),
        "mkdir": lambda d: d.mkdir("m"),
        "names": lambda d: d.names(),
        "stat": lambda d: d.stat("f"),
        "read": lambda d: d.read("f"),
    }
    for action, do in actions.items():
        fs = MemFS()
        fs.outside_mkdirs("/d/e")
        fs.outside_write("/d/f", b"x")
        fs.outside_acl("/d", _deny(perms, flags, ours=ours))
        with fs.open_dir("/d") as d:
            if action in refused:
                with pytest.raises(PermissionError):
                    do(d)
            else:
                do(d)
    fs = MemFS()
    fs.outside_mkdirs("/d")
    fs.outside_acl("/d", _deny(perms, flags, ours=ours))
    with fs.open_dir("/d") as d:
        assert (d.allowed(".", Right.CHANGE_ENTRIES), d.allowed(".", Right.REMOVE_ENTRIES)) == rights


def test_a_folder_that_denies_us_list_cannot_be_opened() -> None:
    """Probed on APFS (S0061 r6): `open(O_RDONLY | O_DIRECTORY)` of a folder under `deny list` answers `EACCES`."""
    fs = MemFS()
    fs.outside_mkdirs("/d")
    fs.outside_acl("/d", _deny("list"))
    with pytest.raises(PermissionError):
        fs.open_dir("/d")


@pytest.mark.parametrize("perms", ["readattr", "readsecurity", "read", "readextattr", "delete"])
def test_an_entrys_own_deny_refuses_what_it_names(perms: str) -> None:
    """On a file (inherited from its folder, as red team r6 and verifier r6 F1 met it): `readattr` and `readsecurity`
    make its `stat` answer `EACCES` (probed on APFS), `read` its bytes, `readextattr` the copy of its extended
    attributes, `delete` its removal."""
    fs = MemFS()
    fs.outside_mkdirs("/d")
    fs.outside_write("/d/f", b"x")
    fs.outside_acl("/d/f", _deny(perms))
    with fs.open_dir("/d") as d:
        checks: dict[str, Callable[[], object]] = {
            "stat": lambda: d.stat("f"),
            "read": lambda: d.read("f"),
            "copy": lambda: d.create("g", b"y", 0o644, like="f"),
            "remove": lambda: d.remove("f"),
        }
        refused = {
            "readattr": {"stat"},
            "readsecurity": {"stat"},
            "read": {"read"},
            "readextattr": {"copy"},
            "delete": {"remove"},
        }[perms]
        assert d.allowed("f", Right.READ_XATTRS) is (perms != "readextattr")
        for name, check in checks.items():
            if name in refused:
                with pytest.raises(PermissionError):
                    check()
            elif name != "remove":
                check()


def test_a_new_file_that_inherits_deny_writeextattr_refuses_the_copy_of_extended_attributes() -> None:
    """Red team r6 overflow 2: `fcopyfile(COPYFILE_XATTR)` into a file whose inherited entry denies `writeextattr`."""
    fs = MemFS()
    fs.outside_mkdirs("/d")
    fs.outside_write("/d/f", b"x")
    fs.outside_acl("/d", _deny("writeextattr", "file_inherit"))
    with fs.open_dir("/d") as d, pytest.raises(PermissionError):
        d.create("g", b"y", 0o644, like="f")


def test_a_locked_folder_refuses_changes_in_it_and_its_own_removal() -> None:
    """`chflags uchg` on a folder (probed on APFS, S0061 r6): write and `_RMFILE_OK` answer no, every change in it
    `EPERM`, and its parent may not remove it."""
    fs = MemFS()
    fs.outside_mkdirs("/d/e")
    fs.outside_write("/d/e/f", b"x")
    fs.outside_mkdirs("/d/empty")
    fs.outside_lock("/d/e")
    fs.outside_lock("/d/empty")
    with fs.open_dir("/d/e") as e:
        assert not e.allowed(".", Right.CHANGE_ENTRIES)
        assert not e.allowed(".", Right.REMOVE_ENTRIES)
        for do in (lambda: e.remove("f"), lambda: e.create("g", b"y", 0o644), lambda: e.mkdir("m")):
            with pytest.raises(PermissionError):
                do()
    with fs.open_dir("/d") as d:
        st = d.stat("empty")
        assert st is not None
        assert st.locked
        assert not d.allowed("empty", Right.DELETE)
        with pytest.raises(PermissionError):
            d.rmdir("empty")
    fs.outside_unlock("/d/empty")
    with fs.open_dir("/d") as d:
        d.rmdir("empty")


def test_acl_entries_and_lock_flags_survive_a_power_loss() -> None:
    fs = MemFS()
    fs.outside_mkdirs("/d/e")
    fs.outside_acl("/d", _deny("delete_child"))
    fs.outside_lock("/d/e")
    for state in fs.power_loss_states():
        with state.open_dir("/d") as d:
            assert d.acl() == (_deny("delete_child"),)
            st = d.stat("e")
            assert st is not None
            assert st.locked


def test_the_user_removing_a_file_is_durable() -> None:
    fs = MemFS()
    fs.outside_mkdirs("/d")
    fs.outside_write("/d/f", b"x")
    fs.outside_remove("/d/f")
    assert fs.file("/d/f") is None
    assert all(state.file("/d/f") is None for state in fs.power_loss_states())
