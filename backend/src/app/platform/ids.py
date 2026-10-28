"""The Ids port (ARCHITECTURE.md §2.3; EP-10): every id is injected, so tests see deterministic ones.

The first use is the API edge's request id (S0046). The real adapter is `app.platform.adapters.ids.Uuid7Ids`, wired
by the composition root that serves the API (S0048); tests use a sequence.
"""

from __future__ import annotations

from typing import Protocol


class Ids(Protocol):
    def new_id(self) -> str:
        """A new id, unique for this process's life; safe to log (letters, digits and hyphens only)."""
        ...
