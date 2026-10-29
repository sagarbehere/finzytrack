"""The sidecar's wait for the engine lock (`app.platform.adapters.engine_lock`; S0053; `design/specs/engine-jobs.md`
§5.1, §5.5; ADR-0064 amendment of 2026-10-28).

Written from the spec: "At start, before its first engine, the sidecar takes the lock itself, waiting, and releases it
at once (each engine must hold it on its own, so that an engine of a crashed session keeps holding it)". One lock
file, `app_local_data/locks/engine.lock`, never deleted. A real child process holds the lock as an engine of an
earlier session would. Waits are on a condition with a deadline (EP-28).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.platform.adapters.engine_lock import engine_lock_path, wait_engine_lock

pytestmark = pytest.mark.skipif(os.name != "posix", reason="flock is POSIX; Windows is BL-0087 (M1b)")

HOLDER = Path(__file__).with_name("lock_holder.py")
DEADLINE = 15.0


def free(path: Path) -> bool:
    """Whether another process could take the lock now (a new open file description, never blocking)."""
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


@pytest.fixture
def holder(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[str], Path]]:
    path = engine_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    proc = subprocess.Popen(  # noqa: S603 (this interpreter running a fixed test helper)
        [sys.executable, "-I", str(HOLDER), str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    assert proc.stdout.readline() == "held\n"
    yield proc, path
    if proc.poll() is None:
        proc.kill()
    proc.wait(DEADLINE)


def test_the_lock_is_one_file_in_the_app_local_data_locks_folder(tmp_path: Path) -> None:
    assert engine_lock_path(tmp_path) == tmp_path / "locks" / "engine.lock"


def test_with_nobody_holding_it_the_wait_returns_at_once_and_creates_the_file(tmp_path: Path) -> None:
    path = engine_lock_path(tmp_path)
    wait_engine_lock(path)
    assert path.is_file()
    assert free(path), "the sidecar must release the lock at once: each engine holds it on its own"


def test_the_wait_lasts_while_an_earlier_sessions_process_holds_it(
    holder: tuple[subprocess.Popen[str], Path],
) -> None:
    proc, path = holder
    done = threading.Event()
    waiter = threading.Thread(target=lambda: (wait_engine_lock(path), done.set()), daemon=True)
    waiter.start()
    assert not done.wait(0.5), "the sidecar went on while an engine of an earlier session held the engine lock"
    assert proc.stdin is not None
    proc.stdin.close()  # the earlier engine ends
    assert done.wait(DEADLINE), "the wait did not end once the holder had ended"
    assert free(path)


def test_the_lock_file_is_never_deleted(tmp_path: Path) -> None:
    path = engine_lock_path(tmp_path)
    for _ in range(2):
        wait_engine_lock(path)
        assert path.is_file()
