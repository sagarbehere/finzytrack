"""Why a backend test skips on Windows (S0127, BL-0260), written as the engine's `tests/windows_pending.py`. Since
S0131 (BL-0290) the `backend-windows` CI job runs the whole suite with `pytest -rs`, so each skip prints its reason,
which names one of: a POSIX-only rule (process groups, signals, `flock`, descriptors, `ps`, permission bits, `resource`)
and the Windows tests of the same rule (`tests/platform/test_engine_windows.py`, S0129; `test_pin_windows.py`, S0130);
the engine's write path, which Windows refuses until BL-0087; or the open row that will turn the test on (BL-0296).
`test_engine_client.py` skips whole for the first reason, and its OS-agnostic tests live in
`test_engine_client_spawn.py`, which runs on Windows too (S0129 round 1). These lines also keep the Windows type check
(`scripts/pyright_platforms.py`) at 0 errors.
"""

from __future__ import annotations

from typing import Final

POSIX_SUPERVISION: Final = "POSIX groups, signals, flock, descriptors; Windows: test_engine_windows.py (S0129)"
TZSET: Final = "Windows has no `time.tzset`: its C runtime reads `TZ` only as a process starts"
WINDOWS_ONLY: Final = "the sidecar on Windows (S0129): these tests run on the Windows runner"
WRITE_PATH: Final = "the engine's write path (and the preview's error keys, BL-0282): Windows refuses it until BL-0087"
NO_SIGTERM: Final = (
    "Windows has no SIGTERM: `terminate()` ends the sidecar at once, with no graceful close; its stop there is BL-0296"
)
LINK_FORM: Final = (
    "the mirror's link form (macOS, Linux); Windows' pointer form: tests/mirror/test_pin_windows.py (S0130)"
)
NO_RUSAGE: Final = (
    "peak RSS through `resource`, POSIX only; the query's caps themselves are tested by the other query tests"
)
NO_MODE_BITS: Final = "POSIX permission bits: on Windows `chmod` sets only the read-only attribute"
SHELL_STAND_IN: Final = "the stand-in engine is a shell script"
RESOLVED_CASE: Final = (
    "Windows' `resolve` gives an existing folder its own spelling, so `BUILD/` there is the ignored `build/` itself"
)
