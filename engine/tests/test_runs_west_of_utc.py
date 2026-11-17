"""The engine's suite runs in a time zone west of UTC (INV-07; ADR-0023 Consequences: tests run with `TZ` west of
UTC), so a UTC "today" taken for the local one fails a test instead of passing by coincidence. `tests/conftest.py`
sets the zone before any test runs.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime

import pytest

pytestmark = pytest.mark.inv("INV-07")


def test_the_suite_runs_west_of_utc() -> None:
    # Must not pass at UTC or east of it: 04:30 UTC on 1 April is still 31 March only west of UTC.
    assert datetime(2026, 4, 1, 4, 30, tzinfo=UTC).astimezone().date() == date(2026, 3, 31)


def test_the_zone_is_los_angeles_with_its_summer_time() -> None:
    """@regression-guard (it holds on the base on macOS and Linux; S0127 keeps it on Windows, whose C runtime reads
    `TZ` only in its own `PST8PDT` form): 07:30 UTC on 1 July is 00:30 in Los Angeles (summer time, UTC-7), still
    30 June at a fixed UTC-8."""
    assert datetime(2026, 7, 1, 7, 30, tzinfo=UTC).astimezone().date() == date(2026, 7, 1)
    assert datetime(2026, 1, 1, 7, 30, tzinfo=UTC).astimezone().date() == date(2025, 12, 31)


def test_a_process_a_test_starts_runs_in_the_same_zone() -> None:
    """@regression-guard (as above): the engine a test starts inherits the zone (INV-07)."""
    code = (
        "from datetime import UTC, datetime\n"
        "print(datetime(2026, 4, 1, 4, 30, tzinfo=UTC).astimezone().date(),"
        " datetime(2026, 7, 1, 7, 30, tzinfo=UTC).astimezone().date())"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603
    assert out.stdout.split() == ["2026-03-31", "2026-07-01"]
