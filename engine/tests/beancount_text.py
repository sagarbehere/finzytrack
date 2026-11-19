"""Paths written into a ledger's strings by tests (S0133, BL-0282).

Beancount's lexer reads a backslash in a string as an escape (`\\U` is `U`, `\\n` a newline, `\\\\` one backslash), on
every OS. So a test that writes a Windows path into an `include`, an `option`, a `document` line or a plugin's config
writes it with each backslash doubled, and Beancount reads back the path itself. On macOS and Linux a path holds no
backslash, so the text is the path, as before.
"""

from __future__ import annotations


def escaped(p: object) -> str:
    """`str(p)` as the text of a Beancount string that Beancount reads back as `str(p)`."""
    return str(p).replace("\\", "\\\\")
