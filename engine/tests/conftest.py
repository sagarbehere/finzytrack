"""Shared pytest set-up for the engine tests."""

from __future__ import annotations

import os
import time

import pytest

# INV-07 (ADR-0023 Consequences): the suite runs in a time zone west of UTC, set before any test and inherited by
# every process a test starts (the engine itself among them), so a UTC "today" taken for the local one fails instead
# of passing by coincidence. `tests/test_runs_west_of_utc.py` fails if this is lost.
WEST_OF_UTC = "America/Los_Angeles"
os.environ["TZ"] = WEST_OF_UTC
time.tzset()

# The overlay's comparison with Beancount's loader lives in a helper module both corpus tests share (S0094); pytest
# rewrites its asserts too, so a failure shows what differs.
pytest.register_assert_rewrite("tests.ledger.parity")
