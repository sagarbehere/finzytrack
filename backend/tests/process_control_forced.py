"""A test plugin for `tests/test_process_control.py` only, loaded with `-p`: it forces the probe's answer.

`FT_TEST_PROCESS_PROBE_ANSWER` empty is "allowed", else the refusal's text. Nothing else reads that variable, so no
run without this plugin can have its answer forced.
"""

from __future__ import annotations

import os

import pytest

from tests import process_control


def pytest_configure(config: pytest.Config) -> None:
    del config
    forced = os.environ["FT_TEST_PROCESS_PROBE_ANSWER"] or None
    process_control.answer.cache_clear()
    process_control.probe = lambda ops=None: forced
