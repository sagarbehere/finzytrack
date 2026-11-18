"""Absolute paths in tests, spelled as this OS's engine sends them (S0131, BL-0290).

The sidecar's codec takes only this OS's absolute paths (`app.platform.engine_protocol.is_absolute`, S0127): `/...` on
macOS and Linux, a drive path on Windows. A test written with `/L/main.bean` writes `host("/L/main.bean")`: the same
text on POSIX, `C:\\L\\main.bean` on Windows. Paths only, never files: no test reads or writes them.
"""

from __future__ import annotations

import sys
from typing import Any, cast


def host(posix: str) -> str:
    """`posix` (an absolute POSIX path) as an absolute path of this OS: unchanged off Windows, on drive `C:` there."""
    if not posix.startswith("/"):
        msg = f"host() takes an absolute POSIX path, not {posix!r}"
        raise ValueError(msg)
    if sys.platform != "win32":
        return posix
    return "C:" + posix.replace("/", "\\")


def hosted(value: Any) -> Any:
    """`value` (decoded JSON: the contract's examples) with every string that is an absolute POSIX path respelled by
    `host`, at any depth. The contract states both spellings (`contracts/engine-protocol.schema.json`), so an example
    stays valid; unchanged off Windows."""
    if isinstance(value, str):
        return host(value) if value.startswith("/") else value
    if isinstance(value, list):
        return [hosted(v) for v in cast("list[Any]", value)]
    if isinstance(value, dict):
        return {k: hosted(v) for k, v in cast("dict[str, Any]", value).items()}
    return value
