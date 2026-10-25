"""The Clock port and its test double (ARCHITECTURE.md §2.3: `Clock`, adapter: system clock, double: `FrozenClock`).

INV-07 and EP-10: time is injected; the acceptance harness freezes it on the backend side (review:27 §1(d)).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.platform.adapters.clock import SystemClock
from app.platform.clock import Clock, FrozenClock

LA_EVENING = datetime(2026, 3, 31, 21, 30, tzinfo=timezone(timedelta(hours=-7)))


def test_a_frozen_clock_always_returns_its_instant() -> None:
    clock: Clock = FrozenClock(LA_EVENING)
    assert clock.now() == LA_EVENING
    assert clock.now() == clock.now()
    assert clock.now().astimezone(UTC) == datetime(2026, 4, 1, 4, 30, tzinfo=UTC)


def test_a_frozen_clock_refuses_a_naive_instant() -> None:
    # Must not pass: a wall time with no offset is not an instant (INV-07: no implicit zone).
    with pytest.raises(ValueError, match="offset"):
        FrozenClock(datetime(2026, 3, 31, 21, 30))  # noqa: DTZ001 (the naive value is the case under test)


def test_the_system_clock_returns_an_aware_utc_instant() -> None:
    clock: Clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now.year >= 2026
