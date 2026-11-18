"""Real-disk helpers shared by the file-system adapters' tests (`test_darwin.py`, `test_linux.py`; S0125): a save's
records in a scratch folder, a crash injected through a wrapper around a real adapter, and the best-effort mode's run on
a real volume without the swap (ADR-0068 D2), so each OS runs the same checks on its own drives."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ledger_engine.fs.port import FileSystem, FolderId
from ledger_engine.fs.this_os import folder_identity
from ledger_engine.ledger.writer import Change, Commit, Landed, durable_commit, sha256


def app_identity(path: Path) -> FolderId:
    """The app folder's identity as the sidecar records it at start (`this_os.folder_identity`: with the creation
    time on Linux, BL-0262, S0125; on Windows the volume serial and the file ID, `port.folder_id`, S0128; the
    sidecar's side since S0129); the sidecar's own computation is tested in the backend."""
    if sys.platform == "win32":  # through the Windows adapter (S0128: the test's `windows_port` selects it)
        from ledger_engine.fs.port import folder_id
        from ledger_engine.fs.this_os import for_this_os

        with for_this_os().open_dir(str(path)) as d:
            return folder_id(d)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return folder_identity(fd)
    finally:
        os.close(fd)


def app_identity_text(path: Path) -> str:
    """`--app-folder-id` for the folder at `path`."""
    return ":".join(str(n) for n in app_identity(path))


def commit(root: Path, *changes: Change) -> Commit:
    (root / "local" / "journal").mkdir(parents=True, exist_ok=True)
    (root / "local" / "backups").mkdir(parents=True, exist_ok=True)
    return Commit(
        session="s1",
        job="j1",
        workspace_key="k",
        lineage="lin",
        engine_build="b",
        changes=changes,
        journal_dir=str(root / "local" / "journal"),
        backups_dir=str(root / "local" / "backups"),
    )


def settled_mkdir(path: Path) -> None:
    """The test's own setup `mkdir` of a folder in one it has just made, tried again (up to 5 times, the parent
    folder synced before each try) on `ENOENT`/`ENOTDIR`: macOS 26's FSKit FAT driver answered so right after the
    parent's `mkdir`, 2 runs in 25 on fresh FAT16 and FAT32 images (S0065 verifier r2 F4; never reproduced in 1600
    bare `mkdir` pairs, nor in 200 engine saves that make a folder, so whether the sync settles it is unproven). Only
    for the setup: no engine call is ever retried here."""
    for attempt in range(5):
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            path.mkdir(exist_ok=True)
        except (FileNotFoundError, NotADirectoryError):
            if attempt == 4:
                raise
        else:
            return


def real_volume_run(fs: FileSystem, tmp_path: Path, ledger: Path) -> None:
    from ledger_engine.ledger.core.recovery import Cause
    from ledger_engine.ledger.handles import SERIAL_IDENTIFIED
    from ledger_engine.ledger.restore import Resolved, Stopped, recover_journal

    with fs.open_dir(str(ledger)) as d:
        assert not d.caps().swap, "a volume without the swap is expected here"
    main, inc = ledger / "main.bean", ledger / "inc" / "a.bean"
    main.write_bytes(b"pre\n")
    inc.write_bytes(b"inc\n")
    created = ledger / "y2026" / "new.bean"
    c = commit(
        tmp_path,
        Change(str(inc), sha256(b"inc\n"), b"inc2\n"),
        Change(str(created), None, b"new\n"),
        Change(str(main), sha256(b"pre\n"), b"post\n"),
    )
    out = durable_commit(fs, c)
    assert isinstance(out, Landed), out
    assert (main.read_bytes(), inc.read_bytes(), created.read_bytes()) == (b"post\n", b"inc2\n", b"new\n")
    assert not [n for n in os.listdir(ledger) + os.listdir(ledger / "inc") if ".ft-" in n], "a name of ours left"
    root2 = tmp_path / "second"
    root2.mkdir()
    c2 = commit(root2, Change(str(inc), sha256(b"inc2\n"), b"inc3\n"), Change(str(main), sha256(b"post\n"), b"post2\n"))
    with pytest.raises(Died):
        durable_commit(DyingFS(fs, ".closed"), c2)  # type: ignore[arg-type]
    assert main.read_bytes() == b"post2\n"
    main.unlink()  # missing in the middle of a replace, as a power loss on a drive without a journal can leave it
    with inc.open("r+b") as f:  # S0101: an editor puts inc back to its old text in place, without a sync
        f.write(b"inc2\n")
        f.truncate()
    out2 = recover_journal(fs, c2.journal_dir, "s1-j1")
    assert out2 == Resolved("s1-j1"), out2  # inc found at pre and flushed (read-only `F_FULLFSYNC`, or `fsync`)
    assert (main.read_bytes(), inc.read_bytes()) == (b"post\n", b"inc2\n"), "restored from the backups"
    assert not [n for n in os.listdir(ledger) + os.listdir(ledger / "inc") if ".ft-" in n]
    root3 = tmp_path / "third"
    root3.mkdir()
    c3 = commit(root3, Change(str(main), sha256(b"post\n"), b"post3\n"))
    with pytest.raises(Died):
        durable_commit(DyingFS(fs, ".closed"), c3)  # type: ignore[arg-type]
    main.write_bytes(b"; an editor\n")  # an outside save before the restore: never replaced
    out3 = recover_journal(fs, c3.journal_dir, "s1-j1")
    assert out3 == Stopped(Cause.FOREIGN, str(main)), out3
    assert main.read_bytes() == b"; an editor\n"
    # an empty file at a replaced path is judged like any other content (ADR-0068 D2): the try stops; once the user
    # deletes it, the file is missing and Retry puts the old version back from the backup
    root4 = tmp_path / "fourth"
    root4.mkdir()
    c4 = commit(root4, Change(str(main), sha256(b"; an editor\n"), b"post4\n"))
    with pytest.raises(Died):
        durable_commit(DyingFS(fs, ".closed"), c4)  # type: ignore[arg-type]
    main.unlink()
    main.write_bytes(b"")
    out4 = recover_journal(fs, c4.journal_dir, "s1-j1")
    assert out4 == Stopped(Cause.FOREIGN, str(main)), out4
    assert main.read_bytes() == b""
    main.unlink()
    out5 = recover_journal(fs, c4.journal_dir, "s1-j1")
    with fs.open_dir(str(ledger)) as d:
        fs_type = d.mark().fs_type
    if fs_type in SERIAL_IDENTIFIED:
        # FAT and exFAT: a one-file save's missing file needs evidence (owner, 2026-11-09: "Evidence for missing
        # (Recommended)"; S0089): nothing is put back, and the user copies the old version by hand, then Retry, which
        # finds it at pre and flushes it before the resolve (S0101; this test predated S0089 and failed here since)
        assert isinstance(out5, Stopped), out5
        assert out5.cause is Cause.MISSING_UNCONFIRMED, out5
        assert not main.exists()
        main.write_bytes(b"; an editor\n")
        out5 = recover_journal(fs, c4.journal_dir, "s1-j1")
    assert out5 == Resolved("s1-j1"), out5
    assert main.read_bytes() == b"; an editor\n"
    assert not [n for n in os.listdir(ledger) if ".ft-" in n]
    # two outside entries at names of ours in one try each get their own kept name, on a drive without the
    # exclusive rename too (S0071 red team F1: there the second plain rename replaced the first kept copy)
    root5 = tmp_path / "fifth"
    root5.mkdir()
    c5 = commit(root5, Change(str(main), sha256(b"; an editor\n"), b"post5\n"))
    with pytest.raises(Died):
        durable_commit(DyingFS(fs, ".closed"), c5)  # type: ignore[arg-type]
    (ledger / ".main.bean.ft-s1-j1.tmp").write_bytes(b"X\n")
    (ledger / ".main.bean.ft-restore-s1-j1.tmp").write_bytes(b"Y\n")
    out6 = recover_journal(fs, c5.journal_dir, "s1-j1")
    assert isinstance(out6, Stopped), out6
    kept = sorted((ledger / n).read_bytes() for n in os.listdir(ledger) if ".kept-" in n and not n.startswith("._"))
    assert kept == [b"X\n", b"Y\n"], kept


class Died(BaseException):
    """The save's process died (a crash injected through a wrapper around the real adapter)."""


class DyingDir:
    def __init__(self, inner: object, die_at: str) -> None:
        self._inner = inner
        self._die_at = die_at

    def rename_excl(self, src: str, dst: str) -> None:
        if dst.endswith(self._die_at):
            raise Died
        self._inner.rename_excl(src, dst)  # type: ignore[attr-defined]

    def remove(self, name: str) -> None:
        if self._die_at == "<remove>":
            raise Died
        self._inner.remove(name)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def __enter__(self) -> DyingDir:
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()  # type: ignore[attr-defined]


class DyingFS:
    def __init__(self, inner: FileSystem, die_at: str) -> None:
        self._inner = inner
        self._die_at = die_at

    def resolve(self, path: str) -> object:
        return self._inner.resolve(path)

    def same_path(self, now: str, recorded: str) -> bool:
        return self._inner.same_path(now, recorded)

    def open_dir(self, path: str) -> DyingDir:
        return DyingDir(self._inner.open_dir(path), self._die_at)
