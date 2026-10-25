"""The Clock port (ARCHITECTURE.md §2.3; INV-07, EP-10): time is injected, never read from the system.

`FrozenClock` is the port's test double; the acceptance harness wires it through the sidecar's `--frozen-clock`
argument (S0030). The system clock is the adapter `app.platform.adapters.clock.SystemClock`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware."""
        ...


class FrozenClock:
    """A clock that always returns one instant. A wall time with no UTC offset is refused: it is not an instant."""

    __slots__ = ("_at",)

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            msg = f"a frozen clock needs an instant with a UTC offset, not {at.isoformat()}"
            raise ValueError(msg)
        self._at = at

    def now(self) -> datetime:
        return self._at
