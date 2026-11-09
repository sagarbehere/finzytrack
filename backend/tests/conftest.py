"""Shared pytest hooks for the backend tests."""

from __future__ import annotations

import os
import time

import pytest

from tests.integrity.planned import PlannedMissing

# INV-07 (ADR-0023 Consequences): the suite runs in a time zone west of UTC, set before any test and inherited by
# every process a test starts, so a UTC "today" taken for the local one fails instead of passing by coincidence.
# `tests/test_runs_west_of_utc.py` fails if this is lost.
WEST_OF_UTC = "America/Los_Angeles"
os.environ["TZ"] = WEST_OF_UTC
time.tzset()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A `fixme` test (S0029) runs as a strict xfail that may fail only because its planned module is missing.

    Its body must fail until the capability lands, and only with `PlannedMissing` (tests/integrity/planned.py; S0032):
    any other failure (a buggy first implementation, a vacuous assertion error) fails the run, and a body that passes
    turns the run red through strict xfail, so the landing slice removes the marker and closes the backlog row.
    `scripts/check_invariants.py` checks that the marker names an open backlog row with the same milestone.
    """
    for item in items:
        marker = item.get_closest_marker("fixme")
        if marker is not None:
            meta = marker.kwargs
            reason = f"fixme {meta.get('id')} (due {meta.get('due')}): {meta.get('reason')}"
            item.add_marker(pytest.mark.xfail(strict=True, raises=PlannedMissing, reason=reason))
