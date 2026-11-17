"""The Windows lists name real modules (S0127): a POSIX-path module that no longer exists would make the conftest's
list claim a module that is gone."""

from __future__ import annotations

from pathlib import Path

from tests.windows_pending import POSIX_PATH_MODULES

TESTS = Path(__file__).resolve().parent


def test_every_posix_path_module_exists() -> None:
    assert all(len(list(TESTS.rglob(m))) == 1 for m in POSIX_PATH_MODULES)
