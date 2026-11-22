"""Windows' absolute paths as the engine's path model splits them (S0131, R2-1; the owner, 2026-11-18: "Full rule
now"). Pure, and the sidecar's twin of `ledger_engine.fs.pathmodel.WindowsPaths`' `split` and `same_path`, which the
sidecar cannot import (ADR-0054); `tests/platform/test_windows_paths.py` holds the two to the same answers on a shared
list of paths, run through the engine's own model.

A path is a root and names. The root is a drive (`C:`, upper case), a UNC share (`\\\\server\\share`) or a volume GUID
(`\\\\?\\Volume{...}`). `\\` and `/` both separate names, except under the long-path prefix `\\\\?\\`, which is a
spelling of the same place, not another one. Device paths (`\\\\.\\`, `\\\\?\\GLOBALROOT`, `\\??\\`), a UNC path
without a share, `.` and `..`, and a name Windows cannot hold safely (a reserved device name, a trailing dot or space,
`<>:"|?*` or a control character) are malformed: `split` answers None.

`normal` gives a path the engine's normal form (`WindowsPaths.normpath`), which the engine's contract requires of every
path it checks (`contract._normal`; S0146, BL-0087; owner, 2026-11-22: the sidecar normalizes a root before the
engine): the codec applies it to every absolute path a request carries (`engine_protocol.encode_request`).
"""

from __future__ import annotations

import ntpath
import re
from typing import Final

_RESERVED: Final = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        "COM0",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "COM\xb9",
        "COM\xb2",
        "COM\xb3",
        "LPT0",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
        "LPT\xb9",
        "LPT\xb2",
        "LPT\xb3",
    }
)
"""Windows' reserved device names, as the engine's `pathmodel._RESERVED` (COM and LPT with 0-9 and superscripts)."""
_INVALID: Final = re.compile(r'[<>:"|?*\x01-\x1f]')
_DRIVE: Final = re.compile(r"[A-Za-z]:")
_VOLUME: Final = re.compile(r"Volume\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")
_LONG: Final = "\\\\?\\"


class _Malformed(ValueError):
    pass


def ordinal_upper(name: str) -> str:
    """Windows' ordinal upper case (NTFS's `$UpCase`): each 16-bit character to its one-character upper case, else its
    one-character title case, else unchanged; characters beyond 16 bits unchanged (the engine's `ordinal_upper`)."""
    out: list[str] = []
    for c in name:
        if ord(c) > 0xFFFF:
            out.append(c)
            continue
        u = c.upper()
        if len(u) != 1:
            u = c.title()
        out.append(u if len(u) == 1 else c)
    return "".join(out)


def _name(name: str) -> str:
    if not name or name in (".", "..") or "\\" in name or "/" in name or "\0" in name:
        raise _Malformed
    if _INVALID.search(name) or name.partition(".")[0].rstrip(" ").upper() in _RESERVED or name[-1] in ". ":
        raise _Malformed
    return name


def _unc(body: str) -> tuple[str, str]:
    server, _, rest = body.partition("\\")
    share, _, rest = rest.partition("\\")
    if not server or not share:
        raise _Malformed
    return f"\\\\{_name(server)}\\{_name(share)}", rest


def _root(path: str) -> tuple[str, str]:
    if path.startswith(_LONG):
        body = path[len(_LONG) :]
        if "/" in body:
            raise _Malformed
        if body[:4].upper() == "UNC\\":
            return _unc(body[4:])
        if _DRIVE.fullmatch(body[:2]) and body[2:3] in ("", "\\"):
            return body[0].upper() + ":", body[3:]
        head, _, rest = body.partition("\\")
        if _VOLUME.fullmatch(head):
            return _LONG + head, rest
        raise _Malformed
    p = path.replace("/", "\\")
    if p.startswith(("\\\\.\\", "\\\\?\\", "\\??\\")):
        raise _Malformed
    if p.startswith("\\\\"):
        return _unc(p[2:])
    if _DRIVE.fullmatch(p[:2]) and p[2:3] == "\\":
        return p[0].upper() + ":", p[3:]
    raise _Malformed


def split(path: str) -> tuple[str, ...] | None:
    """The root and the names of an absolute Windows path, or None when it is not one (the module's docstring)."""
    if "\0" in path:
        return None
    try:
        root, rest = _root(path)
        sep = "\\" if root.startswith(_LONG) or path.startswith(_LONG) else None
        names = tuple(n for n in (rest.split(sep) if sep else rest.replace("/", "\\").split("\\")) if n)
        return (root, *(_name(n) for n in names))
    except _Malformed:
        return None


def normal(path: str) -> str:
    """`path` in the engine's normal form, when `split` takes it: the drive letter upper case (`c:\\x` to `C:\\x`),
    the long-path prefix taken off (`\\\\?\\C:\\x` to `C:\\x`; `\\\\?\\UNC\\nas\\share\\x` to `\\\\nas\\share\\x`)
    except before a volume GUID, which keeps it; then, as Windows itself reads a path, `/` as `\\`, separators not
    doubled and none at the end (the engine's `WindowsPaths.normpath`). Any other text, `.` and `..` names among it
    (`split` takes none), is returned as it is: the engine refuses it, naming why."""
    if split(path) is None:
        return path
    root, rest = _root(path)
    return root + ntpath.normpath("\\" + rest.lstrip("\\/"))
