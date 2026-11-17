"""The reasons a test skips on Windows until the Windows port lands (S0127, BL-0279; ADR-0069's split), each naming the
backlog item that turns it on. Written as the macOS-only skip is (`tests/macos_only.py`), so pyright checks the code
after it for the other platforms only:

    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0280, allow_module_level=True)  # a whole module, before its POSIX imports

and the same `raise` (without `allow_module_level`) inside one test. The engine-windows CI job runs pytest with `-rs`,
so each skip prints its one line there.
"""

from __future__ import annotations

from typing import Final

BL_0280: Final = "BL-0280: the real-disk write path on Windows (the engine refuses file operations until its adapter)"
BL_0281: Final = "BL-0281: the engine's process group and signals on Windows (the Job Object)"
NO_FIFO: Final = "Windows has no FIFO in its file system: the non-blocking read it guards against cannot arise there"
NO_UID: Final = "Windows has no POSIX user and group ids or permission bits on files"
