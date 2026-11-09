"""The Clock port (ARCHITECTURE.md §2.3; INV-07, EP-10): time is injected, never read from the system.

`FrozenClock` is the port's test double; the acceptance harness wires it through the sidecar's `--frozen-clock`
argument (S0030). The system clock is the adapter `app.platform.adapters.clock.SystemClock`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware."""
        ...


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """A clock that always returns one instant. A wall time with no UTC offset is refused: it is not an instant.
    Frozen: the composition root keeps it for the process's life (INV-10, S0093)."""

    at: datetime

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            msg = f"a frozen clock needs an instant with a UTC offset, not {self.at.isoformat()}"
            raise ValueError(msg)

    def now(self) -> datetime:
        return self.at
