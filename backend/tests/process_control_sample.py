"""A marked test that starts nothing, run only by `tests/test_process_control.py` (its name is not collected)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_process_control


def test_sample() -> None:
    assert True
