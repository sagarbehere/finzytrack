"""Why a backend test skips on Windows (S0127, BL-0260), written as the engine's `tests/windows_pending.py`: the
POSIX-only test (process groups, signals, `flock`, descriptors) names the Windows tests that cover the same rule
there (`tests/platform/test_engine_windows.py`, S0129); the `backend-windows` CI job runs `pytest -rs`, so each skip
prints its reason. These lines also keep the Windows type check (`scripts/pyright_platforms.py`) at 0 errors.

    if sys.platform == "win32":
        raise pytest.skip.Exception(POSIX_SUPERVISION, allow_module_level=True)  # before its POSIX imports
"""

from __future__ import annotations

from typing import Final

POSIX_SUPERVISION: Final = "POSIX groups, signals, flock, descriptors; Windows: test_engine_windows.py (S0129)"
TZSET: Final = "Windows has no `time.tzset`: its C runtime reads `TZ` only as a process starts"
WINDOWS_ONLY: Final = "the sidecar on Windows (S0129): these tests run on the Windows runner"
