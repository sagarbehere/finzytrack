"""Why a backend test skips on Windows (S0127, BL-0260), written as the engine's `tests/windows_pending.py`: the
sidecar does not run on Windows before its engine supervision is ported (BL-0281), so its suite runs on macOS and
Linux only; these lines keep the Windows type check (`scripts/pyright_platforms.py`) at 0 errors meanwhile.

    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0281, allow_module_level=True)  # a whole module, before its POSIX imports
"""

from __future__ import annotations

from typing import Final

BL_0281: Final = "BL-0281: the sidecar's engine supervision on Windows (process groups, the Job Object, the locks)"
TZSET: Final = "Windows has no `time.tzset`: its C runtime reads `TZ` only as a process starts"
WINDOWS_ONLY: Final = "the sidecar on Windows (S0129): these tests run on the Windows runner"
