"""`WorkspacePath`: a path inside a workspace folder, as the folder's own files may name it (ARCHITECTURE.md §2.6;
ADR-0017 "`ledger_root` is relative and non-escaping"; INV-27: the folder holds no absolute paths). Pure.

The confusion it prevents: a path read from a workspace file (one that travels between machines, and may come from
someone else) that names a place outside the folder once it is joined to it. Validation is lexical and holds on every
system a folder may travel to, so a path accepted on macOS cannot escape on Windows:
- relative, `/`-separated, every component non-empty and not `.` or `..`, nothing to normalize away;
- no `\\` (a separator on Windows) and no `:` (a drive, `C:x`, or an alternate data stream on Windows);
- no component ending in `.` or a space, which Windows removes (`.. ` would become `..`);
- no character Windows refuses in a name: control characters (NUL included) and `"*<>?|`;
- no Windows device name (`CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9`, `LPT0`-`LPT9`, `COM¹²³`, `LPT¹²³`,
  `CONIN$`, `CONOUT$`), in any case and with any extension (`nul.txt`, `Com1.tar.gz`): Windows opens the device,
  not a file in the folder. The rule is Python's `ntpath.isreserved` (3.13), plus `COM0` and `LPT0`, which
  Microsoft's naming page also lists.
A symlink inside the folder may still point outside it; that is the open's question, not this value's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, override

# Control characters (NUL included) and the characters Windows refuses in a name.
_WINDOWS_REFUSED_CHARACTERS: Final = frozenset(
    "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
    "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
    '"*<>?|'
)
_WINDOWS_DEVICES: Final = frozenset(
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
        "COM\u00b9",
        "COM\u00b2",
        "COM\u00b3",
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
        "LPT\u00b9",
        "LPT\u00b2",
        "LPT\u00b3",
    }
)


class InvalidWorkspacePath(ValueError):
    """The text is not a path inside a workspace folder; `reason` says which rule it breaks."""

    def __init__(self, text: str, reason: str) -> None:
        super().__init__(f"not a workspace path ({reason}): {text!r}")
        self.text = text
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    """A relative POSIX path that stays inside the folder it is joined to. Built only by `parse`."""

    text: str

    @classmethod
    def parse(cls, text: str) -> WorkspacePath:
        reason = _refusal(text)
        if reason is not None:
            raise InvalidWorkspacePath(text, reason)
        return cls(text)

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.text.split("/"))

    @override
    def __str__(self) -> str:
        return self.text


def _refusal(text: str) -> str | None:
    if not text:
        return "empty"
    if _WINDOWS_REFUSED_CHARACTERS.intersection(text):
        return 'a control character or one of "*<>?|'
    if "\\" in text or ":" in text:
        return "Windows separator or drive"
    if text.startswith("/"):
        return "absolute"
    for part in text.split("/"):
        if part in ("", ".", ".."):
            return "empty, `.` or `..` component"
        if part.endswith((".", " ")):
            return "component ending in a dot or a space"
        if part.partition(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICES:
            return "Windows device name"
    return None
