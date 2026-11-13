"""The one place the engine picks its file-system adapters for the operating system it runs on (S0113;
`design/specs/engine-jobs.md` §14). Every caller outside `ledger_engine.fs` takes its real port from here, never
from an adapter module, so an adapter states its own platform once and is never imported elsewhere
(`tests/architecture/test_platform_selection.py`).

macOS gets `ledger_engine.fs.darwin`. Any other operating system gets `UnsupportedPlatform` when a job asks for the
port: the engine still starts and answers its handshake and ping there; only file operations refuse (BL-0087 builds
the Linux and Windows adapters).
"""

from __future__ import annotations

import errno
import sys
from collections.abc import Callable

from ledger_engine.fs.port import FileSystem, LegacyLocks

if sys.platform == "darwin":
    from ledger_engine.fs.darwin import DarwinFS, DarwinLegacyLocks


class UnsupportedPlatform(OSError):
    """An `OSError` (`ENOTSUP`): a file operation refuses on this OS like on any other I/O failure."""

    def __init__(self) -> None:
        super().__init__(errno.ENOTSUP, "the write path's file-system port is macOS-only until BL-0087")


def for_this_os(on_fallback: Callable[[], None] | None = None) -> FileSystem:
    """The real file-system port for this operating system; elsewhere than macOS it raises `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        return DarwinFS(on_fallback)
    raise UnsupportedPlatform


def legacy_locks_for_this_os() -> LegacyLocks:
    """The legacy app's write lock for this operating system (Copy only; INV-26); elsewhere than macOS it raises
    `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        return DarwinLegacyLocks()
    raise UnsupportedPlatform
