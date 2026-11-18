"""The civil date of an instant: the one home of "today" on the main sidecar (INV-07; ADR-0023; EP-23).

From the spec: a date is the civil date in the user's time zone, never the UTC date. The cases sit on a month
boundary where the two differ, so a UTC "today" cannot pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
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
BACKEND = Path(__file__).resolve().parents[2]

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
def test_east_of_utc_every_sidecar_today_is_the_local_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """In this process where the OS can move its zone (macOS, Linux), and on every OS in a child whose zone is set
    before it starts (S0131): Windows' C runtime reads `TZ` only then (`TZSET`), so there the child is the only form.
    `IST-5:30` is UTC+5:30 in both the POSIX and the Windows C runtime's spelling; the child first checks its zone is
    that, so a `TZ` it ignored fails instead of passing west of UTC."""
    if sys.platform != "win32":
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        time.tzset()
        try:
            east_of_utc_checks()
        finally:
            monkeypatch.undo()
            time.tzset()
    code = (
        "import time; assert time.timezone == -19800, time.timezone; "
        "from tests.domain.test_dates import east_of_utc_checks; east_of_utc_checks()"
    )
    env = {**os.environ, "TZ": "IST-5:30"}
    done = subprocess.run(  # noqa: S603 (fixed argv: this interpreter and a constant)
        [sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr


def east_of_utc_checks() -> None:
    """Every sidecar "today" in a zone east of UTC, where 19:00 UTC on 31 March is already 1 April."""
    # 19:00 UTC on 31 March is 00:30 on 1 April in Kolkata: a UTC "today" would send 31 March.
    clock = FrozenClock(EVENING)
    assert civil_date(EVENING) == date(2026, 4, 1)
    open_target = OpenTarget(workspace_key="k", root="/L/main.bean", lineage="lin", app_local_data=Path("/app"))
    assert opening.params_of(open_target, clock)["today"] == "2026-04-01"
    rebuild_target = RebuildTarget("k", "/L/main.bean", "{}", lineage="lin", app_local_data=Path("/app"))
    assert rebuild.params_of(rebuild_target, "{}", clock)["today"] == "2026-04-01"
