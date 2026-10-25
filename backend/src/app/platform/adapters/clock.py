"""The system clock, the `Clock` port's one adapter (ARCHITECTURE.md §2.3). The only place that reads real time."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)  # noqa: TID251 (the one reader of real time: this adapter)
