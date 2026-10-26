"""Text whose handling is part of its type (ARCHITECTURE.md §2.6, §6A.3; EP-21, EP-26).

`UntrustedText` is text from outside the app's own code (payees, narrations, metadata values, email bodies, model
output, a ledger engine's stderr). `Secret` is a credential. Both are wrappers, not `str` subclasses, so neither is
used as plain text by accident: the content is reached only through `.text` or `.value`, and `repr` never shows it.
The log redacts both by type (`<untrusted len=N>`, `<secret>`).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class UntrustedText:
    text: str = field(repr=False)

    def __len__(self) -> int:
        return len(self.text)


@dataclass(frozen=True, slots=True)
class Secret:
    value: str = field(repr=False)
