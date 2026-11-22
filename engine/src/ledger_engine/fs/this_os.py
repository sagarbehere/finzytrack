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

import ctypes
import errno
import os
import sys
from collections.abc import Callable
from typing import Final

from ledger_engine.fs import under_test
from ledger_engine.fs.port import CLOUD_DOWNLOAD_ERRORS, DirHandle, FileSystem, FolderId, LegacyLocks

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
    """Whether this operating system has a file-system port the product uses (Windows only under test), and on
    Windows whether this process runs in the engine's placeholder mode (`pin_placeholder_mode`; S0149): in any other
    mode, a refused pin included, every op that reads or writes ledger, journal or mirror files is refused, never run
    in another mode (the engine lock in the app folder is still taken: a guard, not a ledger file; S0149 r1, V7)."""
    return sys.platform in PORTED or (sys.platform == "win32" and under_test.selected() and pin_refused() is None)


def for_this_os(on_fallback: Callable[[], None] | None = None) -> FileSystem:
    """The real file-system port for this operating system; elsewhere than macOS and Linux it raises
    `UnsupportedPlatform`."""
    if sys.platform == "darwin":
        return DarwinFS(on_fallback)
    if sys.platform == "linux":
        return linux.LinuxFS(on_fallback)
    if sys.platform == "win32" and under_test.selected() and pin_refused() is None:  # S0149: another mode refuses
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


PLACEHOLDER_MODES = {0: "application_default", 1: "disguise_placeholder", 2: "expose_placeholders", 3: "disguise_full"}
"""`PHCM_*` (`ntifs.h`): how the cloud filter shows this process a cloud placeholder (OneDrive's Files On-Demand)."""


def placeholder_mode() -> int | None:
    """Windows only: this process's placeholder compatibility mode (`RtlQueryProcessPlaceholderCompatibilityMode`, a
    `PLACEHOLDER_MODES` key); None elsewhere. Read only, by the test op `test.placeholder_mode` (S0149, BL-0283: which
    mode the engine runs in as the sidecar starts it)."""
    if sys.platform == "win32":
        query = ctypes.WinDLL("ntdll").RtlQueryProcessPlaceholderCompatibilityMode
        query.argtypes = []
        query.restype = ctypes.c_byte
        return int(query())
    return None


PHCM_DISGUISE_PLACEHOLDER: Final = 1
"""The engine's placeholder mode (S0149, BL-0283; owner, 2026-11-22: "A"; ADR-0070): every cloud placeholder (OneDrive,
Dropbox, Google Drive, Box: any Cloud Files provider) is a plain file or folder to this process, read and replaced as
one; a read of a cloud-only file downloads it. Other reparse points (symbolic links, junctions, deduplicated files,
AppExecLinks) are not placeholders and stay visible (probed, the 2026-11-22 run 11: links and junctions are `LINK` in
every mode)."""


def pin_placeholder_mode() -> bool:
    """Windows only: this process's placeholder compatibility mode set to `PHCM_DISGUISE_PLACEHOLDER`
    (`RtlSetProcessPlaceholderCompatibilityMode`), at the start of every engine process that reads a ledger (the
    engine's `main`, the copy check's child): the mode a process gets is not the same for every start (the 2026-11-22
    run 11: 0 for the engine as the sidecar starts it, 1 for a child of the test process) and a child does not inherit
    a mode its parent set. True when the mode reads `PHCM_DISGUISE_PLACEHOLDER` after it (and off Windows, where there
    is nothing to pin). Nothing is kept: `supported()` and `for_this_os()` ask the mode itself (`pin_refused()`), so a
    process in any other mode, the pin refused or never made, refuses every op that reads or writes ledger, journal or
    mirror files (`engine.unsupported_platform`, its message naming the mode; the engine lock is still taken): a
    missing primitive refuses, never falls back."""
    if sys.platform != "win32":
        return True
    try:
        nt = ctypes.WinDLL("ntdll")
        setter = nt.RtlSetProcessPlaceholderCompatibilityMode
        setter.argtypes = [ctypes.c_byte]
        setter.restype = ctypes.c_byte
        setter(PHCM_DISGUISE_PLACEHOLDER)
    except (OSError, AttributeError):  # an old Windows without the call: the mode below says so
        return False
    return pin_refused() is None


def mode_refusal() -> str | None:
    """Why a file operation is refused on Windows when the port would otherwise be selected: this process is not in
    the engine's placeholder mode (`pin_refused()`); None otherwise, and off Windows, where the platform is the
    reason (S0149; `protocol.server.unsupported_message`)."""
    if sys.platform == "win32" and under_test.selected():
        return pin_refused()
    return None


def pin_refused() -> str | None:
    """Windows only: why this process is not in the engine's placeholder mode (the mode it reads, or that the mode
    cannot be read), or None when it is; None off Windows."""
    if sys.platform != "win32":
        return None
    try:
        mode = placeholder_mode()
    except (OSError, AttributeError):
        return "the placeholder mode cannot be read"
    if mode != PHCM_DISGUISE_PLACEHOLDER:
        return f"the placeholder mode is {mode} ({PLACEHOLDER_MODES.get(mode or 0, 'unknown')})"
    return None


def cloud_download_failed(fd: int) -> bool:
    """Windows only: after a read of the open file `fd` failed through the C runtime, which keeps no Win32 code
    (probed: a bare `EINVAL`, S0144 Fact 6), one byte read again at the same place with `ReadFile`: whether Windows
    refuses it with one of the cloud filter's download errors (`port.CLOUD_DOWNLOAD_ERRORS`; S0149, BL-0283). Never
    True off Windows, or for any other error."""
    if sys.platform != "win32":
        return False
    import msvcrt  # Windows only

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    read = k32.ReadFile
    read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    read.restype = ctypes.c_int
    buf = ctypes.create_string_buffer(1)
    got = ctypes.c_ulong()
    try:
        handle = msvcrt.get_osfhandle(fd)
    except OSError:
        return False
    if read(handle, buf, 1, ctypes.byref(got), None):
        return False
    return ctypes.get_last_error() in CLOUD_DOWNLOAD_ERRORS
