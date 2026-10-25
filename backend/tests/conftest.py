"""Shared pytest hooks for the backend tests."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A `fixme` test (S0029) runs as a strict xfail: its body must fail until the capability lands.

    When it passes, strict xfail turns the run red, and the landing slice removes the marker and closes the backlog
    row. `scripts/check_invariants.py` checks that the marker names an open backlog row with the same milestone.
    """
    for item in items:
        marker = item.get_closest_marker("fixme")
        if marker is not None:
            meta = marker.kwargs
            reason = f"fixme {meta.get('id')} (due {meta.get('due')}): {meta.get('reason')}"
            item.add_marker(pytest.mark.xfail(strict=True, reason=reason))
