"""The sidecar's few file operations on Windows (S0129, BL-0281; `design/specs/engine-jobs.md` §5.1, §6.1 step 8):
the app folder's identity, the sync of its parent at start, and the wait for the engine lock. The engine's Windows
adapter (`ledger_engine.fs.windows`, S0128) is the model; the sidecar never imports the engine (ADR-0054), so the few
calls it needs are bound here, with the same rules:

- **Identity** (`identity`): `FileIdInfo`'s 64-bit volume serial and 128-bit file ID on NTFS and ReFS, and, as `born`,
  the file ID again, never the creation time (tunnelling gives a folder made again at once its predecessor's creation
  time; S0128, verifier Q-C); on FAT32 and exFAT, which have no `FileIdInfo`, the 32-bit serial, the file index and the
  creation time; on any other file system no `born`. The same value the engine computes (`port.folder_id` over
  `WindowsDir`), so `--app-folder-id` is checked by the engine as the sidecar recorded it (tested on the runner).
- **A folder is held without delete sharing** (`open_folder`), so while the handle is open no program can remove,
  rename or replace it: its path names the object whose identity was read. Every handle is closed before return.
- **The sync** (`sync_folder`): `FlushFileBuffers` on the folder opened for adding a file (a read-only handle is
  refused the flush; probed in S0126), as the engine's `WindowsDir.sync`.
- **The mirror's pointer** (`read_shared`; S0130, BL-0206; `design/specs/mirror/publish.md` §7): a bounded read of
  `current.txt` opened sharing reading, writing and deleting, so a pin's read never refuses the engine's rename over it
  (Python's `open` shares no deletion: probed, S0126 and S0130's P4), and never through a link at its name.
- **The engine lock** (`wait_lock`): `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK)` over the whole file, waiting, then released
  and closed at once, the range and sharing the engine takes (`ledger_engine.fs.windows.take_engine_lock`). The file is
  opened only if it exists: an engine that holds the lock opened or made the file and keeps it open without delete
  sharing, so a missing file means no engine holds it, and the sidecar makes nothing.

Windows only: importing it elsewhere fails.
"""

from __future__ import annotations

import ctypes
import errno
import struct
import sys
from collections.abc import Generator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any, Final

if sys.platform != "win32":
    raise ImportError("the sidecar's Windows file operations")  # noqa: TRY003 (one raise site)

GENERIC_READ: Final = 0x80000000
GENERIC_WRITE: Final = 0x40000000
SYNCHRONIZE: Final = 0x00100000
FILE_LIST_DIRECTORY: Final = 0x0001
FILE_ADD_FILE: Final = 0x0002
FILE_TRAVERSE: Final = 0x0020
FILE_READ_ATTRIBUTES: Final = 0x0080
FILE_SHARE_READ_WRITE: Final = 0x3
FILE_SHARE_ALL: Final = 0x7
OPEN_EXISTING: Final = 3
FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x400
FILE_ATTRIBUTE_DIRECTORY: Final = 0x10
FILE_BASIC_INFO: Final = 0
FILE_ID_INFO: Final = 18
LOCKFILE_EXCLUSIVE_LOCK: Final = 0x2
WHOLE_FILE: Final = 0xFFFFFFFF
ERROR_FILE_NOT_FOUND: Final = 2
ERROR_PATH_NOT_FOUND: Final = 3
ERROR_NOT_SUPPORTED: Final = 50
ERROR_INVALID_PARAMETER: Final = 87
EPOCH_AS_FILETIME: Final = 116_444_736_000_000_000
"""1970-01-01 in 100 ns units since 1601-01-01 (a FILETIME)."""
FS_SERIAL64: Final = frozenset({"ntfs", "refs"})
FS_SERIAL32: Final = frozenset({"fat", "fat32", "exfat"})
INVALID_HANDLE: Final = ctypes.c_void_p(-1).value


class OVERLAPPED(ctypes.Structure):
    _fields_ = (
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    )


def _sig(fn: Any, args: list[Any], res: Any) -> Any:
    fn.argtypes = args
    fn.restype = res
    return fn


class _Api:
    def __init__(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h, d, b, p = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_void_p
        self.CreateFileW = _sig(k32.CreateFileW, [wintypes.LPCWSTR, d, d, p, d, d, h], h)
        self.CloseHandle = _sig(k32.CloseHandle, [h], b)
        self.FlushFileBuffers = _sig(k32.FlushFileBuffers, [h], b)
        self.ReadFile = _sig(k32.ReadFile, [h, p, d, ctypes.POINTER(d), p], b)
        self.GetInfo = _sig(k32.GetFileInformationByHandleEx, [h, ctypes.c_int, p, d], b)
        self.GetFileInformationByHandle = _sig(k32.GetFileInformationByHandle, [h, p], b)
        self.GetVolumeInformationByHandleW = _sig(
            k32.GetVolumeInformationByHandleW,
            [h, wintypes.LPWSTR, d, ctypes.POINTER(d), ctypes.POINTER(d), ctypes.POINTER(d), wintypes.LPWSTR, d],
            b,
        )
        self.LockFileEx = _sig(k32.LockFileEx, [h, d, d, d, d, ctypes.POINTER(OVERLAPPED)], b)
        self.UnlockFileEx = _sig(k32.UnlockFileEx, [h, d, d, d, ctypes.POINTER(OVERLAPPED)], b)


def _error(what: str, path: Path | None = None, code: int | None = None) -> OSError:
    """The last Win32 error as Python's `OSError` (its `errno` from Windows' own mapping, so a missing file is
    `FileNotFoundError`)."""
    c = ctypes.get_last_error() if code is None else code
    return OSError(None, f"{what}: {ctypes.FormatError(c).strip()}", None if path is None else str(path), c)


def _open(api: _Api, path: Path, access: int, share: int, flags: int) -> int:
    h = api.CreateFileW(str(path), access, share, None, OPEN_EXISTING, flags, None)
    if h is None or h == INVALID_HANDLE:
        raise _error("open", path)
    return int(h)


@contextmanager
def open_folder(path: Path) -> Generator[tuple[_Api, int]]:
    """The folder at `path` (links followed, as the save follows them), held without delete sharing: while the
    handle is open it cannot be removed, renamed or replaced. `NotADirectoryError` if it is not a folder."""
    api = _Api()
    h = _open(
        api,
        path,
        FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
        FILE_SHARE_READ_WRITE,
        FILE_FLAG_BACKUP_SEMANTICS,
    )
    try:
        if not _attributes(api, h) & FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.ENOTDIR, "not a folder", str(path))
        yield api, h
    finally:
        api.CloseHandle(h)


def read_shared(path: Path, limit: int) -> bytes:
    """At most `limit` bytes of the regular file at `path`, read through a handle that shares reading, writing and
    deleting (another program may rename over it or remove it meanwhile) and that does not follow a link or a
    junction at its name. `FileNotFoundError` when nothing is there; `OSError` for a folder or a link."""
    api = _Api()
    h = _open(api, path, GENERIC_READ | SYNCHRONIZE, FILE_SHARE_ALL, FILE_FLAG_OPEN_REPARSE_POINT)
    try:
        attrs = _attributes(api, h)
        if attrs & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        out = b""
        while len(out) < limit:
            buf = ctypes.create_string_buffer(limit - len(out))
            n = wintypes.DWORD()
            if not api.ReadFile(h, buf, limit - len(out), ctypes.byref(n), None):
                raise _error("read", path)
            if n.value == 0:
                break
            out += buf.raw[: n.value]
        return out
    finally:
        api.CloseHandle(h)


def _attributes(api: _Api, h: int) -> int:
    return struct.unpack_from("<I", _info(api, h, FILE_BASIC_INFO, 40), 32)[0]


def _info(api: _Api, h: int, cls: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    if not api.GetInfo(h, cls, buf, size):
        raise _error("GetFileInformationByHandleEx")
    return buf.raw


def _fs_type(api: _Api, h: int) -> str:
    name = ctypes.create_unicode_buffer(261)
    serial, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    if not api.GetVolumeInformationByHandleW(
        h, None, 0, ctypes.byref(serial), ctypes.byref(maxlen), ctypes.byref(flags), name, 261
    ):
        raise _error("GetVolumeInformationByHandleW")
    return name.value.lower()


def identity(api: _Api, h: int) -> tuple[int, int, int | None]:
    """`(dev, ino, born)` of the folder `h` holds (the module's docstring, "Identity")."""
    buf = ctypes.create_string_buffer(24)
    if api.GetInfo(h, FILE_ID_INFO, buf, 24):
        dev, ino = struct.unpack_from("<Q", buf.raw, 0)[0], int.from_bytes(buf.raw[8:24], "little")
    else:
        if ctypes.get_last_error() not in (ERROR_INVALID_PARAMETER, ERROR_NOT_SUPPORTED):
            raise _error("FileIdInfo")
        info = ctypes.create_string_buffer(52)
        if not api.GetFileInformationByHandle(h, info):
            raise _error("GetFileInformationByHandle")
        dev, _, _, _, high, low = struct.unpack_from("<IIIIII", info.raw, 28)
        ino = (high << 32) | low
    fs = _fs_type(api, h)
    if fs in FS_SERIAL64:
        return dev, ino, ino
    if fs in FS_SERIAL32:
        created = struct.unpack_from("<q", _info(api, h, FILE_BASIC_INFO, 40), 0)[0]
        return dev, ino, 0 if created == 0 else (created - EPOCH_AS_FILETIME) * 100
    return dev, ino, None


def identity_at(path: Path) -> tuple[int, int, int | None]:
    """The identity of the folder at `path` now (`OSError` if it is missing or not a folder)."""
    with open_folder(path) as (api, h):
        return identity(api, h)


def sync_folder(path: Path) -> None:
    """`FlushFileBuffers` on the folder at `path` (the module's docstring, "The sync")."""
    api = _Api()
    h = _open(api, path, FILE_ADD_FILE | SYNCHRONIZE, FILE_SHARE_ALL, FILE_FLAG_BACKUP_SEMANTICS)
    try:
        if not api.FlushFileBuffers(h):
            raise _error("FlushFileBuffers", path)
    finally:
        api.CloseHandle(h)


def wait_lock(api: _Api, lock: Path) -> None:
    """Take the engine lock at `lock`, waiting while another process holds it, and release it at once; nothing if
    the file (or its folder) is missing. The caller holds the app folder (`open_folder`), so `lock` is in it."""
    try:
        h = _open(api, lock, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ_WRITE, 0)
    except OSError as e:
        if e.winerror in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
            return
        raise
    try:
        if not api.LockFileEx(h, LOCKFILE_EXCLUSIVE_LOCK, 0, WHOLE_FILE, WHOLE_FILE, OVERLAPPED()):
            raise _error("LockFileEx", lock)
        api.UnlockFileEx(h, 0, WHOLE_FILE, WHOLE_FILE, OVERLAPPED())
    finally:
        api.CloseHandle(h)


__all__ = ["identity", "identity_at", "open_folder", "sync_folder", "wait_lock"]
