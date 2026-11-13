"""The skip for tests of macOS-only code (S0113), written so that pyright checks the code after it for macOS only and
the Linux and Windows type checks (`--pythonplatform`) never see the macOS-only names:

    if sys.platform != "darwin":
        raise pytest.skip.Exception(MACOS_ONLY, allow_module_level=True)  # a whole module, before its macOS imports

and the same `raise` (without `allow_module_level`) inside a test. It must be a `raise`: pyright leaves the code after
it unchecked, function bodies defined there included, only after a statement it knows ends the block. A call
(`pytest.skip(...)`, even one declared `NoReturn`) ends only the statements of its own scope, and the functions
defined after it are still checked (probed with pyright 1.1.414).
"""

from __future__ import annotations

from typing import Final

MACOS_ONLY: Final = "macOS-only until BL-0087 (the Linux and Windows file-system adapters)"
