"""Shared pytest set-up for the engine tests."""

from __future__ import annotations

import functools
import os
import sys
import time
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ledger_engine.fs import pathmodel
from tests.windows_pending import POSIX_PATH_MODULES

# INV-07 (ADR-0023 Consequences): the suite runs in a time zone west of UTC, set before any test and inherited by
# every process a test starts (the engine itself among them), so a UTC "today" taken for the local one fails instead
# of passing by coincidence. `tests/test_runs_west_of_utc.py` fails if this is lost.
WEST_OF_UTC = "America/Los_Angeles"
WINDOWS_TZ = "PST8PDT"
"""Los Angeles in the one form Windows' C runtime reads (`_tzset`: a name, the hours west of UTC, a summer-time
name; the US summer-time rules): it reads `TZ` only as a process starts, and Python has no `time.tzset` there, so the
`TZ` variable must be set before the test run starts (the CI job's `engine-windows` sets it; S0127)."""
if sys.platform == "win32":
    os.environ["TZ"] = WINDOWS_TZ  # every process a test starts inherits it
    _summer, _winter = (datetime(2026, m, 1, 12, tzinfo=UTC).astimezone().utcoffset() for m in (7, 1))
    if (_summer, _winter) != (timedelta(hours=-7), timedelta(hours=-8)):
        raise pytest.UsageError(f"INV-07: set TZ={WINDOWS_TZ} before the test run starts (Windows reads it only then)")  # noqa: TRY003
else:
    os.environ["TZ"] = WEST_OF_UTC
    time.tzset()

# The overlay's comparison with Beancount's loader lives in a helper module both corpus tests share (S0094); pytest
# rewrites its asserts too, so a failure shows what differs.
pytest.register_assert_rewrite("tests.ledger.parity")


@pytest.fixture(autouse=True)
def _forget_ledger_plugins(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A test's ledger plugins leave the module cache after it, as a fresh engine starts without them (engine-jobs
    §5.1; S0099): a module imported during the test from a file under the run's temporary folders is dropped, so the
    next test's load imports its own plugins again (`ledger_engine.ledger.imports`: one load with plugins per engine).
    Modules still loaded from earlier tests (Beancount's steps, the standard library) are like those a fresh engine
    imported at its start."""
    before = set(sys.modules)
    yield
    base = str(tmp_path_factory.getbasetemp().resolve())
    for name in set(sys.modules) - before:
        file = getattr(sys.modules[name], "__file__", None)
        if isinstance(file, str) and os.path.realpath(file).startswith(base + os.sep):
            del sys.modules[name]


POSIX_FAKES = frozenset(
    {"tests.fakes.memfs", "tests.ledger.worlds", "tests.ledger.memledger", "tests.mirror.fake_store"}
)
"""The in-memory file system and the worlds built on it: a POSIX file system (its paths are `/...`)."""


@functools.cache
def _uses_the_fake(test_file: Path) -> bool:
    """The test module imports the fake or a world built on it (by its source: it names the module), or is a pure
    test written in POSIX paths (`windows_pending.POSIX_PATH_MODULES`)."""
    if test_file.name in POSIX_PATH_MODULES:
        return True
    source = test_file.read_text(encoding="utf-8")
    return any(name in source for name in POSIX_FAKES)


@pytest.fixture(autouse=True)
def _posix_model_for_the_fake(request: pytest.FixtureRequest) -> Iterator[None]:
    """On Windows a test over the in-memory fake (`tests/fakes/memfs.py`, a POSIX file system) runs with the POSIX path
    model, as on macOS and Linux (S0127): its paths are `/...`, which Windows' model refuses. The Windows model's own
    tests simulate it on every OS (`tests/fs/test_pathmodel.py`, `tests/ledger/test_journal_windows.py`); every other
    test on Windows runs with Windows' model."""
    if sys.platform == "win32" and _uses_the_fake(request.path):
        with pathmodel.simulating(pathmodel.POSIX):
            yield
    else:
        yield


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    """On Windows a module over the fake is also imported with the POSIX model: some build their worlds at import."""
    if sys.platform != "win32":
        return (yield)
    if isinstance(collector, pytest.Module) and _uses_the_fake(collector.path):
        with pathmodel.simulating(pathmodel.POSIX):
            return (yield)
    return (yield)


@pytest.fixture
def windows_port() -> Iterator[None]:
    """On Windows, the Windows adapter selected for the test (`this_os.windows_port_under_test`, S0128): the product
    refuses file operations there until BL-0087 turns writes on, and a real-disk test runs the product's own code
    paths on the adapter. A no-op on macOS and Linux. A module opts in with
    `pytestmark = pytest.mark.usefixtures("windows_port")`."""
    if sys.platform != "win32":
        yield
        return
    from ledger_engine.fs import this_os

    with this_os.windows_port_under_test():
        yield
