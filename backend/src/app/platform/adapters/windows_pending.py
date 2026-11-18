"""The sidecar's POSIX-only code on Windows (S0127, BL-0260): each function that uses a POSIX-only name (process groups
and `waitid`, `select.poll`, `O_NOFOLLOW`, `O_CLOEXEC`) starts with

    if sys.platform == "win32":
        raise NotOnWindowsError

so the Windows type check (`scripts/pyright_platforms.py`) reads the rest for macOS and Linux only, and on Windows the
call refuses as an I/O failure (`ENOTSUP`) instead of failing on a missing name. Since S0129 (BL-0281) the sidecar runs
on Windows with its own forms of the engine's group (`windows_job`), the app folder and the lock wait
(`windows_files`); what still raises this is POSIX-only by design: the descriptor-relative bytecode fill (no bytecode
directory on Windows yet, BL-0288), the reply pump's `poll`, and `PosixGroupOps`."""

from __future__ import annotations

import errno
from typing import Final

NOT_ON_WINDOWS: Final = "a POSIX-only operation of the sidecar (its Windows form is elsewhere or BL-0288)"


class NotOnWindowsError(OSError):
    """An `OSError` (`ENOTSUP`): this POSIX-only operation has no Windows form (the module's docstring)."""

    def __init__(self) -> None:
        super().__init__(errno.ENOTSUP, NOT_ON_WINDOWS)
