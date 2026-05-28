"""Inter-process write-lock serialisation tests.

Spec under test: a ``WriteLockManager`` wired with a sidecar lock file
serialises writes between two FinzyTrack-instance processes against the
same user's data dir. Intra-process serialisation (threading.Lock) is
exercised by `tests/isolation/test_concurrent_writes.py`.
"""
import os
import subprocess
import sys
import textwrap
import time

import portalocker
import pytest

from app.write_lock import WriteLockManager


def _hold_lock_subprocess(lock_path: str, hold_secs: float, started_marker: str) -> subprocess.Popen:
    """Spawn a child Python that acquires *lock_path* via WriteLockManager
    and sleeps inside the critical section for *hold_secs* seconds.

    The child writes ``started_marker`` to its stdout once the lock is held
    so the parent can synchronise without a race.
    """
    script = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from app.write_lock import WriteLockManager

        mgr = WriteLockManager(user_id="child", lock_file=Path({lock_path!r}))
        with mgr.acquire("test_hold"):
            sys.stdout.write({started_marker!r} + chr(10))
            sys.stdout.flush()
            time.sleep({hold_secs})
    """)
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_file_lock_blocks_a_second_process(tmp_path):
    """Spec: while one process holds the WriteLockManager file lock, a
    second process must not be able to acquire it. ``portalocker.lock``
    with ``LOCK_NB`` raises immediately if the lock is held elsewhere —
    use that to assert the contention rather than risk a deadlock.
    """
    lock_path = tmp_path / "data" / ".write.lock"

    child = _hold_lock_subprocess(str(lock_path), hold_secs=2.0, started_marker="HOLDING")
    try:
        # Wait for the child to signal it has acquired.
        line = child.stdout.readline().strip()
        assert line == "HOLDING", f"child did not report holding lock: {line!r}"

        # While the child holds the lock, a non-blocking attempt from this
        # process must fail. ``portalocker.LockException`` is the documented
        # raised type when ``LOCK_NB`` cannot be granted.
        with open(lock_path, "a") as fp:
            with pytest.raises(portalocker.LockException):
                portalocker.lock(fp, portalocker.LOCK_EX | portalocker.LOCK_NB)
    finally:
        child.wait(timeout=5.0)
        assert child.returncode == 0, child.stderr.read()


def test_second_process_proceeds_after_first_releases(tmp_path):
    """Once the holder exits, the next acquirer must proceed normally."""
    lock_path = tmp_path / "data" / ".write.lock"

    child = _hold_lock_subprocess(str(lock_path), hold_secs=0.2, started_marker="HOLDING")
    assert child.stdout.readline().strip() == "HOLDING"
    child.wait(timeout=5.0)
    assert child.returncode == 0

    mgr = WriteLockManager(user_id="parent", lock_file=lock_path)
    started = time.monotonic()
    with mgr.acquire("test_after_release"):
        elapsed = time.monotonic() - started
    # Acquiring after the holder has exited must be effectively instant.
    assert elapsed < 1.0, f"acquire after release took {elapsed:.3f}s"


def test_no_lock_file_path_falls_back_to_intra_process(tmp_path):
    """Constructing a manager without a lock_file path must keep working
    (used by unit tests). Two consecutive acquires from the same process
    must both succeed.
    """
    mgr = WriteLockManager(user_id="unit", lock_file=None)
    with mgr.acquire("first"):
        pass
    with mgr.acquire("second"):
        pass
