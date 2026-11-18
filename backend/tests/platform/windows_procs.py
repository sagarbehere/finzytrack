"""Process and lock probes for the sidecar's Windows tests (S0129, BL-0281): a process watched through a handle held
from the moment its pid is known (so the pid cannot pass to another program while the test looks at it), a hard end
(`TerminateProcess`), and a lock probe that never waits. Test code only; Windows only (elsewhere the import fails, and
pyright reads the rest for Windows: `scripts/pyright_platforms.py`)."""

from __future__ import annotations

import ctypes
import msvcrt
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Final

if sys.platform != "win32":
    raise ImportError("Windows only")  # noqa: TRY003 (one raise site)

SYNCHRONIZE: Final = 0x00100000
PROCESS_TERMINATE: Final = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
WAIT_OBJECT_0: Final = 0
WAIT_TIMEOUT: Final = 0x102
GENERIC_READ: Final = 0x80000000
OPEN_EXISTING: Final = 3


def _sig(fn: Any, args: list[Any], res: Any) -> Any:
    fn.argtypes = args
    fn.restype = res
    return fn


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_OpenProcess = _sig(_k32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
_Wait = _sig(_k32.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
_Terminate = _sig(_k32.TerminateProcess, [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
_Close = _sig(_k32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)


class Watched:
    """A process by its pid, through a handle opened now and closed by `close()`."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        h = _OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        self._h: int | None = int(h)

    def gone(self, timeout: float = 0.0) -> bool:
        """Whether it has ended (its handles are closed by then), waiting at most `timeout` seconds."""
        assert self._h is not None
        return _Wait(self._h, int(timeout * 1000)) == WAIT_OBJECT_0

    def end(self) -> None:
        """End it at once (`TerminateProcess`): a crash, as the OS or a user's Task Manager would end it."""
        assert self._h is not None
        _Terminate(self._h, 1)

    def close(self) -> None:
        if self._h is not None:
            _Close(self._h)
            self._h = None


def lock_free(path: Path) -> bool:
    """Whether another process could lock the file's first byte now (never waiting): the engine lock's whole-file
    `LockFileEx` and a test holder's `msvcrt.locking` of byte 0 both cover it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o600)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    else:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return True
    finally:
        os.close(fd)


def open_exclusively(path: Path) -> bool:
    """Whether the file can be opened now with no sharing at all: no other handle on it is open anywhere."""
    create = _sig(
        _k32.CreateFileW,
        [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ],
        wintypes.HANDLE,
    )
    h = create(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        return False
    _Close(h)
    return True
