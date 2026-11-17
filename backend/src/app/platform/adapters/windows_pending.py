"""The sidecar's POSIX-only code on Windows (S0127, BL-0260): each function that uses a POSIX-only name (process groups
and `waitid`, `select.poll`, `O_NOFOLLOW`, `O_CLOEXEC`) starts with

    if sys.platform == "win32":
        raise NotOnWindowsError

so the Windows type check (`scripts/pyright_platforms.py`) reads the rest for macOS and Linux only, and on Windows the
call refuses as an I/O failure (`ENOTSUP`) instead of failing on a missing name. The Windows port of the engine's
process group, its supervision and the app folder is BL-0281; the sidecar does not run on Windows before it."""

from __future__ import annotations

import errno
from typing import Final

NOT_ON_WINDOWS: Final = "the sidecar's engine supervision and app folder on Windows are BL-0281"


class NotOnWindowsError(OSError):
    """An `OSError` (`ENOTSUP`): this POSIX-only operation has no Windows port yet (BL-0281)."""

    def __init__(self) -> None:
        super().__init__(errno.ENOTSUP, NOT_ON_WINDOWS)
