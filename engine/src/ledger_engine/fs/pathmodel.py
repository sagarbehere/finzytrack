"""The engine's path model, one per operating system (`design/specs/engine-jobs.md` §6.3; S0127, BL-0279). Pure.

Every absolute path the engine splits, joins, records or compares goes through the model of the OS it runs on
(`HOST`, selected once here by `sys.platform`; `host()` at each use):

- **POSIX** (macOS, Linux): exactly the rules the engine had before S0127, byte for byte
  (`tests/test_posix_path_pins.py` pins them on the slice's base commit): a path is `/` and names; a name is
  anything but empty, `.`, `..`, or one that holds `/` or NUL; the components of a path have no root element.
- **Windows**: a path is a root and names. The root is a drive (`C:`, upper case: Windows' own spelling), a UNC share
  (`\\\\server\\share`) or a volume GUID (`\\\\?\\Volume{...}`), and is the first element of the components.
  `\\` and `/` both separate names (Windows takes either), and `\\` is the only separator a joined path has. The
  long-path prefix `\\\\?\\` is a spelling, not another place: a path given with it splits as without it, and
  `extended` adds it back for the system's calls (BL-0280).
  Device paths (`\\\\.\\`, `\\\\?\\GLOBALROOT`, `\\??\\`) are refused.
  **A name Windows cannot hold safely is refused** with a named reason (S0126's probe: without the prefix `aux.txt`
  opens the device and `trail.` is created as `trail`): reserved device names with or without an extension
  (`reserved_name`: `CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9`, `LPT0`-`LPT9`, their superscript-digit forms,
  `CONIN$`, `CONOUT$`, any case, spaces before the extension ignored as Windows ignores them), a name ending in a dot
  or a space (`trailing_dot_or_space`), and a character no Windows name holds (`invalid_character`: `<>:"|?*` and
  the control characters; `:` would name a stream). The check runs wherever a Windows path or name is split, reads
  included, since a read of `aux.txt` would read the device; on macOS and Linux it never runs (S0127 decision: those
  systems keep such names, and a ledger there saves exactly as before).

A plan's relative paths (a create's or a copy's files) use `/` on every OS; `child` turns them into the OS's form.

`simulating(model)` sets another model for the current context. Only tests use it (the Windows model's pure tests on
macOS; `tests/architecture/test_platform_selection.py` refuses it in the product).
"""

from __future__ import annotations

import contextvars
import ntpath
import posixpath
import re
import sys
import unicodedata
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, override

__all__ = [
    "HOST",
    "POSIX",
    "WINDOWS",
    "FoldRule",
    "MalformedPathError",
    "PathModel",
    "fold",
    "for_platform",
    "host",
    "ordinal_upper",
    "simulating",
]


@dataclass(frozen=True, slots=True)
class FoldRule:
    """How a directory's volume compares the names in it (§6.3): `nfd` (macOS: always), `casefold` (a
    case-insensitive volume or directory on macOS or Linux), `upper` (Windows: ordinal upper case, unless the folder
    is case-sensitive)."""

    nfd: bool
    casefold: bool
    upper: bool = False


class MalformedPathError(ValueError):
    """A caller's error: a path or a name the port does not take (nothing was done)."""

    def __init__(self, what: str) -> None:
        super().__init__(f"malformed path: {what}")


def ordinal_upper(name: str) -> str:
    """Windows' ordinal upper case (NTFS's `$UpCase` table): each 16-bit character to its one-character upper case
    (its simple mapping), else unchanged; no normalization. Python gives full mappings only, so a character whose
    upper case is several characters takes its one-character title case when it has one (`ᾀ` to `ᾈ`) and stays itself
    otherwise (`ß`). Characters beyond the 16-bit range stay as they are, as NTFS's table covers none."""
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


def fold(component: str, rule: FoldRule) -> str:
    """One name folded by its directory's rule (§6.3). Caseless matching is NFD(casefold(NFD(x))) (Unicode §3.13);
    Windows' ordinal upper case first, where the rule has it."""
    out = ordinal_upper(component) if rule.upper else component
    out = unicodedata.normalize("NFD", out) if rule.nfd or rule.casefold else out
    if rule.casefold:
        out = unicodedata.normalize("NFD", out.casefold())
    return out


class PathModel:
    """The rules of one OS's absolute paths (the module's docstring). Subclasses: `PosixPaths`, `WindowsPaths`."""

    name: str
    sep: str
    rules: tuple[FoldRule, ...]
    """Every rule a `path_key` component may be folded by on this OS (a journal record's key must fold so)."""
    wide: FoldRule
    """The widest folding: two names a volume of this OS may treat as one are compared as one (the journal's check
    of our own names; a record does not say its volumes' rules)."""

    def check_name(self, name: str) -> str:
        """A single path component this OS can hold, or `MalformedPathError`."""
        raise NotImplementedError

    def split(self, path: str) -> tuple[str, ...]:
        """The components of an absolute path (Windows: the root first); `MalformedPathError` for a relative path, a
        `.` or `..` component (they would change meaning with links) or a name `check_name` refuses."""
        raise NotImplementedError

    def join(self, parts: tuple[str, ...] | list[str]) -> str:
        """The absolute path of components `split` gives."""
        raise NotImplementedError

    def is_root(self, parts: tuple[str, ...] | list[str]) -> bool:
        """Whether `split`'s components name a root (no name below it)."""
        raise NotImplementedError

    def is_absolute(self, text: str) -> bool:
        """Whether `text` is spelled as an absolute path of this OS (by its text only; `split` checks the names)."""
        raise NotImplementedError

    def is_root_path(self, text: str) -> bool:
        """Whether `text` is the spelling of a root that a plan's target or source may not be (POSIX: `/` exactly, as
        before S0127; Windows: a path whose components are its root alone)."""
        raise NotImplementedError

    def normpath(self, path: str) -> str:
        """The path's normal spelling, by its text: a path that equals it is in the form the engine records."""
        raise NotImplementedError

    def child(self, folder: str, rel: str) -> str:
        """The path of `rel` (a name, or a plan's relative path with `/`) in the folder `folder`."""
        raise NotImplementedError

    def parent_and_name(self, path: str) -> tuple[str, str]:
        """The folder an absolute path is in, and its last name."""
        raise NotImplementedError

    def trim(self, path: str) -> str:
        """`path` without trailing separators, a root kept whole."""
        raise NotImplementedError

    def is_under(self, path: str, folder: str, *, strict: bool = False) -> bool:
        """`path` is `folder` (unless `strict`) or inside it, by text."""
        if path == folder:
            return not strict
        return path.startswith(self.trim(folder).rstrip(self.sep) + self.sep)

    def same_path(self, now: str, recorded: str) -> bool:
        """The path guard's comparison of a folder's path now with the recorded one, as this OS gives paths back.
        POSIX: the adapters' own rules (`DarwinFS.same_path`, `LinuxFS.same_path`)."""
        raise NotImplementedError

    def extended(self, path: str) -> str:
        """The path as the system's calls take it: on Windows with the long-path prefix (`WindowsPaths.extended`, for
        the Windows adapter, S0128); the POSIX adapters take paths as they are and never ask."""
        raise NotImplementedError

    # the lexical operations the copy layout needs, as this OS's `os.path` does them
    def dirname(self, path: str) -> str:
        raise NotImplementedError

    def joinpath(self, a: str, b: str) -> str:
        raise NotImplementedError

    def relpath(self, path: str, start: str) -> str:
        raise NotImplementedError

    def commonpath(self, paths: list[str]) -> str:
        raise NotImplementedError


class PosixPaths(PathModel):
    """macOS and Linux: the rules the engine had before S0127, unchanged."""

    name = "posix"
    sep = "/"
    rules = tuple(FoldRule(nfd=n, casefold=c) for n in (False, True) for c in (False, True))
    wide = FoldRule(nfd=True, casefold=True)

    @override
    def check_name(self, name: str) -> str:
        if not name or name in (".", "..") or "/" in name or "\0" in name:
            raise MalformedPathError("not_one_component")
        return name

    @override
    def split(self, path: str) -> tuple[str, ...]:
        if not path.startswith("/"):
            raise MalformedPathError("not_absolute")
        parts = tuple(p for p in path.split("/") if p)
        for p in parts:
            self.check_name(p)
        return parts

    @override
    def join(self, parts: tuple[str, ...] | list[str]) -> str:
        return "/" + "/".join(parts)

    @override
    def is_root(self, parts: tuple[str, ...] | list[str]) -> bool:
        return not parts

    @override
    def is_absolute(self, text: str) -> bool:
        return text.startswith("/")

    @override
    def is_root_path(self, text: str) -> bool:
        return text == "/"

    @override
    def normpath(self, path: str) -> str:
        return posixpath.normpath(path)

    @override
    def child(self, folder: str, rel: str) -> str:
        return "/" + rel if folder == "/" else f"{folder}/{rel}"

    @override
    def parent_and_name(self, path: str) -> tuple[str, str]:
        head, _, name = path.rpartition("/")
        return head or "/", name

    @override
    def trim(self, path: str) -> str:
        return path.rstrip("/") or "/"

    @override
    def is_under(self, path: str, folder: str, *, strict: bool = False) -> bool:
        if path == folder:
            return not strict
        return path.startswith(folder.rstrip("/") + "/")

    @override
    def dirname(self, path: str) -> str:
        return posixpath.dirname(path)

    @override
    def joinpath(self, a: str, b: str) -> str:
        return posixpath.join(a, b)

    @override
    def relpath(self, path: str, start: str) -> str:
        return posixpath.relpath(path, start)

    @override
    def commonpath(self, paths: list[str]) -> str:
        return posixpath.commonpath(paths)


_RESERVED: Final = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"{d}{n}" for d in ("COM", "LPT") for n in (*"0123456789", "\u00b9", "\u00b2", "\u00b3")),
    }
)
"""Windows' reserved device names (Microsoft's "Naming Files, Paths, and Namespaces", and CPython's
`ntpath.isreserved`); `COM0` and `LPT0` included, which some Windows versions reserve: refusing a name Windows would
have taken is fail-closed."""
_INVALID: Final = re.compile(r'[<>:"|?*\x01-\x1f]')
_DRIVE: Final = re.compile(r"[A-Za-z]:")
_VOLUME: Final = re.compile(r"Volume\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")
_LONG: Final = "\\\\?\\"


class WindowsPaths(PathModel):
    """Windows (the module's docstring)."""

    name = "windows"
    sep = "\\"
    rules = (FoldRule(nfd=False, casefold=False, upper=True), FoldRule(nfd=False, casefold=False))
    wide = FoldRule(nfd=True, casefold=True, upper=True)

    @override
    def check_name(self, name: str) -> str:
        if not name or name in (".", "..") or "\\" in name or "/" in name or "\0" in name:
            raise MalformedPathError("not_one_component")
        if _INVALID.search(name):
            raise MalformedPathError("invalid_character")
        if name.partition(".")[0].rstrip(" ").upper() in _RESERVED:
            raise MalformedPathError("reserved_name")  # first: the device is the graver of the two (`nul `)
        if name[-1] in ". ":
            raise MalformedPathError("trailing_dot_or_space")
        return name

    def _root(self, path: str) -> tuple[str, str]:
        """The root (as the first component) and the rest of a path, its prefix taken off."""
        if path.startswith(_LONG):
            body = path[len(_LONG) :]
            if "/" in body:
                raise MalformedPathError("invalid_character")  # under the prefix `/` is no separator
            if body[:4].upper() == "UNC\\":
                return self._unc(body[4:])
            if _DRIVE.fullmatch(body[:2]) and body[2:3] in ("", "\\"):
                return body[0].upper() + ":", body[3:]
            head, _, rest = body.partition("\\")
            if _VOLUME.fullmatch(head):
                return _LONG + head, rest
            raise MalformedPathError("device_path")
        p = path.replace("/", "\\")
        if p.startswith(("\\\\.\\", "\\\\?\\", "\\??\\")):
            raise MalformedPathError("device_path")  # `//?/` and `\\.\`: the device namespace
        if p.startswith("\\\\"):
            return self._unc(p[2:])
        if _DRIVE.fullmatch(p[:2]) and p[2:3] == "\\":
            return p[0].upper() + ":", p[3:]
        raise MalformedPathError("not_absolute")

    def _unc(self, body: str) -> tuple[str, str]:
        server, _, rest = body.partition("\\")
        share, _, rest = rest.partition("\\")
        if not server or not share:
            raise MalformedPathError("unc_without_share")
        self.check_name(server)
        self.check_name(share)
        return f"\\\\{server}\\{share}", rest

    @override
    def split(self, path: str) -> tuple[str, ...]:
        if "\0" in path:
            raise MalformedPathError("not_one_component")
        root, rest = self._root(path)
        sep = "\\" if root.startswith(_LONG) or path.startswith(_LONG) else None
        names = tuple(n for n in (rest.split(sep) if sep else rest.replace("/", "\\").split("\\")) if n)
        for n in names:
            self.check_name(n)
        return (root, *names)

    @override
    def join(self, parts: tuple[str, ...] | list[str]) -> str:
        return parts[0] + "\\" + "\\".join(parts[1:])

    @override
    def is_root(self, parts: tuple[str, ...] | list[str]) -> bool:
        return len(parts) <= 1

    @override
    def is_absolute(self, text: str) -> bool:
        return ntpath.isabs(text) and not text.replace("/", "\\").startswith("\\\\.\\")

    @override
    def is_root_path(self, text: str) -> bool:
        try:
            return self.is_root(self.split(text))
        except MalformedPathError:
            return False

    @override
    def normpath(self, path: str) -> str:
        if not self.is_absolute(path):
            return ntpath.normpath(path)
        try:
            root, rest = self._root(path)
        except MalformedPathError:
            return ntpath.normpath(path)
        # `.` and `..` resolved below the root, never above it; separators after the root collapsed first, or ntpath
        # reads `\\\\x` as the start of a UNC path and keeps it (S0127 verifier F3)
        tail = ntpath.normpath("\\" + rest.lstrip("\\/"))
        return root + tail

    @override
    def child(self, folder: str, rel: str) -> str:
        return folder.rstrip("\\") + "\\" + rel.replace("/", "\\")

    @override
    def parent_and_name(self, path: str) -> tuple[str, str]:
        parts = self.split(path)
        if self.is_root(parts):
            raise MalformedPathError("root_has_no_parent")  # S0127 verifier F2: was an `IndexError`
        return self.join(parts[:-1]), parts[-1]

    @override
    def trim(self, path: str) -> str:
        try:
            return self.join(self.split(path))
        except MalformedPathError:
            return path.rstrip("\\")

    @override
    def same_path(self, now: str, recorded: str) -> bool:
        """Windows gives a folder's path back (`GetFinalPathNameByHandleW`) with the prefix, the drive letter in upper
        case and each name as stored: compared after taking the prefix off, the root without case (a drive letter, a
        server and a share have none), each name exactly (a folder renamed in case only has moved: fails closed, as
        on macOS)."""
        try:
            a, b = self.split(now), self.split(recorded)
        except MalformedPathError:
            return False
        return ordinal_upper(a[0]) == ordinal_upper(b[0]) and a[1:] == b[1:]

    @override
    def extended(self, path: str) -> str:
        """The path with the long-path prefix, as the system's calls take it (the Windows adapter, S0128):
        `\\\\?\\C:\\...`, `\\\\?\\UNC\\server\\share\\...`; a volume GUID path has it already."""
        parts = self.split(path)
        root = parts[0]
        if root.startswith(_LONG):
            return self.join(parts)
        if root.startswith("\\\\"):
            return _LONG + "UNC\\" + self.join(parts)[2:]
        return _LONG + self.join(parts)

    @override
    def dirname(self, path: str) -> str:
        return ntpath.dirname(path)

    @override
    def joinpath(self, a: str, b: str) -> str:
        return ntpath.join(a, b)

    @override
    def relpath(self, path: str, start: str) -> str:
        return ntpath.relpath(path, start)

    @override
    def commonpath(self, paths: list[str]) -> str:
        return ntpath.commonpath(paths)


POSIX: Final = PosixPaths()
WINDOWS: Final = WindowsPaths()


def for_platform(platform: str) -> PathModel:
    """The model of the OS `sys.platform` names."""
    return WINDOWS if platform == "win32" else POSIX


HOST: Final = for_platform(sys.platform)
_current: Final = contextvars.ContextVar[PathModel]("path_model", default=HOST)


def host() -> PathModel:
    """The model in force: `HOST`, except inside a test's `simulating`."""
    return _current.get()


@contextmanager
def simulating(model: PathModel) -> Generator[None]:
    """Tests only: `model` in force in this context until the block ends."""
    token = _current.set(model)
    try:
        yield
    finally:
        _current.reset(token)
