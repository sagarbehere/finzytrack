"""The engine's Job Object on Windows (S0129, BL-0281; engine-jobs §5.2). Test-first stub: the interface the Windows
tests name, refusing as every POSIX-only sidecar function does on Windows until the port lands."""

from __future__ import annotations

import subprocess
from typing import Any

from app.platform.adapters.engine_group import GroupOps
from app.platform.adapters.windows_pending import NotOnWindowsError


class EngineJob:
    """One engine's Job Object."""

    ops: GroupOps

    @classmethod
    def around(cls, proc: subprocess.Popen[Any]) -> EngineJob:
        raise NotOnWindowsError

    def member_ids(self) -> list[int] | None:
        raise NotOnWindowsError

    def close(self) -> None:
        raise NotOnWindowsError
