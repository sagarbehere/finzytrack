"""The app's machine-local folder at start (S0100, BL-0163 (a); `design/specs/engine-jobs.md` §6.1 step 8, §15;
ADR-0012 amendment of 2026-11-11).

Owner, 2026-11-10, in session: "Sidecar makes it once (Recommended)": "The sidecar creates the app folder at start and
syncs its parent once; if that fails, the app says so at start. The save and recovery then write and sync only inside
the app folder, never in or above its parent."

`prepare(path)` runs first in `app.main`, before the log (which lives in the folder): the folder itself is made if it
is missing (one `mkdir`, mode 0700: never its parents, which belong to the system), and its parent is synced at every
start, whether the folder was made now or by an earlier start that may have ended before its sync, so the folder's
entry is durable before any save writes its journal in it (a link at the path: the real folder's parent too, since the
save and recover-all follow it). The sync is `F_FULLFSYNC` on macOS (the engine's
`durable_sync`, engine-jobs §6.2), `fsync` elsewhere. Any failure is an `AppFolderRefusal` with its text
(`msg:start.app_folder`): the sidecar then exits before its handshake.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

FOLDER_MODE = 0o700


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


def _full_sync(fd: int) -> None:
    if sys.platform == "darwin":
        import fcntl  # POSIX only

        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:  # pragma: no cover (macOS is the only platform built today)
        os.fsync(fd)


def prepare(path: Path) -> AppFolderRefusal | None:
    """Make `path` if it is missing (its parent must exist) and sync its parent; None when both hold."""
    folder = str(path)
    try:
        try:
            os.mkdir(path, FOLDER_MODE)
        except FileExistsError:
            if not stat.S_ISDIR(os.stat(path).st_mode):
                return AppFolderRefusal(folder, errno.ENOTDIR)
        for parent in dict.fromkeys((path.parent, path.resolve(strict=True).parent)):  # a link: its folder's too
            fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC)
            try:
                _full_sync(fd)
            finally:
                os.close(fd)
    except OSError as e:
        return AppFolderRefusal(folder, e.errno)
    return None
