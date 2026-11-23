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

from ledger_engine.fs import pathmodel, this_os
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

# S0150 (BL-0087, ADR-0070): the test process plays the engine, so on Windows its placeholder mode is pinned as every
# engine process pins it at its start (`this_os.pin_placeholder_mode`); the port refuses in any other mode. A test that
# sets another mode puts this one back (`tests/fs/test_windows_cloud.py`).
if not this_os.pin_placeholder_mode():
    raise pytest.UsageError(f"the engine's placeholder mode could not be pinned: {this_os.pin_refused()}")  # noqa: TRY003

# The overlay's comparison with Beancount's loader lives in a helper module both corpus tests share (S0094); pytest
# rewrites its asserts too, so a failure shows what differs.
pytest.register_assert_rewrite("tests.ledger.parity")


@pytest.fixture(autouse=True)
def _forget_ledger_plugins(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A test's ledger plugins leave the module cache after it, as a fresh engine starts without them (engine-jobs
    §5.1; S0099): a module imported during the test from a file under the run's temporary folders is dropped, so the
    next test's load imports its own plugins again (`ledger_engine.ledger.imports`: one load with plugins per engine).
    Limit: every other module an earlier test imported stays, and not all of them are ones a fresh engine imports
    at its start: Beancount imports some steps of its load only when a load runs them (`beancount.ops.documents`,
    `beancount.ops.pad`). A load records only the modules it imports itself, so a test that compares the fingerprints
    of two loads in one process can depend on the order the suite runs in; such a test runs each load in
    `tests/fresh_engine.fresh_engine()` (S0142, BL-0299)."""
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
    test on Windows runs with Windows' model, and so does every test that asks for `host_path_model`: a real-disk test
    in a module that also names the fake (S0130, the 2026-11-18 run 8, `test_save.py`; the 2026-11-23 run 17, the
    tests of `test_cloud_unavailable.py` that take `test_save`'s `led`)."""
    if sys.platform == "win32" and _uses_the_fake(request.path) and "host_path_model" not in request.fixturenames:
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
def host_path_model() -> None:
    """A real-disk test, or a fixture that makes one (`tests/ledger/test_save.led`), in a module that also names the
    fake asks for this: on Windows it then runs with Windows' path model, never the fake's POSIX one (S0150; until then
    the test switch's fixture did this as well). It does nothing itself: `_posix_model_for_the_fake`
    reads the test's fixture names. A module whose every test is on the real disk opts in whole with
    `pytestmark = pytest.mark.usefixtures("host_path_model")`."""
