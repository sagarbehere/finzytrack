"""The one place the engine picks its file-system adapters for the operating system it runs on (S0113;
`design/specs/engine-jobs.md` §14). Every caller outside `ledger_engine.fs` takes its real port from here, never
from an adapter module, so an adapter states its own platform once and is never imported elsewhere
(`tests/architecture/test_platform_selection.py`).

macOS gets `ledger_engine.fs.darwin`, Linux `ledger_engine.fs.linux` (S0125, BL-0266). Any other operating system
gets `UnsupportedPlatform` when a job asks for the port: the engine still starts and answers its handshake and ping
there; an op that needs the port is refused `engine.unsupported_platform`, naming the platform (BL-0261; Windows is
BL-0087).
"""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Callable

from ledger_engine.fs.port import FileSystem, FolderId, LegacyLocks

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
    """Whether this operating system has a file-system port."""
    return sys.platform in PORTED


def for_this_os(on_fallback: Callable[[], None] | None = None) -> FileSystem:
    """The real file-system port for this operating system; elsewhere than macOS and Linux it raises
    `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        return DarwinFS(on_fallback)
    if sys.platform == "linux":
        return linux.LinuxFS(on_fallback)
    raise UnsupportedPlatform


def legacy_locks_for_this_os() -> LegacyLocks:
    """The legacy app's write lock for this operating system (Copy only; INV-26); elsewhere than macOS and Linux it
    raises `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        return DarwinLegacyLocks()
    if sys.platform == "linux":
        return linux.LinuxLegacyLocks()
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
