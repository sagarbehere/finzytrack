"""The engine's suite runs in a time zone west of UTC (INV-07; ADR-0023 Consequences: tests run with `TZ` west of
UTC), so a UTC "today" taken for the local one fails a test instead of passing by coincidence. `tests/conftest.py`
sets the zone before any test runs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

pytestmark = pytest.mark.inv("INV-07")


def test_the_suite_runs_west_of_utc() -> None:
    # Must not pass at UTC or east of it: 04:30 UTC on 1 April is still 31 March only west of UTC.
    assert datetime(2026, 4, 1, 4, 30, tzinfo=UTC).astimezone().date() == date(2026, 3, 31)
