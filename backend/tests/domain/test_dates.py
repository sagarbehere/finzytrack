"""The civil date of an instant: the one home of "today" on the main sidecar (INV-07; ADR-0023; EP-23).

From the spec: a date is the civil date in the user's time zone, never the UTC date. The cases sit on a month
boundary where the two differ, so a UTC "today" cannot pass.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.domain.dates import civil_date
from app.platform.clock import FrozenClock
from app.platform.engine_jobs import opening, rebuild
from app.platform.engine_jobs.opening import OpenTarget
from app.platform.engine_jobs.rebuild import RebuildTarget

pytestmark = pytest.mark.inv("INV-07")

# 04:30 UTC on 1 April 2026: still 31 March in Los Angeles (UTC-7), already 1 April in Kolkata (UTC+5:30).
INSTANT = datetime(2026, 4, 1, 4, 30, tzinfo=UTC)
# 19:00 UTC on 31 March 2026: already 1 April in Kolkata, after midnight there.
EVENING = datetime(2026, 3, 31, 19, 0, tzinfo=UTC)


def test_west_of_utc_the_civil_date_is_the_day_before_utcs() -> None:
    assert civil_date(INSTANT, ZoneInfo("America/Los_Angeles")) == date(2026, 3, 31)


def test_east_of_utc_after_local_midnight_the_civil_date_is_the_next_day() -> None:
    assert civil_date(EVENING, ZoneInfo("Asia/Kolkata")) == date(2026, 4, 1)


def test_the_zone_of_the_instant_does_not_matter_only_the_zone_asked_for() -> None:
    same_instant = INSTANT.astimezone(timezone(timedelta(hours=9)))
    assert civil_date(same_instant, ZoneInfo("America/Los_Angeles")) == date(2026, 3, 31)


def test_without_a_zone_it_is_the_zone_the_app_runs_in() -> None:
    # The suite runs in Los Angeles (tests/conftest.py): the machine's zone, on the desktop the user's.
    assert civil_date(INSTANT) == date(2026, 3, 31)


def test_a_naive_instant_is_refused() -> None:
    # Must not pass: a wall time without an offset is not an instant (it would be read in the machine's zone).
    with pytest.raises(ValueError, match="offset"):
        civil_date(datetime(2026, 4, 1, 4, 30))  # noqa: DTZ001 (the naive value is the case under test)


# ADR-0023 Consequences: one run east of UTC (S0092 verifier V5). The whole suite runs in Los Angeles; here every
# sidecar "today" is also taken in Kolkata, after local midnight, where the civil date is already UTC's next day.
@pytest.fixture
def kolkata(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("kolkata")
def test_east_of_utc_every_sidecar_today_is_the_local_date() -> None:
    # 19:00 UTC on 31 March is 00:30 on 1 April in Kolkata: a UTC "today" would send 31 March.
    clock = FrozenClock(EVENING)
    assert civil_date(EVENING) == date(2026, 4, 1)
    open_target = OpenTarget(workspace_key="k", root="/L/main.bean", lineage="lin", app_local_data=Path("/app"))
    assert opening.params_of(open_target, clock)["today"] == "2026-04-01"
    rebuild_target = RebuildTarget("k", "/L/main.bean", "{}", lineage="lin", app_local_data=Path("/app"))
    assert rebuild.params_of(rebuild_target, "{}", clock)["today"] == "2026-04-01"
