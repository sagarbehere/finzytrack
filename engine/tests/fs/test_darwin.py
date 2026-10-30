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
from typing import override

import pytest

from ledger_engine.fs.darwin import DarwinFS
from ledger_engine.ledger.handles import acl_denial
from ledger_engine.ledger.paths import identify
from ledger_engine.ledger.writer import Change, Commit, Landed, NotSaved, Reason, Step, durable_commit, sha256

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

    def remove(self, name: str) -> None:
        if self._die_at == "<remove>":
            raise _Died
        self._inner.remove(name)  # type: ignore[attr-defined]

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
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Resolved, Stopped, recover_journal

    c, main, created = _crashed_save_on_apfs(fs, tmp_path)
    (tmp_path / "locks").mkdir()
    locks = DarwinLocks(str(tmp_path / "locks"))
    out = recover_journal(fs, locks, c.journal_dir, "s1-j1")
    # the save had placed new.bean: never removed (owner, 2026-10-30: "Never remove it (Recommended)")
    assert out == Stopped(Cause.CREATED_PATH_TAKEN, str(created))
    assert (main.read_bytes(), created.read_bytes()) == (b"pre\n", b"new\n")
    assert os.listdir(tmp_path / "local" / "journal") == ["s1-j1.open"]
    created.unlink()  # the user deletes it and presses Retry
    out = recover_journal(fs, locks, c.journal_dir, "s1-j1")
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
    with pytest.raises(_Died):  # at the created file's exclusive rename: the folder holds only our temp (S0061 r5)
        durable_commit(_DyingFS(fs, "new.bean"), c)  # type: ignore[arg-type]
    (tmp_path / "locks").mkdir()
    os.chmod(tmp_path / "L", 0o555)  # noqa: S103 (read-only on purpose)
    try:
        out = recover_journal(fs, DarwinLocks(str(tmp_path / "locks")), c.journal_dir, "s1-j1")
        assert isinstance(out, Stopped)
        assert out.cause is Cause.PERMISSION
        assert not created.exists()
        assert os.listdir(created.parent) == [".new.bean.ft-s1-j1.tmp"]
        assert os.listdir(tmp_path / "local" / "journal") == ["s1-j1.open"]
    finally:
        os.chmod(tmp_path / "L", 0o755)  # noqa: S103 (the temp folder back to its default)


def test_an_outside_program_shortening_the_file_through_a_descriptor_held_from_before_the_save_is_kept_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """S0061 red team r3 F2a, on the real adapter: the save dies after its swap, before removing its temp; a program
    that opened main.bean before the save shortens it in place through its descriptor, so its bytes land in the
    displaced old object at our temp name, a prefix of both pre and post (the save appended). Recovery keeps them in
    the kept copy and stops (owner, 2026-10-30: "Staging name (Recommended)": a temp name is ours only when equal to
    the pre or post bytes)."""
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Stopped, recover_journal

    ledger = tmp_path / "L"
    ledger.mkdir()
    main = ledger / "main.bean"
    pre = b'include "a.bean"\n'
    post = pre + b'include "prices.bean"\n'
    main.write_bytes(pre)
    held = os.open(main, os.O_RDWR)  # the other program's descriptor, opened before the save
    try:
        c = _commit(tmp_path, Change(str(main), sha256(pre), post))
        with pytest.raises(_Died):
            durable_commit(_DyingFS(fs, "<remove>"), c)  # type: ignore[arg-type]
        assert main.read_bytes() == post
        shortened = b'include "a'
        os.ftruncate(held, 0)
        os.pwrite(held, shortened, 0)
        os.fsync(held)
    finally:
        os.close(held)
    (tmp_path / "locks").mkdir()
    out = recover_journal(fs, DarwinLocks(str(tmp_path / "locks")), c.journal_dir, "s1-j1")
    assert out == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, os.path.realpath(main))
    assert (ledger / "main.bean.kept-s1-j1").read_bytes() == shortened
    assert os.listdir(tmp_path / "local" / "journal") == ["s1-j1.open"]


def test_a_save_on_apfs_writes_its_new_bytes_under_the_staging_name_first(fs: DarwinFS, tmp_path: Path) -> None:
    """Owner, 2026-10-30: "Staging name (Recommended)": the save dies right after its staging file's rename to the
    temp name: the temp holds the full new bytes, no staging file is left, the file is untouched."""
    ledger = tmp_path / "L"
    ledger.mkdir()
    main = ledger / "main.bean"
    main.write_bytes(b"pre\n")
    c = _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"))

    class _AfterStaging(_DyingDir):
        @override
        def rename_excl(self, src: str, dst: str) -> None:
            self._inner.rename_excl(src, dst)  # type: ignore[attr-defined]
            if src.endswith(".new"):
                raise _Died

    class _FS(_DyingFS):
        @override
        def open_dir(self, path: str) -> _DyingDir:
            return _AfterStaging(self._inner.open_dir(path), "")

    with pytest.raises(_Died):
        durable_commit(_FS(fs, ""), c)  # type: ignore[arg-type]
    assert sorted(p.name for p in ledger.iterdir()) == [".main.bean.ft-s1-j1.tmp", "main.bean"]
    assert (ledger / ".main.bean.ft-s1-j1.tmp").read_bytes() == b"post\n"
    assert main.read_bytes() == b"pre\n"


def test_sync_file_syncs_a_regular_file_through_a_read_only_descriptor_and_refuses_anything_else(
    fs: DarwinFS, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S0061 r4 (red team F2; probed on APFS: `F_FULLFSYNC` works on a read-only descriptor, a mode-0400 file too):
    the save syncs each file it replaces before it reads it for the backup. A link, a folder, a FIFO and no entry are
    refused, never followed or opened."""
    (tmp_path / "f").write_bytes(b"data")
    (tmp_path / "f").chmod(0o400)
    (tmp_path / "sub").mkdir()
    (tmp_path / "l").symlink_to(tmp_path / "f")
    os.mkfifo(tmp_path / "p")
    real_fcntl = fcntl.fcntl
    seen: list[int] = []

    def recording(fd: int, cmd: int, *args: object) -> object:
        seen.append(cmd)
        return real_fcntl(fd, cmd, *args)  # type: ignore[arg-type]

    monkeypatch.setattr("ledger_engine.fs.darwin.fcntl.fcntl", recording)
    with fs.open_dir(str(tmp_path)) as d:
        d.sync_file("f")
        assert seen.count(fcntl.F_FULLFSYNC) == 1
        for name in ("l", "sub", "p"):
            with pytest.raises(OSError, match=r"."):
                d.sync_file(name)
        with pytest.raises(FileNotFoundError):
            d.sync_file("missing")
    assert seen.count(fcntl.F_FULLFSYNC) == 1
    assert (tmp_path / "f").read_bytes() == b"data"


# ---- S0061 round 5: an inheritable ACL entry, and each removal of the restore checked (red team r5 F2) ------------


@pytest.mark.parametrize(
    ("acl", "refused"),
    [
        ("everyone deny delete,file_inherit,directory_inherit", True),
        ("everyone deny read,file_inherit,only_inherit", True),
        (f"user:{os.environ.get('USER', 'nobody')} deny delete,file_inherit", True),
        ("everyone deny delete,directory_inherit", True),
        ("user:nobody deny delete,file_inherit", False),  # another user's: not ours
        ("everyone deny delete", False),  # not inherited: the folder's own (its own deletion)
        ("everyone deny write,file_inherit", True),  # S0061 r6: any inheritable deny is refused (the allowlist)
    ],
)
def test_an_inheritable_acl_entry_denying_us_delete_or_read_refuses_the_save_before_anything_is_written_on_apfs(
    fs: DarwinFS, tmp_path: Path, acl: str, refused: bool
) -> None:
    """Red team r5 F2, ported (probed on APFS first): `deny delete,file_inherit` set after the ledger files were made
    is inherited by the save's staging file, so its rename to the temp name answered EACCES and nothing of ours could
    ever remove it (every recovery `failed`); `deny read,file_inherit,only_inherit` made our new file unreadable (read
    back failed, every recovery stopped on our own post object). The save now refuses such a folder with `permission`
    before anything is written; an entry for another user, or one that is not inherited, does not refuse it."""
    folder = tmp_path / "L"
    folder.mkdir()
    main = folder / "main.bean"
    main.write_bytes(b"pre\n")
    try:
        _run("/bin/chmod", "+a", acl, str(folder))
        with fs.open_dir(str(folder)) as h:
            assert (acl_denial(h) is not None) is refused
        out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
        if refused:
            assert isinstance(out, NotSaved), out
            assert (out.reason, out.unchanged, out.open_journal) == (Reason.PERMISSION, True, False)
            assert os.listdir(tmp_path / "local" / "backups") == []
            assert os.listdir(tmp_path / "local" / "journal") == []
            assert sorted(os.listdir(folder)) == ["main.bean"]
            assert main.read_bytes() == b"pre\n"
        else:
            assert isinstance(out, Landed), out
    finally:
        _run("/bin/chmod", "-RN", str(tmp_path))  # every ACL off, so a regression cannot wedge the temp clean-up


class _DenyingDir(_DyingDir):
    """At the staging file's rename to its temp name: an ACL `deny delete` put on the staging file, then `EIO`."""

    @override
    def rename_excl(self, src: str, dst: str) -> None:
        if src.endswith(".new") and dst.endswith(".tmp") and not src.startswith(".main.bean.ft-restore"):
            _run("/bin/chmod", "+a", "everyone deny delete", os.path.join(self._inner.path(), src))  # type: ignore[attr-defined]
            raise OSError(errno.EIO, "injected")
        self._inner.rename_excl(src, dst)  # type: ignore[attr-defined]


class _DenyingFS(_DyingFS):
    @override
    def open_dir(self, path: str) -> _DyingDir:
        return _DenyingDir(self._inner.open_dir(path), self._die_at)


def test_a_name_of_ours_whose_removal_is_denied_stops_the_rollback_before_anything_is_written_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Red team r5 F2, step 2's half: our staging file carries a `deny delete` (as an inherited entry would); the
    save fails at its rename; the rollback's checks see that its removal is denied (`access(_DELETE_OK)`) and stop
    with `permission`, nothing written; once the entry is gone, recovery resolves."""
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Resolved, Stopped, recover_journal

    folder = tmp_path / "L"
    folder.mkdir()
    main = folder / "main.bean"
    main.write_bytes(b"pre\n")
    c = _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"))
    staging = folder / ".main.bean.ft-s1-j1.new"
    try:
        out = durable_commit(_DenyingFS(fs, "<none>"), c)  # type: ignore[arg-type]
        assert out == NotSaved(Step.REPLACE, Reason.IO_ERROR, True, True, str(main)), out
        assert staging.exists()
        assert sorted(os.listdir(folder)) == [".main.bean.ft-s1-j1.new", "main.bean"]
        (tmp_path / "locks").mkdir()
        locks = DarwinLocks(str(tmp_path / "locks"))
        assert recover_journal(fs, locks, c.journal_dir, "s1-j1") == Stopped(Cause.PERMISSION, str(main), str(staging))
        assert staging.exists()
        assert os.listdir(tmp_path / "local" / "journal") == ["s1-j1.open"]
        _run("/bin/chmod", "-N", str(staging))
        assert recover_journal(fs, locks, c.journal_dir, "s1-j1") == Resolved("s1-j1")
        assert sorted(os.listdir(folder)) == ["main.bean"]
        assert main.read_bytes() == b"pre\n"
    finally:
        _run("/bin/chmod", "-RN", str(tmp_path))


def test_a_folder_that_denies_new_entries_stops_the_restore_of_a_file_at_post_before_anything_is_written_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Probed on APFS (S0061 r5): `deny add_file` on a folder allows removals (`access(_DELETE_OK)` answers yes) but
    refuses the restore temp's create; the entry-change right is checked for a file at post, so the try stops with
    `permission` before anything is written instead of failing half-way."""
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Stopped, recover_journal

    c, main, created = _crashed_save_on_apfs(fs, tmp_path)
    created.unlink()  # nothing at the created path: only main.bean, at post, is undone
    (tmp_path / "locks").mkdir()
    before = sorted(os.listdir(tmp_path / "L"))
    try:
        _run("/bin/chmod", "+a", "everyone deny add_file", str(tmp_path / "L"))
        out = recover_journal(fs, DarwinLocks(str(tmp_path / "locks")), c.journal_dir, "s1-j1")
        assert out == Stopped(Cause.PERMISSION, str(main), str(tmp_path / "L"), "deny add_file"), out
        assert sorted(os.listdir(tmp_path / "L")) == before
        assert main.read_bytes() == b"post\n"
    finally:
        _run("/bin/chmod", "-RN", str(tmp_path))


# ---- S0061 round 6: the ACL allowlist (owner, 2026-10-30: "Allowlist (Recommended)"), probed on APFS first ---------


def _me() -> str:
    return os.environ.get("USER", "nobody")


def _two_folders(root: Path, created: str) -> tuple[Path, Path, Commit]:
    """Red team r6's shape: L/Q/a.bean replaced (written first), and the created file `created` under L/P."""
    ledger = root / "L"
    (ledger / "Q").mkdir(parents=True)
    (ledger / "P").mkdir()
    a = ledger / "Q" / "a.bean"
    a.write_bytes(b"pre\n")
    c = _commit(root, Change(str(a), sha256(b"pre\n"), b"post\n"), Change(str(ledger / "P" / created), None, b"new\n"))
    return ledger, a, c


def _nothing_written(root: Path, ledger: Path, listing: list[str]) -> None:
    assert os.listdir(root / "local" / "backups") == []
    assert os.listdir(root / "local" / "journal") == []
    assert sorted(str(p.relative_to(ledger)) for p in ledger.rglob("*")) == listing


def _clean_acls(root: Path) -> None:
    subprocess.run(["/usr/bin/chflags", "-R", "nouchg", str(root)], check=False)  # noqa: S603
    subprocess.run(["/bin/chmod", "-RN", str(root)], check=False)  # noqa: S603 (every ACL set here, off again)


R6_APFS_REFUSED = {
    # case: (the entry on L/P, without its subject; the created file under L/P)
    "rt_F1_delete_child_direct": ("deny delete_child", "b.bean"),
    "rt_F1_delete_child_into_made_folder": ("deny delete_child,directory_inherit,only_inherit", "y2026/b.bean"),
    "rt_F2_search_into_made_folder": ("deny search,directory_inherit,only_inherit", "y2026/b.bean"),
    "v_F1_readattr_inherited": ("deny readattr,file_inherit,only_inherit", "b.bean"),
    "v_F1_readsecurity_inherited": ("deny readsecurity,file_inherit,only_inherit", "b.bean"),
    "rt_O2_readextattr_inherited": ("deny readextattr,file_inherit", "b.bean"),
    "rt_O2_writeextattr_inherited": ("deny writeextattr,file_inherit", "b.bean"),
}


@pytest.mark.parametrize("case", sorted(R6_APFS_REFUSED))
def test_R6_a_folder_whose_acl_denies_us_anything_the_save_or_its_undo_needs_is_refused_on_apfs(
    fs: DarwinFS, tmp_path: Path, case: str
) -> None:
    """Red team r6 F1 (High: `deny delete_child`, direct or inherited by a folder the save makes: the staging file's
    rename failed and every undo stopped on it for ever), F2 (an inherited `deny search` into a folder the save
    makes: `failed` for ever), overflow 2, and verifier r6 F1 (inherited `readattr`, `readsecurity`: our own names
    could not be `stat`ed). Each got past round 5's check; each is now refused before anything is written, with
    `permission` naming the folder (and, for an ACL entry the allowlist refuses, the entry)."""
    ace, created = R6_APFS_REFUSED[case]
    ledger, a, c = _two_folders(tmp_path, created)
    try:
        _run("/bin/chmod", "+a", f"user:{_me()} {ace}", str(ledger / "P"))
        out = durable_commit(fs, c)
        assert isinstance(out, NotSaved), out
        assert (out.reason, out.unchanged, out.open_journal) == (Reason.PERMISSION, True, False), out
        assert out.entry == str(ledger / "P"), out
        assert out.detail == (None if ace == "deny delete_child" else ace), out  # `_RMFILE_OK` meets it first
        _nothing_written(tmp_path, ledger, ["P", "Q", "Q/a.bean"])
        assert a.read_bytes() == b"pre\n"
    finally:
        _clean_acls(tmp_path)


class _FailingCreate(_DyingDir):
    """An I/O error at the staging create of `b.bean` (red team r6 F3's injection)."""

    def create(self, name: str, data: bytes, mode: int, like: str | None = None) -> None:
        if name.startswith(".b.bean.ft-") and name.endswith(".new"):
            raise OSError(errno.EIO, "injected")
        self._inner.create(name, data, mode, like=like)  # type: ignore[attr-defined]


class _FailingCreateFS(_DyingFS):
    @override
    def open_dir(self, path: str) -> _DyingDir:
        return _FailingCreate(self._inner.open_dir(path), "<none>")


def test_R6_rt_F3_a_folder_that_denies_removing_its_entries_is_refused_before_the_save_makes_a_folder_in_it_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Red team r6 F3: `deny delete_child` on P, the save makes P/y2026 and fails at its staging create: the rollback
    could never remove the empty y2026 (`permission` for ever). `_RMFILE_OK` on P now refuses the save first."""
    ledger = tmp_path / "L"
    (ledger / "P").mkdir(parents=True)
    main = ledger / "main.bean"
    main.write_bytes(b"pre\n")
    c = _commit(
        tmp_path,
        Change(str(ledger / "P" / "y2026" / "b.bean"), None, b"new\n"),
        Change(str(main), sha256(b"pre\n"), b"post\n"),
    )
    try:
        _run("/bin/chmod", "+a", f"user:{_me()} deny delete_child", str(ledger / "P"))
        out = durable_commit(_FailingCreateFS(fs, "<none>"), c)  # type: ignore[arg-type]
        assert isinstance(out, NotSaved), out
        assert out == NotSaved(Step.BACKUP, Reason.PERMISSION, True, False, out.path, str(ledger / "P")), out
        _nothing_written(tmp_path, ledger, ["P", "main.bean"])
    finally:
        _clean_acls(tmp_path)


SELF_BITS = ("delete", "writeextattr", "writeattr", "writesecurity", "chown")
OTHER_BITS = (
    "list",
    "add_file",
    "search",
    "add_subdirectory",
    "delete_child",
    "readattr",
    "readextattr",
    "readsecurity",
)


@pytest.mark.parametrize(
    ("ace", "allowed"),
    [
        *((f"deny {b}", True) for b in SELF_BITS),  # each folder-self bit, not inherited: allowed
        *((f"deny {b},file_inherit", False) for b in SELF_BITS),  # the same bits inherited: refused
        *((f"deny {b},directory_inherit", False) for b in SELF_BITS),
        *((f"deny {b},file_inherit,only_inherit", False) for b in SELF_BITS),
        *((f"deny {b}", False) for b in OTHER_BITS),  # every other bit, direct
        *((f"deny {b},file_inherit,directory_inherit", False) for b in OTHER_BITS),  # and inherited
        *((f"deny {b},file_inherit,directory_inherit,only_inherit", False) for b in OTHER_BITS),  # only inherited
    ],
)
def test_R6_the_allowlist_from_the_rule_on_apfs(fs: DarwinFS, tmp_path: Path, ace: str, allowed: bool) -> None:
    """The rule as the owner approved it, through the real adapter and the real save: a deny entry for us on the
    ledger folder passes only when new entries do not inherit it and it denies only folder-self bits; then the save
    lands. Anything else is refused before anything is written (a direct `deny search` or `deny list` stops even
    the resolution of the file: still refused before the first effect)."""
    ledger = tmp_path / "L"
    ledger.mkdir()
    main = ledger / "main.bean"
    main.write_bytes(b"pre\n")
    c = _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"), Change(str(ledger / "n.bean"), None, b"n\n"))
    try:
        _run("/bin/chmod", "+a", f"user:{_me()} {ace}", str(ledger))
        bit = ace.split()[1].split(",")[0]
        # probed: an entry acting on the folder itself that denies `list` or `search` (the folder cannot be opened or
        # looked into) or `readattr`, `readsecurity` (it cannot be `stat`ed) stops the file's resolution: an I/O
        # error, before the first effect
        unreachable = bit in ("list", "search", "readattr", "readsecurity") and "only_inherit" not in ace
        if not unreachable:
            with fs.open_dir(str(ledger)) as h:
                assert (acl_denial(h) is None) is allowed, ace
        out = durable_commit(fs, c)
        if allowed:
            assert isinstance(out, Landed), (ace, out)
        else:
            assert isinstance(out, NotSaved), (ace, out)
            assert out.open_journal is False, (ace, out)
            if unreachable:  # its resolution fails: an I/O error, before the first effect
                assert out.reason is Reason.IO_ERROR, (ace, out)
            else:
                assert (out.reason, out.unchanged, out.entry) == (Reason.PERMISSION, True, str(ledger)), (ace, out)
            assert os.listdir(tmp_path / "local" / "backups") == []
            assert os.listdir(tmp_path / "local" / "journal") == []
            _clean_acls(tmp_path)
            assert sorted(os.listdir(ledger)) == ["main.bean"]
            assert main.read_bytes() == b"pre\n"
    finally:
        _clean_acls(tmp_path)


@pytest.mark.parametrize(
    ("aces", "allowed"),
    [
        (("group:everyone deny delete", "group:everyone deny writeextattr"), True),  # macOS's own pair
        (("user:nobody deny delete_child,search,list,file_inherit,directory_inherit",), True),  # another user's
        (("group:wheel deny delete_child,file_inherit",), True),  # a group we are not in
        (("group:staff deny delete_child,file_inherit",), False),  # a group we are in
        (("group:everyone deny delete_child",), False),
    ],
)
def test_R6_whose_entry_it_is_decides_on_apfs(
    fs: DarwinFS, tmp_path: Path, aces: tuple[str, ...], allowed: bool
) -> None:
    """macOS's own entries on the home folder, Desktop, Documents and Downloads (`group:everyone deny delete`,
    `deny writeextattr`, checked on this Mac on 2026-10-30) must pass; an entry for another user or a group we are
    not in does not apply to us; one for a group we are in, or everyone, does."""
    ledger = tmp_path / "L"
    ledger.mkdir()
    main = ledger / "main.bean"
    main.write_bytes(b"pre\n")
    try:
        for ace in aces:
            _run("/bin/chmod", "+a", ace, str(ledger))
        out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
        if allowed:
            assert isinstance(out, Landed), out
        else:
            assert isinstance(out, NotSaved), out
            assert (out.reason, out.open_journal, out.entry) == (Reason.PERMISSION, False, str(ledger)), out
    finally:
        _clean_acls(tmp_path)


def test_R6_an_allow_entry_before_a_deny_still_refuses_on_apfs(fs: DarwinFS, tmp_path: Path) -> None:
    """The red team saw APFS honour an allow placed before the deny; the rule does not weigh allows: fails closed."""
    ledger = tmp_path / "L"
    ledger.mkdir()
    main = ledger / "main.bean"
    main.write_bytes(b"pre\n")
    try:
        _run("/bin/chmod", "+a", f"user:{_me()} deny delete,file_inherit", str(ledger))
        _run("/bin/chmod", "+a#", "0", f"user:{_me()} allow delete,file_inherit", str(ledger))
        out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
        assert isinstance(out, NotSaved), out
        assert (out.reason, out.detail) == (Reason.PERMISSION, "deny delete,file_inherit"), out
    finally:
        _clean_acls(tmp_path)


def test_R6_the_system_answers_rmfile_ok_under_deny_delete_child_and_not_under_macos_own_entries_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Probed (S0061 r6): write and search answer yes under `deny delete_child`; `_RMFILE_OK` answers no; macOS's own
    entries leave both yes."""
    from ledger_engine.fs.port import Right

    d = tmp_path / "d"
    d.mkdir()
    try:
        _run("/bin/chmod", "+a", "group:everyone deny delete,writeextattr", str(d))
        with fs.open_dir(str(d)) as h:
            assert (h.allowed(".", Right.CHANGE_ENTRIES), h.allowed(".", Right.REMOVE_ENTRIES)) == (True, True)
        _run("/bin/chmod", "+a", f"user:{_me()} deny delete_child", str(d))
        with fs.open_dir(str(d)) as h:
            assert (h.allowed(".", Right.CHANGE_ENTRIES), h.allowed(".", Right.REMOVE_ENTRIES)) == (True, False)
    finally:
        _clean_acls(tmp_path)


def test_R6_a_bit_libc_does_not_name_or_a_word_that_disagrees_is_unknown() -> None:
    """The adapter's fail-closed reading of a permset or flagset: a bit outside the named ones, or a word that does
    not match libc's own getter, adds `unknown` (which the allowlist refuses)."""
    import ctypes

    from ledger_engine.fs.darwin import ACL_PERMS, _named  # pyright: ignore[reportPrivateUsage]

    bits = dict(ACL_PERMS)

    def getter(answer: int) -> Callable[[ctypes.c_void_p, int], int]:
        return lambda _w, b: 1 if answer & b else 0

    for word, answer, want in [
        (bits["delete"], bits["delete"], frozenset({"delete"})),
        (bits["delete"] | 1 << 25, bits["delete"], frozenset({"delete", "unknown"})),
        (bits["delete"], bits["delete"] | bits["search"], frozenset({"delete", "search", "unknown"})),
    ]:
        w = ctypes.c_uint32(word)
        assert _named(ctypes.c_void_p(ctypes.addressof(w)), ACL_PERMS, getter(answer)) == want


def test_V6_F2_an_acl_entry_that_appeared_after_the_crash_stops_recovery_before_anything_is_written_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Verifier r6 F2 on APFS: the save crashed with main.bean at post; then `everyone deny delete,file_inherit` is
    put on the ledger folder. The try stops with `permission`, naming the folder and the entry, nothing written (it
    used to write its restore staging file, which inherited the entry, and every later try stopped on it). Once the
    entry is removed, Retry undoes main.bean."""
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Stopped, recover_journal

    c, main, created = _crashed_save_on_apfs(fs, tmp_path)
    (tmp_path / "locks").mkdir()
    locks = DarwinLocks(str(tmp_path / "locks"))
    ledger = tmp_path / "L"
    before = sorted(str(p.relative_to(ledger)) for p in ledger.rglob("*"))
    try:
        _run("/bin/chmod", "+a", "everyone deny delete,file_inherit", str(ledger))
        out = recover_journal(fs, locks, c.journal_dir, "s1-j1")
        assert out == Stopped(Cause.PERMISSION, str(main), str(ledger), "deny delete,file_inherit"), out
        assert sorted(str(p.relative_to(ledger)) for p in ledger.rglob("*")) == before
        assert main.read_bytes() == b"post\n"
        _run("/bin/chmod", "-N", str(ledger))
        out = recover_journal(fs, locks, c.journal_dir, "s1-j1")
        assert out == Stopped(Cause.CREATED_PATH_TAKEN, str(created)), out
        assert main.read_bytes() == b"pre\n"
    finally:
        _clean_acls(tmp_path)


def test_R6_O3_a_locked_ledger_folder_is_refused_and_a_locked_folder_the_save_made_stops_recovery_on_apfs(
    fs: DarwinFS, tmp_path: Path
) -> None:
    """Red team r6 overflow 3: a lock flag (`uchg`) on a folder. On the ledger folder the save writes in, write and
    `_RMFILE_OK` answer no (probed): refused at step 0. On a folder the save made and would remove (the save died at
    its staging create, so it is empty), its `rmdir` would answer `EPERM` on every try: step 2 stops with
    `permission`, nothing written; once the flag is cleared, Retry resolves."""
    from ledger_engine.fs.darwin import DarwinLocks
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.restore import Resolved, Stopped, recover_journal

    ledger = tmp_path / "L"
    ledger.mkdir()
    main = ledger / "main.bean"
    main.write_bytes(b"pre\n")
    try:
        _run("/usr/bin/chflags", "uchg", str(ledger))
        out = durable_commit(fs, _commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
        assert isinstance(out, NotSaved), out
        assert (out.reason, out.open_journal, out.entry) == (Reason.PERMISSION, False, str(ledger)), out
        _run("/usr/bin/chflags", "nouchg", str(ledger))
        made = ledger / "y2026"
        c = _commit(tmp_path, Change(str(made / "b.bean"), None, b"new\n"))

        class _Dies(_DyingDir):
            def create(self, name: str, data: bytes, mode: int, like: str | None = None) -> None:
                if name.endswith(".new"):
                    raise _Died
                self._inner.create(name, data, mode, like=like)  # type: ignore[attr-defined]

        class _DiesFS(_DyingFS):
            @override
            def open_dir(self, path: str) -> _DyingDir:
                return _Dies(self._inner.open_dir(path), "<none>")

        with pytest.raises(_Died):
            durable_commit(_DiesFS(fs, "<none>"), c)  # type: ignore[arg-type]
        assert os.listdir(made) == []
        _run("/usr/bin/chflags", "uchg", str(made))
        (tmp_path / "locks").mkdir()
        locks = DarwinLocks(str(tmp_path / "locks"))
        assert recover_journal(fs, locks, c.journal_dir, "s1-j1") == Stopped(Cause.PERMISSION, str(made))
        assert made.is_dir()
        _run("/usr/bin/chflags", "nouchg", str(made))
        assert recover_journal(fs, locks, c.journal_dir, "s1-j1") == Resolved("s1-j1")
        assert not made.exists()
    finally:
        _clean_acls(tmp_path)
