"""A marked test that starts nothing, run only by `tests/test_process_control.py` (its name is not collected)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_process_control


def test_sample() -> None:
    assert True


def test_another_sample() -> None:
    """A second marked test, so a rule that skips only some of them fails (S0147 verifier F-2)."""
    assert True
