"""The file-system port on Linux (`ledger_engine.fs.linux`, through `ledger_engine.fs.this_os`; S0125, BL-0266;
`design/specs/engine-jobs.md` §6.2, §6.3, §6.8, §15): the primitives the durable order relies on, on the runner's real
disk (temp folders only, INV-24), and, opt-in, on scratch volumes the manual CI run formats (`FT_TEST_VOLUMES`:
vfat, exFAT, ext2, btrfs, xfs and a case-folding ext4; `ci.yml`, the engine job).

The ownership rule (owner, 2026-11-16: "Rule unchanged (Recommended)"): the Linux port writes exactly what the macOS
port writes; only the primitives differ, and where one cannot be given the port refuses or takes macOS's stated mode.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from ledger_engine.fs.port import FileSystem, FoldRule, Kind, Right
from ledger_engine.fs.this_os import for_this_os, legacy_locks_for_this_os
from ledger_engine.ledger.writer import Change, Landed, NotSaved, durable_commit, sha256
from tests.fs.real_volume import commit, real_volume_run, settled_mkdir

LINUX_ONLY = "the Linux adapter's own tests (macOS: tests/fs/test_darwin.py)"

if sys.platform != "linux":
    raise pytest.skip.Exception(LINUX_ONLY, allow_module_level=True)

pytestmark = pytest.mark.inv("INV-01")

UUID_SHAPE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SWAP_TYPES = frozenset({"ext4", "ext3", "xfs", "btrfs", "tmpfs", "f2fs"})


@pytest.fixture
def fs() -> FileSystem:
    return for_this_os()


def _findmnt(path: Path, column: str) -> str:
    return subprocess.run(  # noqa: S603 (a system tool, no shell)
        ["findmnt", "-n", "-o", column, "-T", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _bounded(fn: Callable[[], object]) -> bool:
    """Run `fn` on a thread; True when it ended. A hang detector only (the call ends in microseconds)."""
    done = threading.Event()

    def run() -> None:
        with contextlib.suppress(OSError):
            fn()
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return done.wait(10)


# -- the primitives, as on macOS ---------------------------------------------------------------------------------------


def test_swap_exchanges_two_files_in_one_operation(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    ino_a = (tmp_path / "a").stat().st_ino
    with fs.open_dir(str(tmp_path)) as d:
        d.swap("a", "b")
    assert ((tmp_path / "a").read_bytes(), (tmp_path / "b").read_bytes()) == (b"B", b"A")
    assert (tmp_path / "b").stat().st_ino == ino_a


def test_swap_needs_both_names_and_an_exclusive_rename_refuses_an_existing_name(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    with fs.open_dir(str(tmp_path)) as d:
        with pytest.raises(FileNotFoundError):
            d.swap("a", "missing")
        with pytest.raises(FileExistsError):
            d.rename_excl("a", "b")
        d.rename_excl("a", "c")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b", "c"]
    assert ((tmp_path / "b").read_bytes(), (tmp_path / "c").read_bytes()) == (b"B", b"A")


def test_create_is_exclusive_keeps_the_mode_asked_for_whatever_the_umask_and_refuses_a_link(
    fs: FileSystem, tmp_path: Path
) -> None:
    (tmp_path / "target").write_bytes(b"t")
    (tmp_path / "link").symlink_to(tmp_path / "target")
    old = os.umask(0o077)
    try:
        with fs.open_dir(str(tmp_path)) as d:
            ident = d.create("f", b"data", 0o644)
            with pytest.raises(FileExistsError):
                d.create("f", b"other", 0o644)
            with pytest.raises(FileExistsError):
                d.create("link", b"other", 0o644)
    finally:
        os.umask(old)
    st = (tmp_path / "f").stat()
    assert (stat.S_IMODE(st.st_mode), (tmp_path / "f").read_bytes()) == (0o644, b"data")
    assert ident == (st.st_dev, st.st_ino)
    assert (tmp_path / "target").read_bytes() == b"t"


def test_a_streamed_create_writes_every_chunk_in_order(fs: FileSystem, tmp_path: Path) -> None:
    with fs.open_dir(str(tmp_path)) as d:
        d.create("f", iter([b"one ", b"", b"two"]), 0o600)
    assert (tmp_path / "f").read_bytes() == b"one two"


def test_read_refuses_a_link_a_directory_and_a_fifo_without_blocking_and_answers_none_for_no_entry(
    fs: FileSystem, tmp_path: Path
) -> None:
    (tmp_path / "f").write_bytes(b"0123456789")
    (tmp_path / "link").symlink_to(tmp_path / "f")
    (tmp_path / "dir").mkdir()
    os.mkfifo(tmp_path / "fifo")
    with fs.open_dir(str(tmp_path)) as d:
        assert d.read("missing") is None
        assert d.read("f", 4) == b"0123"
        assert d.sha256("f", 4) == (sha256(b"0123"), 4)
        for name in ("link", "dir"):
            with pytest.raises(OSError):  # noqa: PT011 (any refusal)
                d.read(name)
        assert _bounded(lambda: d.read("fifo"))
        st = d.stat("fifo")
        assert st is not None
        assert st.kind is Kind.OTHER
        with pytest.raises(OSError):  # noqa: PT011
            d.read("fifo")


def test_read_identified_and_sync_file_name_the_object_they_met(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    st = (tmp_path / "f").stat()
    with fs.open_dir(str(tmp_path)) as d:
        assert d.read_identified("f") == (b"x", (st.st_dev, st.st_ino))
        assert d.sync_file("f") == (st.st_dev, st.st_ino)
        with pytest.raises(FileNotFoundError):
            d.sync_file("missing")


def test_a_handles_path_follows_its_folder_when_it_moves(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with fs.open_dir(str(tmp_path / "a")) as d:
        assert d.path() == str(tmp_path / "a")
        (tmp_path / "a").rename(tmp_path / "b")
        (tmp_path / "a").symlink_to(tmp_path / "b")
        assert d.path() == str(tmp_path / "b")


def test_a_removed_folders_path_is_its_last_path_as_on_macos(fs: FileSystem, tmp_path: Path) -> None:
    """macOS's `F_GETPATH` answers a removed folder's last path (probed, S0125); Linux's `/proc/self/fd` link adds
    " (deleted)", which the adapter drops only for a folder that has no link left (`st_nlink` 0), so a live folder
    whose own name ends so keeps it."""
    (tmp_path / "x").mkdir()
    (tmp_path / "y (deleted)").mkdir()
    with fs.open_dir(str(tmp_path / "x")) as d, fs.open_dir(str(tmp_path / "y (deleted)")) as e:
        (tmp_path / "x").rmdir()
        assert d.path() == str(tmp_path / "x")
        assert e.path() == str(tmp_path / "y (deleted)")
        with pytest.raises(FileNotFoundError):
            d.create("f", b"", 0o600)


def test_open_dir_through_a_link_reports_the_real_folder_and_resolve_gives_the_canonical_path(
    fs: FileSystem, tmp_path: Path
) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "f").write_bytes(b"")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with fs.open_dir(str(tmp_path / "link")) as d:
        assert d.path() == str(tmp_path / "real")
    r = fs.resolve(str(tmp_path / "link" / "f"))
    assert "/" + "/".join(r.existing) == str(tmp_path / "real" / "f")
    assert r.rest == ()
    r2 = fs.resolve(str(tmp_path / "link" / "new" / "g"))
    assert (r2.rest, "/" + "/".join(r2.existing)) == (("new", "g"), str(tmp_path / "real"))


def test_names_are_case_sensitive_and_never_normalized_on_a_linux_disk(fs: FileSystem, tmp_path: Path) -> None:
    """§6.3: Linux folds no names except in a case-folding folder or on a folding mount; the runner's disk folds
    none, so `Main.bean` and `main.bean` are two files and NFC and NFD spellings are two names."""
    (tmp_path / "Main.bean").write_bytes(b"A")
    (tmp_path / "main.bean").write_bytes(b"B")
    nfc, nfd = "é.bean", "é.bean"
    (tmp_path / nfc).write_bytes(b"C")
    (tmp_path / nfd).write_bytes(b"D")
    with fs.open_dir(str(tmp_path)) as d:
        assert d.rule() == FoldRule(nfd=False, casefold=False)
    r = fs.resolve(str(tmp_path / "main.bean"))
    assert r.existing[-1] == "main.bean"
    assert set(r.rules) == {FoldRule(nfd=False, casefold=False)}
    assert fs.resolve(str(tmp_path / nfd)).existing[-1] == nfd


def test_a_hard_linked_file_has_one_identity_and_each_name_its_own_canonical_path(
    fs: FileSystem, tmp_path: Path
) -> None:
    """S0060 red team 1, on Linux: the canonical path is the name asked for, read from its folder's listing, however
    recently the other name was looked up (the `/proc/self/fd` link of a file would name either)."""
    (tmp_path / "Ledger").mkdir()
    (tmp_path / "Other").mkdir()
    (tmp_path / "Ledger" / "Main.beancount").write_bytes(b"x")
    os.link(tmp_path / "Ledger" / "Main.beancount", tmp_path / "Other" / "copy.beancount")
    for _ in range(10):
        os.stat(tmp_path / "Other" / "copy.beancount")
        main = fs.resolve(f"{tmp_path}/Ledger/Main.beancount")
        assert main.existing[-2:] == ("Ledger", "Main.beancount")
        os.stat(tmp_path / "Ledger" / "Main.beancount")
        other = fs.resolve(f"{tmp_path}/Other/copy.beancount")
        assert other.existing[-2:] == ("Other", "copy.beancount")
        assert main.identity is not None
        assert main.identity == other.identity


def test_a_dangling_link_resolves_as_absent_with_its_own_name_missing(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "new.beancount").symlink_to(tmp_path / "nowhere" / "x.beancount")
    r = fs.resolve(f"{tmp_path}/new.beancount")
    assert r.rest == ("new.beancount",)
    assert "/" + "/".join(r.existing) == str(tmp_path)


def test_a_folder_that_may_be_searched_but_not_read_still_resolves(fs: FileSystem, tmp_path: Path) -> None:
    """The resolve walk opens a folder read-only, and with `O_PATH` where it may only be searched (mode 0311)."""
    if os.geteuid() == 0:
        pytest.skip("root passes every mode check")
    (tmp_path / "s" / "inner").mkdir(parents=True)
    (tmp_path / "s").chmod(0o311)
    try:
        r = fs.resolve(f"{tmp_path}/s/inner")
    finally:
        (tmp_path / "s").chmod(0o755)
    assert ("/" + "/".join(r.existing), r.rest) == (str(tmp_path / "s" / "inner"), ())


def test_resolve_refuses_relative_paths_and_dots(fs: FileSystem, tmp_path: Path) -> None:
    for bad in ("rel/x", str(tmp_path) + "/../x", str(tmp_path) + "/./x"):
        with pytest.raises(ValueError, match="malformed"):
            fs.resolve(bad)


def test_rename_remove_mkdir_rmdir_symlink_touch_and_open_child(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    (tmp_path / "real").mkdir()
    with fs.open_dir(str(tmp_path)) as d:
        d.rename("a", "b")  # a plain rename replaces its target
        d.mkdir("m", 0o700)
        d.symlink("b", "ln")
        assert d.readlink("ln") == "b"
        assert d.readlink("missing") is None
        st = d.stat("ln")
        assert st is not None
        assert st.kind is Kind.LINK
        os.utime(tmp_path / "b", ns=(1, 1))
        d.touch("ln")
        assert (tmp_path / "b").stat().st_mtime_ns == 1, "touch never follows a link"
        with d.open_child("real") as c:
            assert c.path() == str(tmp_path / "real")
        with pytest.raises(OSError):  # noqa: PT011 (a link is never followed)
            d.open_child("ln")
        (tmp_path / "m" / "f").write_bytes(b"")
        with pytest.raises(OSError) as full:  # noqa: PT011 (the errno is checked)
            d.rmdir("m")
        assert full.value.errno == errno.ENOTEMPTY
        (tmp_path / "m" / "f").unlink()
        d.rmdir("m")
        d.remove("ln")
        assert d.names() == ["b", "real"]
    assert (tmp_path / "b").read_bytes() == b"A"


def test_stat_gives_the_entrys_own_modification_time_not_a_links_target(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"")
    os.utime(tmp_path / "f", ns=(5, 5))
    (tmp_path / "ln").symlink_to("f")
    os.utime(tmp_path / "ln", ns=(7, 7), follow_symlinks=False)
    with fs.open_dir(str(tmp_path)) as d:
        f, ln = d.stat("f"), d.stat("ln")
    assert f is not None
    assert ln is not None
    assert (f.mtime_ns, ln.mtime_ns, f.locked, ln.kind) == (5, 7, False, Kind.LINK)


# -- the durable sync (§6.2): `fsync` on files and folders -------------------------------------------------------------


def test_every_sync_is_an_fsync_of_the_descriptor_and_an_error_is_never_hidden(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.fsync
    synced: list[int] = []

    def spy(fd: int) -> None:
        synced.append(os.fstat(fd).st_ino)
        real(fd)

    monkeypatch.setattr("ledger_engine.fs.linux.os.fsync", spy)
    with fs.open_dir(str(tmp_path)) as d:
        d.create("f", b"x", 0o600)
        d.sync()
        d.sync_file("f")
    ino_f, ino_dir = (tmp_path / "f").stat().st_ino, tmp_path.stat().st_ino
    assert synced == [ino_f, ino_dir, ino_f]

    def failing(fd: int) -> None:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr("ledger_engine.fs.linux.os.fsync", failing)
    with fs.open_dir(str(tmp_path)) as d, pytest.raises(OSError) as e:  # noqa: PT011 (the errno is checked)
        d.sync()
    assert e.value.errno == errno.EIO


# -- the swap and the mode without it (§6.8) ---------------------------------------------------------------------------


def test_the_runners_disk_offers_the_swap_and_the_exclusive_rename(fs: FileSystem, tmp_path: Path) -> None:
    with fs.open_dir(str(tmp_path)) as d:
        kind = d.mark().fs_type
        caps = d.caps()
    assert kind in SWAP_TYPES, kind
    assert (caps.swap, caps.excl) == (True, True)


def test_a_save_on_a_volume_without_renameat2s_exchange_takes_the_best_effort_path(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance (S0125 brief): a volume the adapter does not know to offer `RENAME_EXCHANGE` (an injected type list
    without the runner's file system) is §6.8's case: the save lands by a plain rename, with the backup and the
    read-back, and never calls `renameat2`."""
    calls: list[int] = []

    def no_rename2(dfd: int, a: str, b: str, flags: int) -> None:
        calls.append(flags)
        raise AssertionError

    monkeypatch.setattr("ledger_engine.fs.linux.SWAP_TYPES", frozenset[str]())
    monkeypatch.setattr("ledger_engine.fs.linux._renameat2", no_rename2)
    main = tmp_path / "L" / "main.bean"
    main.parent.mkdir()
    main.write_bytes(b"pre\n")
    with fs.open_dir(str(main.parent)) as d:
        assert (d.caps().swap, d.caps().excl) == (False, False)
    out = durable_commit(fs, commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, Landed), out
    assert main.read_bytes() == b"post\n"
    assert calls == []
    backups = [p for p in (tmp_path / "local" / "backups").rglob("*") if p.is_file()]
    assert [p.read_bytes() for p in backups] == [b"pre\n"]


@pytest.mark.parametrize("refusal", [errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP])
def test_a_swap_the_volume_refuses_after_all_is_not_saved_and_changes_nothing(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: int
) -> None:
    """A volume of a swap type that still refuses `RENAME_EXCHANGE` (an old kernel, an odd mount): nothing is done by
    the call, so the save rolls back and says "not saved"; never a silent plain rename."""
    from ledger_engine.fs import this_os

    def refusing(dfd: int, a: str, b: str, flags: int) -> None:
        raise OSError(refusal, os.strerror(refusal))

    monkeypatch.setattr("ledger_engine.fs.linux._renameat2", refusing)
    main = tmp_path / "L" / "main.bean"
    main.parent.mkdir()
    main.write_bytes(b"pre\n")
    out = durable_commit(this_os.for_this_os(), commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, NotSaved), out
    assert out.unchanged is True
    assert main.read_bytes() == b"pre\n"
    assert sorted(p.name for p in main.parent.iterdir()) == ["main.bean"]


# -- drive identity (BL-0218, Linux) and the folder's mark -------------------------------------------------------------


def test_volume_id_is_the_file_systems_uuid_where_the_system_reports_one(fs: FileSystem, tmp_path: Path) -> None:
    """The runner's disk is a block device's file system with a UUID (`findmnt`'s `UUID`, from `blkid`)."""
    want = _findmnt(tmp_path, "UUID").lower()
    with fs.open_dir(str(tmp_path)) as d:
        got = d.volume_id()
    if not want:
        pytest.skip("this runner's temp folder has no file-system UUID")
    assert got == want
    assert re.fullmatch(UUID_SHAPE, want)


def test_mark_reads_the_file_system_type_and_the_folders_creation_time(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    with fs.open_dir(str(tmp_path / "x")) as d:
        m = d.mark()
    assert m.fs_type == _findmnt(tmp_path, "FSTYPE")
    assert m.born is not None
    out = subprocess.run(  # noqa: S603 (coreutils' stat: the birth time in whole seconds)
        ["stat", "-c", "%W", str(tmp_path / "x")],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert m.born // 1_000_000_000 == int(out)


def test_a_folder_made_again_at_the_same_path_is_another_folder_even_with_the_same_inode_number(
    fs: FileSystem, tmp_path: Path
) -> None:
    """BL-0262: ext4 gives a folder removed and made again at once the freed inode number (the Linux CI run of
    2026-11-13); its creation time tells the two apart."""
    seen: set[tuple[int, int | None]] = set()
    for _ in range(5):
        (tmp_path / "x").mkdir()
        with fs.open_dir(str(tmp_path / "x")) as d:
            seen.add((d.identity()[1], d.mark().born))
        (tmp_path / "x").rmdir()
    assert len(seen) == 5


def test_the_app_folders_identity_carries_its_creation_time_and_tells_a_folder_made_again_apart(tmp_path: Path) -> None:
    """BL-0262: `this_os.folder_identity` is `(st_dev, st_ino, birth time)` on Linux, the birth time `mark` reads (and
    coreutils' `stat %W` to the second); a folder removed and made again at once is another identity even where ext4
    gives it the same inode number."""
    from ledger_engine.fs.port import FolderId, same_identity
    from ledger_engine.fs.this_os import folder_identity

    seen: list[FolderId] = []
    for _ in range(3):
        (tmp_path / "app").mkdir()
        fd = os.open(tmp_path / "app", os.O_RDONLY | os.O_DIRECTORY)
        try:
            ident = folder_identity(fd)
        finally:
            os.close(fd)
        st = (tmp_path / "app").stat()
        assert ident[:2] == (st.st_dev, st.st_ino)
        assert len(ident) == 3
        with for_this_os().open_dir(str(tmp_path / "app")) as d:
            assert d.mark().born == ident[2]
        assert not any(same_identity(ident, before) for before in seen)
        seen.append(ident)
        (tmp_path / "app").rmdir()


def test_the_path_guard_compares_bytes_on_linux(fs: FileSystem) -> None:
    """§6.3: Linux gives a path back as stored, so a folder renamed to another spelling of its name (NFC to NFD, or
    case) has moved; macOS compares after NFD (`port.same_path`)."""
    assert fs.same_path("/a/\u00e9", "/a/\u00e9")
    assert not fs.same_path("/a/\u00e9", "/a/e\u0301")
    assert not fs.same_path("/a/Main", "/a/main")


# -- rights (§6.1 step 8 "folder rule"): POSIX modes, and ACLs as the system applies them ------------------------------


def test_a_read_only_folder_denies_changing_and_removing_entries(fs: FileSystem, tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root passes every mode check")
    folder = tmp_path / "L"
    folder.mkdir()
    (folder / "f").write_bytes(b"")
    (folder / "f").chmod(0o000)
    folder.chmod(0o555)
    try:
        with fs.open_dir(str(folder)) as d:
            assert d.allowed(".", Right.SEARCH)
            assert not d.allowed(".", Right.CHANGE_ENTRIES)
            assert not d.allowed(".", Right.REMOVE_ENTRIES)
            assert not d.allowed("f", Right.DELETE)
            assert not d.allowed("f", Right.READ_XATTRS)
            assert d.acl() == ()
    finally:
        folder.chmod(0o755)
    with fs.open_dir(str(folder)) as d:
        assert d.allowed(".", Right.CHANGE_ENTRIES)
        assert d.allowed(".", Right.REMOVE_ENTRIES)
        assert d.allowed("f", Right.DELETE)
        assert not d.allowed("f", Right.READ_XATTRS)


def _sudo() -> bool:
    if shutil.which("sudo") is None or shutil.which("chattr") is None:
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode == 0  # noqa: S607


@contextlib.contextmanager
def _chattr(path: Path, flag: str) -> Generator[None]:
    subprocess.run(["sudo", "-n", "chattr", f"+{flag}", str(path)], check=True)  # noqa: S603, S607
    try:
        yield
    finally:
        subprocess.run(["sudo", "-n", "chattr", f"-{flag}", str(path)], check=True)  # noqa: S603, S607


@pytest.mark.skipif(not _sudo(), reason="needs sudo and chattr (the CI runner has both)")
def test_an_immutable_or_append_only_file_is_locked_and_refuses_the_save_before_anything_is_written(
    fs: FileSystem, tmp_path: Path
) -> None:
    main = tmp_path / "L" / "main.bean"
    main.parent.mkdir()
    main.write_bytes(b"pre\n")
    for flag in ("i", "a"):
        with _chattr(main, flag):
            with fs.open_dir(str(main.parent)) as d:
                st = d.stat("main.bean")
                assert st is not None
                assert st.locked
                assert not d.allowed("main.bean", Right.DELETE)
            out = durable_commit(fs, commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
            assert isinstance(out, NotSaved), out
            assert out.unchanged is True
        assert main.read_bytes() == b"pre\n"
        assert sorted(p.name for p in main.parent.iterdir()) == ["main.bean"]


@pytest.mark.skipif(not _sudo(), reason="needs sudo and chattr (the CI runner has both)")
def test_an_append_only_folder_denies_removing_entries(fs: FileSystem, tmp_path: Path) -> None:
    folder = tmp_path / "L"
    folder.mkdir()
    (folder / "f").write_bytes(b"")
    with _chattr(folder, "a"), fs.open_dir(str(folder)) as d:
        assert d.allowed(".", Right.SEARCH)
        assert not d.allowed(".", Right.REMOVE_ENTRIES)
        assert not d.allowed("f", Right.DELETE)


def test_a_replaced_file_keeps_its_user_extended_attributes_and_mode(fs: FileSystem, tmp_path: Path) -> None:
    main = tmp_path / "L" / "main.bean"
    main.parent.mkdir()
    main.write_bytes(b"pre\n")
    main.chmod(0o640)
    try:
        os.setxattr(main, "user.ft-test", b"tag")
    except OSError as e:
        if e.errno == errno.EOPNOTSUPP:
            pytest.skip("this folder's file system keeps no user extended attributes")
        raise
    out = durable_commit(fs, commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, Landed), out
    assert main.read_bytes() == b"post\n"
    assert os.getxattr(main, "user.ft-test") == b"tag"
    assert stat.S_IMODE(main.stat().st_mode) == 0o640


# -- the legacy app's lock (INV-26): `flock`, as portalocker takes it on Linux -----------------------------------------


def test_the_legacy_lock_is_flock_and_sees_a_holder(tmp_path: Path) -> None:
    import fcntl

    from ledger_engine.fs.port import LegacyAbsent, LegacyHeld

    locks = legacy_locks_for_this_os()
    lock = tmp_path / ".write.lock"
    assert isinstance(locks.try_lock(str(lock)), LegacyAbsent)
    assert not lock.exists(), "never created"
    lock.write_bytes(b"")
    fd = os.open(lock, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert isinstance(locks.try_lock(str(lock)), LegacyHeld)
    finally:
        os.close(fd)
    held = locks.try_lock(str(lock))
    assert not isinstance(held, LegacyAbsent | LegacyHeld)
    held.release()


# -- real scratch volumes (opt-in: `FT_TEST_VOLUMES`, made by the manual CI run) ---------------------------------------


def _volumes() -> list[str]:
    return [v for v in os.environ.get("FT_TEST_VOLUMES", "").split(":") if v]


SERIAL_NAMESPACE = uuid.UUID("3b1c6a52-8f0e-5d6a-9b1e-6c4f0f25a125")  # `linux.SERIAL_NAMESPACE`, fixed for good
EXPECTED_SWAP = {"btrfs": True, "xfs": True, "ext4": True, "ext2": False, "vfat": False, "exfat": False}


@pytest.mark.skipif(not _volumes(), reason="no scratch volumes given (FT_TEST_VOLUMES)")
@pytest.mark.parametrize("volume", _volumes() or ["-"])
def test_a_save_its_crash_and_its_recovery_on_a_real_scratch_volume(
    fs: FileSystem, tmp_path: Path, volume: str
) -> None:
    """Each volume kind takes the mode its type states (§6.8): btrfs, xfs and ext4 the swap; ext2 (no journal), vfat
    and exFAT the best-effort mode, which is then run in full (the macOS disk-image run's checks, `real_volume.py`).
    Each reports a drive identity, or the save refuses `no_drive_id` (recorded in the journal per kind)."""
    with fs.open_dir(volume) as v:
        kind = v.mark().fs_type
        caps = v.caps()
        drive = v.volume_id()
    assert kind in EXPECTED_SWAP, kind
    assert caps.swap is EXPECTED_SWAP[kind], (kind, caps)
    blkid = _findmnt(Path(volume), "UUID")  # udev's and blkid's name for the drive
    if kind in ("vfat", "exfat"):  # a format serial, as a name-based UUID (`linux.uuid_of_name`)
        assert drive == str(uuid.uuid5(SERIAL_NAMESPACE, blkid.upper())), (kind, blkid, drive)
    else:
        assert drive == blkid.lower(), (kind, blkid, drive)
    ledger = Path(volume) / f"ft-{uuid.uuid4().hex}"
    ledger.mkdir()
    try:
        settled_mkdir(ledger / "inc")
        if caps.swap:
            main = ledger / "main.bean"
            main.write_bytes(b"pre\n")
            out = durable_commit(fs, commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
            assert isinstance(out, Landed), out
            assert main.read_bytes() == b"post\n"
        else:
            real_volume_run(fs, tmp_path, ledger)
    finally:
        shutil.rmtree(ledger, ignore_errors=True)


@pytest.mark.skipif(not os.environ.get("FT_TEST_CASEFOLD_DIR"), reason="no case-folding folder given")
def test_a_case_folding_ext4_folder_is_probed_not_assumed(fs: FileSystem) -> None:
    folder = os.environ["FT_TEST_CASEFOLD_DIR"]
    with fs.open_dir(folder) as d:
        assert d.rule().casefold
    r = fs.resolve(folder + "/Main.bean")
    assert r.rules[-1].casefold
