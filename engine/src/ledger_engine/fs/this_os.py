"""The one place the engine picks its file-system adapters for the operating system it runs on (S0113;
`design/specs/engine-jobs.md` §14). Every caller outside `ledger_engine.fs` takes its real port from here, never
from an adapter module, so an adapter states its own platform once and is never imported elsewhere
(`tests/architecture/test_platform_selection.py`).

macOS gets `ledger_engine.fs.darwin`, Linux `ledger_engine.fs.linux` (S0125, BL-0266). Windows has its adapter,
`ledger_engine.fs.windows` (S0128, BL-0280; the mirror's pointer file since S0130, BL-0206), but **the product does not
select it yet**: until BL-0087 turns writes on, `for_this_os` and `legacy_locks_for_this_os` refuse there as on any
other operating system without a port. Only a test reaches the Windows adapter through this selector, inside
`ledger_engine.fs.under_test.windows_port_under_test()` (a context variable, off by default, in a module no product
module may reach: `tests/architecture/test_platform_selection.py`), so the engine's real-disk tests run the product's
own code paths on the Windows runner. The engine lock is the one Windows file operation the product takes
(`engine_lock_folder`, `take_engine_lock`, called only by `protocol/server.py`): a guard, written only in the app
folder's `locks/`. Elsewhere `UnsupportedPlatform` is raised when a job asks for the port: the engine still starts and
answers its handshake and ping there; an op that needs the port is refused `engine.unsupported_platform`, naming the
platform (BL-0261)."""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Callable

from ledger_engine.fs import under_test
from ledger_engine.fs.port import DirHandle, FileSystem, FolderId, LegacyLocks

if sys.platform == "darwin":
    from ledger_engine.fs.darwin import DarwinFS, DarwinLegacyLocks
elif sys.platform == "linux":
    from ledger_engine.fs import linux

PORTED = ("darwin", "linux")


class UnsupportedPlatform(OSError):
    """An `OSError` (`ENOTSUP`): a file operation refuses on this OS like on any other I/O failure."""

    def __init__(self) -> None:
        super().__init__(errno.ENOTSUP, f"the write path's file-system port is for macOS and Linux, not {sys.platform}")


def supported() -> bool:
    """Whether this operating system has a file-system port the product uses (Windows only under test)."""
    return sys.platform in PORTED or (sys.platform == "win32" and under_test.selected())


def for_this_os(on_fallback: Callable[[], None] | None = None) -> FileSystem:
    """The real file-system port for this operating system; elsewhere than macOS and Linux it raises
    `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        return DarwinFS(on_fallback)
    if sys.platform == "linux":
        return linux.LinuxFS(on_fallback)
    if sys.platform == "win32" and under_test.selected():
        from ledger_engine.fs import windows  # imported only here: the product never loads it (S0128)

        return windows.WindowsFS(on_fallback)
    raise UnsupportedPlatform


def legacy_locks_for_this_os() -> LegacyLocks:
    """The legacy app's write lock for this operating system (Copy only; INV-26); elsewhere than macOS and Linux it
    raises `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        return DarwinLegacyLocks()
    if sys.platform == "linux":
        return linux.LinuxLegacyLocks()
    if sys.platform == "win32" and under_test.selected():
        from ledger_engine.fs import windows

        return windows.WindowsLegacyLocks()
    raise UnsupportedPlatform


def folder_identity(fd: int) -> FolderId:
    """The identity of the folder the descriptor `fd` holds (`port.FolderId`), as the sidecar records the app folder's
    at start: `(st_dev, st_ino)` on macOS; with the creation time on Linux where the file system keeps one (BL-0262).
    Elsewhere `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)
    if sys.platform == "linux":
        return linux.folder_identity(fd)
    raise UnsupportedPlatform


def engine_lock_folder(folder: str) -> DirHandle:
    """Windows only: the app folder at `folder` opened through the Windows adapter, for the engine lock (§5.1; its
    identity is checked with `port.same_folder`, then `take_engine_lock`). Not behind the test switch
    (`under_test`): the lock is a guard and writes only in the app folder's `locks/`. Elsewhere `UnsupportedPlatform`
    (the POSIX lock is `protocol.server`'s `flock`)."""
    if sys.platform == "win32":
        from ledger_engine.fs import windows

        return windows.WindowsFS().open_dir(folder)
    raise UnsupportedPlatform


def take_engine_lock(app: DirHandle, locks: str, name: str) -> None:
    """Windows only: the engine lock `<app>/<locks>/<name>` taken with `LockFileEx` and held for the process's life
    (`windows.take_engine_lock`); `app` is `engine_lock_folder`'s handle. Elsewhere `UnsupportedPlatform`."""
    if sys.platform == "win32":
        from ledger_engine.fs import windows

        if not isinstance(app, windows.WindowsDir):
            raise TypeError(app)
        windows.take_engine_lock(app, locks, name)
        return
    raise UnsupportedPlatform
