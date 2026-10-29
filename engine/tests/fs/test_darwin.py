"""The file-system port on macOS (`ledger_engine.fs.darwin`; `design/specs/engine-jobs.md` §6.2, §6.3; S0060): the
primitives the durable order relies on, on the real file system (temp folders only, INV-24). Linux and Windows are
BL-0087."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from ledger_engine.fs.darwin import DarwinFS
from ledger_engine.ledger.paths import identify
from ledger_engine.ledger.writer import Change, Commit, Landed, NotSaved, Reason, durable_commit, sha256

pytestmark = [
    pytest.mark.inv("INV-01"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the real adapter is macOS-only until BL-0087"),
]


@pytest.fixture
def fs() -> DarwinFS:
    return DarwinFS()


def test_swap_exchanges_two_files_in_one_operation(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    ino_a = (tmp_path / "a").stat().st_ino
    with fs.open_dir(str(tmp_path)) as d:
        d.swap("a", "b")
    assert ((tmp_path / "a").read_bytes(), (tmp_path / "b").read_bytes()) == (b"B", b"A")
    assert (tmp_path / "b").stat().st_ino == ino_a  # the displaced file is the same inode, not a copy


def test_swap_needs_both_names(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    with fs.open_dir(str(tmp_path)) as d, pytest.raises(FileNotFoundError):
        d.swap("a", "missing")


def test_an_exclusive_rename_refuses_an_existing_name_and_leaves_both(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "t").write_bytes(b"T")
    (tmp_path / "x").write_bytes(b"X")
    with fs.open_dir(str(tmp_path)) as d:
        with pytest.raises(OSError, match=r".") as e:
            d.rename_excl("t", "x")
        assert e.value.errno == errno.EEXIST
        d.rename_excl("t", "new")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new", "x"]
    assert (tmp_path / "x").read_bytes() == b"X"


def test_create_is_exclusive_keeps_the_mode_asked_for_and_refuses_a_link(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "taken").write_bytes(b"1")
    (tmp_path / "ln").symlink_to(tmp_path / "elsewhere")
    with fs.open_dir(str(tmp_path)) as d:
        with pytest.raises(FileExistsError):
            d.create("taken", b"2", 0o644)
        with pytest.raises(FileExistsError):
            d.create("ln", b"2", 0o644)  # O_EXCL never follows a link
        d.create("new", b"data", 0o640)
    assert (tmp_path / "taken").read_bytes() == b"1"
    assert not (tmp_path / "elsewhere").exists()
    assert (tmp_path / "new").read_bytes() == b"data"
    assert (tmp_path / "new").stat().st_mode & 0o7777 == 0o640


def test_read_refuses_a_link_and_a_directory_and_answers_none_for_no_entry(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    (tmp_path / "lf").symlink_to(tmp_path / "f")
    (tmp_path / "sub").mkdir()
    with fs.open_dir(str(tmp_path)) as d:
        assert d.read("f") == b"x"
        assert d.read("none") is None
        with pytest.raises(OSError, match=r"."):
            d.read("lf")
        with pytest.raises(OSError, match=r"."):
            d.read("sub")


def test_a_handles_path_follows_its_folder_when_it_moves_and_a_link_put_in_its_place_is_not_followed(
    fs: DarwinFS,
    tmp_path: Path,
) -> None:
    (tmp_path / "L").mkdir()
    (tmp_path / "other").mkdir()
    with fs.open_dir(str(tmp_path / "L")) as d:
        before = d.path()
        (tmp_path / "L").rename(tmp_path / "L-moved")
        (tmp_path / "L").symlink_to(tmp_path / "other")
        assert d.path() == before + "-moved"
        d.create("x", b"1", 0o644)
    assert (tmp_path / "L-moved" / "x").exists()
    assert not (tmp_path / "other" / "x").exists()


def test_open_dir_through_a_link_reports_the_real_folder(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "ln").symlink_to(tmp_path / "real")
    with fs.open_dir(str(tmp_path / "ln")) as d:
        assert d.path().endswith("/real")


def test_resolve_gives_the_stored_spelling_on_a_case_insensitive_volume(fs: DarwinFS, tmp_path: Path) -> None:
    if os.pathconf(tmp_path, 11) == 1:
        pytest.skip("the temp volume is case-sensitive")
    (tmp_path / "Ledger").mkdir()
    (tmp_path / "Ledger" / "Main.bean").write_bytes(b"x")
    r = fs.resolve(f"{tmp_path}/LEDGER/main.BEAN")
    assert r.existing[-2:] == ("Ledger", "Main.bean")
    assert r.rest == ()
    assert all(rule.nfd and rule.casefold for rule in r.rules[-2:])


def test_durable_sync_uses_full_fsync_and_falls_back_once_per_volume(
    fs: DarwinFS,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6.2: `F_FULLFSYNC`; where the volume refuses it, `fsync`, reported once per volume."""
    calls: list[int] = []
    real_fcntl = fcntl.fcntl

    def refusing(fd: int, cmd: int, *args: object) -> object:
        if cmd == fcntl.F_FULLFSYNC:
            calls.append(cmd)
            raise OSError(errno.ENOTSUP, "refused")
        return real_fcntl(fd, cmd, *args)  # type: ignore[arg-type]

    notices: list[None] = []
    fallback = DarwinFS(on_fallback=lambda: notices.append(None))
    monkeypatch.setattr("ledger_engine.fs.darwin.fcntl.fcntl", refusing)
    with fallback.open_dir(str(tmp_path)) as d:
        d.create("a", b"1", 0o644)
        d.create("b", b"2", 0o644)
        d.sync()
    assert len(calls) == 3
    assert len(notices) == 1
    monkeypatch.undo()
    seen: list[int] = []

    def recording(fd: int, cmd: int, *args: object) -> object:
        seen.append(cmd)
        return real_fcntl(fd, cmd, *args)  # type: ignore[arg-type]

    monkeypatch.setattr("ledger_engine.fs.darwin.fcntl.fcntl", recording)
    with fs.open_dir(str(tmp_path)) as d:
        d.create("c", b"3", 0o644)
        d.sync()
    assert seen.count(fcntl.F_FULLFSYNC) == 2


def test_another_error_from_full_fsync_is_not_hidden(
    fs: DarwinFS, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(fd: int, cmd: int, *args: object) -> object:
        raise OSError(errno.EIO, "disk")

    monkeypatch.setattr("ledger_engine.fs.darwin.fcntl.fcntl", failing)
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(OSError, match="disk"):
            fs.durable_sync(fd)
    finally:
        os.close(fd)


def test_other_operating_systems_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_engine.fs import darwin

    monkeypatch.setattr(darwin.sys, "platform", "linux")
    with pytest.raises(darwin.UnsupportedPlatform):
        darwin.for_this_os()


# ---- S0060 verifier round 1 and data-safety red team round 1 ------------------------------------------------------


def _hard_linked(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "L").mkdir()
    (tmp_path / "Other").mkdir()
    main = tmp_path / "L" / "main.bean"
    main.write_bytes(b"pre\n")
    copy = tmp_path / "Other" / "copy.bean"
    os.link(main, copy)
    return main, copy


def test_a_hard_linked_file_resolves_to_itself_while_another_process_looks_up_the_other_name(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """The red team's race probe, bounded: a second process stats the other link until told to stop, while this one
    resolves the ledger path 3,000 times; every answer names the ledger path (before the fix about 1 in 9 did not)."""
    main, copy = _hard_linked(tmp_path)
    flag = tmp_path / "running"
    flag.write_bytes(b"")
    code = f"import os\nprint('ready', flush=True)\nwhile os.path.exists({str(flag)!r}): os.stat({str(copy)!r})\n"
    child = subprocess.Popen(  # noqa: S603 (this interpreter, fixed code)
        [sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        wrong = sum(1 for _ in range(3000) if not identify(fs, str(main)).canonical.endswith("/L/main.bean"))
    finally:
        flag.unlink()
        child.wait()
    assert wrong == 0


def _commit(root: Path, *changes: Change) -> Commit:
    (root / "local" / "journal").mkdir(parents=True, exist_ok=True)
    (root / "local" / "backups").mkdir(parents=True, exist_ok=True)
    return Commit(
        session="s1",
        job="j1",
        workspace_key="k",
        lineage="lin",
        engine_build="b",
        post_fingerprint="fp",
        adopted_digest="ad",
        parse_digest="pd",
        changes=changes,
        journal_dir=str(root / "local" / "journal"),
        backups_dir=str(root / "local" / "backups"),
    )


def test_a_hard_linked_ledger_file_refuses_the_save_on_apfs(fs: DarwinFS, tmp_path: Path) -> None:
    """Owner, 2026-10-29: "Refuse to save it (Recommended)": nothing written, both names keep the pre bytes."""
    main, copy = _hard_linked(tmp_path)
    os.stat(copy)  # the other name looked up last, as in the red team's repro
    out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, NotSaved)
    assert (out.reason, out.unchanged) == (Reason.HARD_LINKED, True)
    assert out.path is not None
    assert out.path.endswith("/L/main.bean")
    assert main.read_bytes() == copy.read_bytes() == b"pre\n"
    assert os.listdir(tmp_path / "local" / "journal") == []


def _bounded(fn: Callable[[], object]) -> bool:
    """Run `fn` on a thread; True when it ended. A hang detector only (the call ends in microseconds)."""
    done = threading.Event()

    def run() -> None:
        with contextlib.suppress(OSError):
            fn()
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return done.wait(5)


def test_a_fifo_never_blocks_read_stat_resolve_or_a_save(fs: DarwinFS, tmp_path: Path) -> None:
    """Verifier F4, red team 8."""
    fifo = tmp_path / "ledger.bean"
    os.mkfifo(fifo)
    d = fs.open_dir(str(tmp_path))
    try:
        assert _bounded(lambda: d.read("ledger.bean"))
        st = d.stat("ledger.bean")
        assert st is not None
        assert st.kind.value == "other"
        with pytest.raises(OSError, match="regular"):
            d.read("ledger.bean")
    finally:
        d.close()
    assert _bounded(lambda: fs.resolve(str(fifo)))
    results: list[object] = []
    assert _bounded(lambda: results.append(durable_commit(fs, _commit(tmp_path, Change(str(fifo), "0" * 64, b"x")))))
    out = results[0]
    assert isinstance(out, NotSaved)
    assert out.reason is Reason.NOT_A_FILE


def _run(*argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout  # noqa: S603 (fixed system tools)


def test_a_replaced_file_keeps_its_extended_attributes_and_mode_but_not_its_acl_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Red team r1 10 and the owner's answer of 2026-10-29 ("Keep xattrs, drop ACLs (Recommended)"): Finder tags
    (an extended attribute) and the mode survive the swap; the file's ACL does not."""
    (tmp_path / "L").mkdir()
    main = tmp_path / "L" / "main.bean"
    main.write_bytes(b"pre\n")
    os.chmod(main, 0o640)
    tag = "com.apple.metadata:_kMDItemUserTags"
    _run("/usr/bin/xattr", "-w", tag, "tagdata", str(main))
    me = os.environ.get("USER") or str(os.getuid())
    _run("/bin/chmod", "+a", f"user:{me} allow readattr", str(main))
    assert "allow readattr" in _run("/bin/ls", "-le", str(main))
    out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, Landed)
    assert main.read_bytes() == b"post\n"
    assert _run("/usr/bin/xattr", "-p", tag, str(main)).strip() == "tagdata"
    assert "allow readattr" not in _run("/bin/ls", "-le", str(main))
    assert main.stat().st_mode & 0o7777 == 0o640


def test_a_locked_file_is_seen_by_stat_and_refuses_the_save_before_anything_is_written(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Red team 10: `chflags uchg`."""
    (tmp_path / "L").mkdir()
    main = tmp_path / "L" / "main.bean"
    main.write_bytes(b"pre\n")
    os.chflags(main, stat.UF_IMMUTABLE)
    try:
        with fs.open_dir(str(tmp_path / "L")) as d:
            st = d.stat("main.bean")
            assert st is not None
            assert st.locked
        out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
        assert isinstance(out, NotSaved)
        assert out.reason is Reason.LOCKED
        assert os.listdir(tmp_path / "local" / "backups") == []
    finally:
        os.chflags(main, 0)


def test_a_file_with_a_name_near_name_max_saves_on_apfs(fs: DarwinFS, tmp_path: Path) -> None:
    """Red team 11: the derived temp and backup names are shortened, so the save lands (APFS allows 255 characters
    per name: `.<name>.ft-s1-j1.tmp` of a 250-character name would be 261)."""
    (tmp_path / "L").mkdir()
    main = tmp_path / "L" / ("a" * 245 + ".bean")
    main.write_bytes(b"pre\n")
    out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, Landed)
    assert main.read_bytes() == b"post\n"


def test_resolve_refuses_a_file_replaced_between_its_lookup_and_its_folder_listing(
    fs: DarwinFS, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The object resolution names must be the one the path named (`st_dev`, `st_ino`): a file renamed over in the
    moment between the two lookups makes resolution fail rather than name another object."""
    (tmp_path / "L").mkdir()
    main = tmp_path / "L" / "main.bean"
    main.write_bytes(b"pre\n")
    (tmp_path / "L" / "new").write_bytes(b"pre\n")
    real_stat = os.stat
    swapped = False

    def stat_then_replace(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal swapped
        st = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if not swapped and path == str(main):
            swapped = True
            os.replace(tmp_path / "L" / "new", main)  # an editor's save by rename
        return st

    monkeypatch.setattr("ledger_engine.fs.darwin.os.stat", stat_then_replace)
    with pytest.raises(OSError, match="changed while it was resolved"):
        fs.resolve(str(main))
    assert swapped


# ---- S0060 verifier round 2 and data-safety red team round 2 ------------------------------------------------------


def test_create_gives_the_mode_asked_for_whatever_the_umask(fs: DarwinFS, tmp_path: Path) -> None:
    """@regression-guard (S0060 verifier r2 F7, its proposed test: it kills dropping `os.fchmod`): a preserved
    group-writable mode survives umask 022."""
    old = os.umask(0o022)
    try:
        with fs.open_dir(str(tmp_path)) as d:
            d.create("f", b"x", 0o664)
    finally:
        os.umask(old)
    assert (tmp_path / "f").stat().st_mode & 0o777 == 0o664


def test_an_apfs_volume_can_swap(fs: DarwinFS, tmp_path: Path) -> None:
    """`can_swap` reads `VOL_CAP_INT_RENAME_SWAP` and `_EXCL`; an exFAT image answers False (checked by hand in
    S0060 round 2 with `hdiutil create -fs ExFAT`, not in this suite: it would mount a disk image)."""
    with fs.open_dir(str(tmp_path)) as d:
        assert d.can_swap()


@pytest.mark.parametrize(
    ("acl", "on_folder"),
    [
        ("everyone deny delete", False),
        ("everyone deny delete_child", True),
        ("everyone deny readextattr", False),
        (None, True),  # a read-only folder (mode 0555)
    ],
)
def test_permissions_that_would_block_the_save_refuse_it_before_anything_is_written_on_apfs(
    fs: DarwinFS, tmp_path: Path, acl: str | None, on_folder: bool
) -> None:
    """Verifier r2 F2, red team r2 M3 and L3: `access(2)` with the extended bits sees modes and ACLs."""
    folder = tmp_path / "L"
    folder.mkdir()
    main = folder / "main.bean"
    main.write_bytes(b"pre\n")
    target = folder if on_folder else main
    try:
        if acl is None:
            os.chmod(folder, 0o555)  # noqa: S103 (read-only on purpose)
        else:
            _run("/bin/chmod", "+a", acl, str(target))
        out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
        assert isinstance(out, NotSaved)
        assert (out.reason, out.unchanged, out.open_journal) == (Reason.PERMISSION, True, False)
        assert os.listdir(tmp_path / "local" / "backups") == []
        assert sorted(os.listdir(folder)) == ["main.bean"]
    finally:
        os.chmod(folder, 0o755)  # noqa: S103 (the temp folder back to its default)
        _run("/bin/chmod", "-RN", str(folder))  # every ACL off, so a regression cannot wedge the temp clean-up
    assert main.read_bytes() == b"pre\n"


# ---- S0061: rmdir, the ledger-set locks, the restore over the real adapter -----------------------------------------


def test_rmdir_removes_only_an_empty_folder_on_apfs(fs: DarwinFS, tmp_path: Path) -> None:
    """Probed before coding (S0061): `ENOTEMPTY` for a folder holding a `.DS_Store`, `ENOTDIR` for a link."""
    (tmp_path / "empty").mkdir()
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / ".DS_Store").write_bytes(b"x")
    (tmp_path / "link").symlink_to(tmp_path / "empty")
    with fs.open_dir(str(tmp_path)) as d:
        for name, err in (("full", errno.ENOTEMPTY), ("link", errno.ENOTDIR)):
            with pytest.raises(OSError, match=r".") as e:
                d.rmdir(name)
            assert e.value.errno == err
        d.rmdir("empty")
        with pytest.raises(FileNotFoundError):
            d.rmdir("empty")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["full", "link"]


HOLD = """import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.read()
"""


def test_a_ledger_set_lock_held_by_another_process_is_not_taken_and_is_free_once_it_ends(tmp_path: Path) -> None:
    """§6.1 step 3, §8.1 step 1: one lock file per `path_key`, `<sha256(path_key)>`, never deleted; tried without
    waiting."""
    from ledger_engine.fs.darwin import DarwinLocks

    locks = DarwinLocks(str(tmp_path))
    key = "/users/x/ledger/main.bean"
    lock_file = tmp_path / sha256(key.encode())
    holder = subprocess.Popen(  # noqa: S603 (this interpreter, fixed code)
        [sys.executable, "-c", HOLD, str(lock_file)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline() == "held\n"  # an event, not a sleep
        assert locks.try_lock(key) is None
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait()
    mine = locks.try_lock(key)
    assert mine is not None
    mine.release()
    assert lock_file.exists(), "lock files are never deleted"
    assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600


class _Died(BaseException):
    """The save's process died (a crash injected through a wrapper around the real adapter)."""


class _DyingDir:
    def __init__(self, inner: object, die_at: str) -> None:
        self._inner = inner
        self._die_at = die_at

    def rename_excl(self, src: str, dst: str) -> None:
        if dst.endswith(self._die_at):
            raise _Died
        self._inner.rename_excl(src, dst)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def __enter__(self) -> _DyingDir:
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()  # type: ignore[attr-defined]


class _DyingFS:
    def __init__(self, inner: DarwinFS, die_at: str) -> None:
        self._inner = inner
        self._die_at = die_at

    def resolve(self, path: str) -> object:
        return self._inner.resolve(path)

    def open_dir(self, path: str) -> _DyingDir:
        return _DyingDir(self._inner.open_dir(path), self._die_at)


def _crashed_save_on_apfs(fs: DarwinFS, root: Path) -> tuple[Commit, Path, Path]:
    ledger = root / "L"
    (ledger / "y2026").mkdir(parents=True)
    main = ledger / "main.bean"
    main.write_bytes(b"pre\n")
    created = ledger / "y2026" / "new.bean"
    c = _commit(root, Change(str(created), None, b"new\n"), Change(str(main), sha256(b"pre\n"), b"post\n"))
    with pytest.raises(_Died):
        durable_commit(_DyingFS(fs, ".closed"), c)  # type: ignore[arg-type]
    assert main.read_bytes() == b"post\n"
    assert os.listdir(root / "local" / "journal") == ["s1-j1.open"]
    return c, main, created


def test_a_crashed_saves_journal_is_resolved_on_apfs(fs: DarwinFS, tmp_path: Path) -> None:
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.restore import Resolved, recover_journal

    c, main, created = _crashed_save_on_apfs(fs, tmp_path)
    (tmp_path / "locks").mkdir()
    out = recover_journal(fs, DarwinLocks(str(tmp_path / "locks")), c.journal_dir, "s1-j1")
    assert out == Resolved("s1-j1")
    assert main.read_bytes() == b"pre\n"
    assert not created.exists()
    assert sorted(p.name for p in (tmp_path / "L").iterdir()) == ["main.bean", "y2026"]
    assert os.listdir(tmp_path / "local" / "journal") == ["s1-j1.resolved"]


def test_a_hard_linked_file_at_post_stops_the_restore_on_apfs(fs: DarwinFS, tmp_path: Path) -> None:
    """Probed before coding: a restore swap would give the name the pre bytes and leave the other link at post."""
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Stopped, recover_journal

    c, main, _ = _crashed_save_on_apfs(fs, tmp_path)
    os.link(main, tmp_path / "copy.bean")
    (tmp_path / "locks").mkdir()
    out = recover_journal(fs, DarwinLocks(str(tmp_path / "locks")), c.journal_dir, "s1-j1")
    assert isinstance(out, Stopped)
    assert out.cause is Cause.HARD_LINKED
    assert main.read_bytes() == (tmp_path / "copy.bean").read_bytes() == b"post\n"
    assert os.listdir(tmp_path / "local" / "journal") == ["s1-j1.open"]


def test_a_folder_the_save_made_in_a_read_only_parent_stops_the_restore_before_anything_is_written(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """S0061 verifier V1 on APFS: the parent made read-only (mode 0555) after the crash: the created folder could not
    be removed, so the try stops at its checks with `permission`, every file as it was, instead of failing for ever."""
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Stopped, recover_journal

    (tmp_path / "L").mkdir()
    created = tmp_path / "L" / "y2027" / "new.bean"
    c = _commit(tmp_path, Change(str(created), None, b"new\n"))
    with pytest.raises(_Died):
        durable_commit(_DyingFS(fs, ".closed"), c)  # type: ignore[arg-type]
    (tmp_path / "locks").mkdir()
    os.chmod(tmp_path / "L", 0o555)  # noqa: S103 (read-only on purpose)
    try:
        out = recover_journal(fs, DarwinLocks(str(tmp_path / "locks")), c.journal_dir, "s1-j1")
        assert isinstance(out, Stopped)
        assert out.cause is Cause.PERMISSION
        assert created.read_bytes() == b"new\n"
        assert os.listdir(tmp_path / "local" / "journal") == ["s1-j1.open"]
    finally:
        os.chmod(tmp_path / "L", 0o755)  # noqa: S103 (the temp folder back to its default)
