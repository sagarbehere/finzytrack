"""The app's machine-local folder at start (S0100, BL-0163 (a); `design/specs/engine-jobs.md` §6.1 step 8, §15;
ADR-0012 amendment of 2026-11-11).

Owner, 2026-11-10, in session: "Sidecar makes it once (Recommended)": "The sidecar creates the app folder at start and
syncs its parent once; if that fails, the app says so at start. The save and recovery then write and sync only inside
the app folder, never in or above its parent."

`prepare(path)` runs first in `app.main`, before the log (which lives in the folder): the folder itself is made if it
is missing (one `mkdir`, mode 0700: never its parents, which belong to the system), and its parent is synced at every
start, whether the folder was made now or by an earlier start that may have ended before its sync, so the folder's
entry is durable before any save writes its journal in it (a link at the path: the real folder's parent too, since the
save and recover-all follow it). The sync is the engine's `durable_sync` rule (engine-jobs §6.2): `F_FULLFSYNC` on
macOS, `fsync` where the volume refuses it, `fsync` elsewhere. Any failure is an `AppFolderRefusal` with its text
(`msg:start.app_folder`): the sidecar then exits before its handshake.

S0102 (BL-0244; owner, 2026-11-11: "BL-0244 slice in M1a (Recommended)"): `prepare` answers the folder's identity
`(st_dev, st_ino)` (links followed, as the save and recover-all follow them), an `AppFolder`. Every process of ours
that writes in the app folder later checks it against the object it is about to write in (`AppFolder.open`: the
sidecar's lock wait and the bytecode fill here, the engine with `--app-folder-id`), so a folder removed while the app
runs, or another folder put in its place, gets nothing of ours: its entry was never made durable by our start.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from app.platform.engine import AppFolderChangedError

FOLDER_MODE = 0o700


def directory_flags() -> int:
    """`os.open`'s flags for a folder read through its descriptor (`dir_fd`), never inherited by a child."""
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC


@dataclass(frozen=True, slots=True)
class AppFolder:
    """The app folder as the start made it durable: its path and its identity then."""

    path: Path
    dev: int
    ino: int

    @property
    def identity(self) -> tuple[int, int]:
        return (self.dev, self.ino)

    @classmethod
    def of(cls, path: Path) -> AppFolder:
        """The folder at `path` as it is now (links followed)."""
        st = os.stat(path)
        return cls(path, st.st_dev, st.st_ino)

    def open(self) -> int:
        """A descriptor on the folder, only while it is still the object of the start; the caller closes it and
        writes only through it (`dir_fd`). Gone, not a folder, or another object: `AppFolderChangedError`."""
        try:
            fd = os.open(self.path, directory_flags())
        except (FileNotFoundError, NotADirectoryError) as e:
            raise AppFolderChangedError(e.errno, "the app folder is missing", str(self.path)) from None
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != self.identity:
            os.close(fd)
            raise AppFolderChangedError(errno.ESTALE, "the app folder was replaced", str(self.path))
        return fd

    def same_now(self) -> bool:
        """Whether the path names the object of the start now (a missing folder: no)."""
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        return (st.st_dev, st.st_ino) == self.identity


@dataclass(frozen=True, slots=True)
class AppFolderRefusal:
    """The folder could not be made or made durable. `errno`: the system's answer, when it gave one."""

    folder: str
    errno: int | None

    @property
    def text(self) -> str:
        return refusal_text(self.folder, None if self.errno is None else os.strerror(self.errno))


def refusal_text(folder: str, answer: str | None) -> str:
    """`msg:start.app_folder`: what the user reads when the app cannot start (the shell shows it: BL-0095)."""
    said = f" ({answer})" if answer else ""
    parent = folder.rstrip("/").rpartition("/")[0] or "/"
    return (
        f"Finzytrack cannot start: it could not set up its data folder {folder}{said}. Check that your user account "
        f"may write in {parent}, then open Finzytrack again."
    )


REFUSED_FULL_SYNC = (errno.ENOTSUP, errno.EINVAL, errno.ENOTTY)
"""What a volume answers when it cannot do `F_FULLFSYNC`: then `fsync`, as the engine's `durable_sync` does
(`ledger_engine.fs.darwin`; S0100 round 1, red team F4)."""


def _full_sync(fd: int) -> None:
    if sys.platform == "darwin":
        import fcntl  # POSIX only

        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        except OSError as e:
            if e.errno not in REFUSED_FULL_SYNC:
                raise
            os.fsync(fd)
    else:  # pragma: no cover (macOS is the only platform built today)
        os.fsync(fd)


def prepare(path: Path) -> AppFolder | AppFolderRefusal:
    """Make `path` if it is missing (its parent must exist) and sync its parent; the folder with its identity when both
    hold (recorded before the sync, so the folder whose entry the sync made durable is the one identified)."""
    folder = str(path)
    try:
        try:
            os.mkdir(path, FOLDER_MODE)
        except FileExistsError:
            if not stat.S_ISDIR(os.stat(path).st_mode):
                return AppFolderRefusal(folder, errno.ENOTDIR)
        made = AppFolder.of(path)
        for parent in dict.fromkeys((path.parent, path.resolve(strict=True).parent)):  # a link: its folder's too
            fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC)
            try:
                _full_sync(fd)
            finally:
                os.close(fd)
    except OSError as e:
        return AppFolderRefusal(folder, e.errno)
    return made
