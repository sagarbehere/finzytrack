"""Shared pytest hooks for the backend tests."""

from __future__ import annotations

import pytest

from tests.integrity.planned import PlannedMissing


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
