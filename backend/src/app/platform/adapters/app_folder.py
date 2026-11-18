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
`(st_dev, st_ino)` (links followed, as the save and recover-all follow them), an `AppFolder`; on Linux with the
folder's creation time too (`statx`'s birth time, S0125, BL-0262: ext4 gives a folder removed and made again at once
the freed inode number), as the engine asks it (`ledger_engine.fs.this_os.folder_identity`); on Windows the volume
serial and the file ID, with the file ID as `born` on NTFS and ReFS (S0129, `windows_files`), as the engine's Windows
adapter gives it (`port.folder_id`). Every process of ours that writes in the app folder later checks it against the
object it is about to write in (`AppFolder.open`: the sidecar's lock wait and the bytecode fill here, the engine with
`--app-folder-id`), so a folder removed while the app runs, or another folder put in its place, gets nothing of
ours: its entry was never made durable by our start.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.platform.adapters.windows_pending import NotOnWindowsError
from app.platform.engine import AppFolderChangedError

FOLDER_MODE = 0o700


def directory_flags() -> int:
    """`os.open`'s flags for a folder read through its descriptor (`dir_fd`), never inherited by a child."""
    if sys.platform == "win32":
        raise NotOnWindowsError
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC


AT_EMPTY_PATH: Final = 0x1000
STATX_BTIME: Final = 0x800
STATX_BASIC: Final = 0x7FF


def _birth(fd: int) -> int | None:
    """Linux: the creation time in ns of the folder the descriptor `fd` holds, by `statx` (glibc 2.28), None where the
    file system keeps none; an error raises `OSError`."""
    try:
        statx = ctypes.CDLL(None, use_errno=True).statx
    except AttributeError:  # glibc before 2.28 (S0125 round 1, verifier F4): refused at start, never a crash
        raise OSError(errno.ENOTSUP, "glibc 2.28 or later is needed (statx)") from None
    buf = ctypes.create_string_buffer(256)  # `struct statx` (<linux/stat.h>)
    if statx(fd, b"", AT_EMPTY_PATH, STATX_BASIC | STATX_BTIME, buf) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    if not struct.unpack_from("<I", buf.raw, 0)[0] & STATX_BTIME:
        return None
    sec, nsec = struct.unpack_from("<qI", buf.raw, 80)
    return sec * 1_000_000_000 + nsec


def _identity_of_fd(fd: int) -> tuple[int, int, int | None]:
    st = os.fstat(fd)
    return st.st_dev, st.st_ino, _birth(fd) if sys.platform == "linux" else None


def _identity_at(path: Path) -> tuple[int, int, int | None]:
    """The identity of the folder at `path` now (links followed): on Linux through an `O_PATH` descriptor, which needs
    no right to read the folder, so one object answers every part; on Windows through a handle (the volume serial and
    the file ID, `windows_files.identity`; S0129); elsewhere `stat`."""
    if sys.platform == "win32":
        from app.platform.adapters import windows_files  # Windows only

        return windows_files.identity_at(path)
    if sys.platform == "linux":
        fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            return _identity_of_fd(fd)
        finally:
            os.close(fd)
    st = os.stat(path)
    return st.st_dev, st.st_ino, None


@dataclass(frozen=True, slots=True)
class AppFolder:
    """The app folder as the start made it durable: its path and its identity then. `born`: its creation time in ns
    on Linux (BL-0262), None on macOS (APFS reuses no inode number) and where the file system keeps none; on Windows
    (S0129) `dev` and `ino` are the volume serial and the file ID, and `born` the file ID again on NTFS and ReFS (never
    the creation time, which tunnelling repeats), the creation time on FAT32 and exFAT, as the engine computes it."""

    path: Path
    dev: int
    ino: int
    born: int | None = None

    @property
    def identity(self) -> tuple[int, int] | tuple[int, int, int]:
        return (self.dev, self.ino) if self.born is None else (self.dev, self.ino, self.born)

    @property
    def id_text(self) -> str:
        """`--app-folder-id`: `DEV:INO`, or `DEV:INO:BORN` with a creation time."""
        return ":".join(str(n) for n in self.identity)

    @classmethod
    def of(cls, path: Path) -> AppFolder:
        """The folder at `path` as it is now (links followed)."""
        return cls(path, *_identity_at(path))

    def open(self) -> int:
        """A descriptor on the folder, only while it is still the object of the start; the caller closes it and
        writes only through it (`dir_fd`). Gone, not a folder, another object, or a folder that cannot be opened to
        be judged (a permission taken away: fail closed, S0102 round 1, red team F6): `AppFolderChangedError`. POSIX
        only: on Windows the one writer of the sidecar's in the app folder, the bytecode fill, does not run (S0129),
        and the engine-lock wait holds the folder itself (`engine_lock`)."""
        if sys.platform == "win32":
            raise NotOnWindowsError
        try:
            fd = os.open(self.path, directory_flags())
        except OSError as e:
            raise AppFolderChangedError(
                e.errno, "the app folder is missing or cannot be read", str(self.path)
            ) from None
        try:
            now = AppFolder(self.path, *_identity_of_fd(fd))
        except OSError as e:
            os.close(fd)
            raise AppFolderChangedError(e.errno, "the app folder cannot be read", str(self.path)) from None
        if now.identity != self.identity:
            os.close(fd)
            raise AppFolderChangedError(errno.ESTALE, "the app folder was replaced", str(self.path))
        return fd

    def same_now(self) -> bool:
        """Whether the path names the object of the start now (a missing folder: no)."""
        try:
            return AppFolder(self.path, *_identity_at(self.path)).identity == self.identity
        except OSError:
            return False


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


def _sync_folder(path: Path) -> None:
    if sys.platform == "win32":  # S0129: `FlushFileBuffers` on the folder opened for adding a file
        from app.platform.adapters import windows_files  # Windows only

        windows_files.sync_folder(path)
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC)
    try:
        _full_sync(fd)
    finally:
        os.close(fd)


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
            _sync_folder(parent)
    except OSError as e:
        return AppFolderRefusal(folder, e.errno)
    return made
