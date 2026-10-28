"""`WorkspacePath`: a path inside a workspace folder, as the folder's own files may name it (ARCHITECTURE.md §2.6;
ADR-0017 "`ledger_root` is relative and non-escaping"; INV-27: the folder holds no absolute paths). Pure.

The confusion it prevents: a path read from a workspace file (one that travels between machines, and may come from
someone else) that names a place outside the folder once it is joined to it. Validation is lexical and holds on every
system a folder may travel to, so a path accepted on macOS cannot escape on Windows:
- relative, `/`-separated, every component non-empty and not `.` or `..`, nothing to normalize away;
- no `\\` (a separator on Windows) and no `:` (a drive, `C:x`, or an alternate data stream on Windows);
- no component ending in `.` or a space, which Windows removes (`.. ` would become `..`);
- no NUL.
A symlink inside the folder may still point outside it; that is the open's question, not this value's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override


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
    if "\x00" in text:
        return "NUL"
    if "\\" in text or ":" in text:
        return "Windows separator or drive"
    if text.startswith("/"):
        return "absolute"
    for part in text.split("/"):
        if part in ("", ".", ".."):
            return "empty, `.` or `..` component"
        if part.endswith((".", " ")):
            return "component ending in a dot or a space"
    return None
