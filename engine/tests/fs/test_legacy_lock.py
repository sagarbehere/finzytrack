"""The legacy app's write lock adapter (`legacy_locks_for_this_os`: macOS and Linux, both `flock`, S0125; Windows,
`LockFileEx` over the range portalocker's `MsvcrtLocker` locks, S0128, the product's since S0150; INV-26,
ADR-0062 decisions 2 and 5, narrowed to Copy by ADR-0068 D4): tried once, without waiting, with the call portalocker
3.2.0 makes, on the file opened read-only and only if it exists. Copy is BL-0026's; the save no longer tries the lock
(S0071), so these are the adapter's own tests, against a holder that takes the lock with portalocker 3.2.0 itself, as
v0.2.2 and v0.3.0 do."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ledger_engine.fs.port import LegacyAbsent, LegacyHeld
from ledger_engine.fs.this_os import legacy_locks_for_this_os

pytestmark = pytest.mark.inv("INV-26")

HOLDER = """
import sys, portalocker
fh = open(sys.argv[1], "a")
portalocker.lock(fh, portalocker.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()
"""
TRY = """
import sys, portalocker
fh = open(sys.argv[1], "rb")
try:
    portalocker.lock(fh, getattr(portalocker, sys.argv[2]) | portalocker.LOCK_NB)
except portalocker.AlreadyLocked:
    print("held")
else:
    print("free")
"""


@contextmanager
def legacy_app_saving(lock: Path) -> Generator[None]:
    """The legacy app saving: another process holds its write lock as v0.2.2/v0.3.0 do (portalocker 3.2.0,
    `portalocker.lock(fp, LOCK_EX)` in its `write_lock.py`; ADR-0062)."""
    proc = subprocess.Popen(  # noqa: S603 (our own interpreter and script)
        [sys.executable, "-c", HOLDER, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        yield
    finally:
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.close()
        proc.wait()


def legacy_state(lock: Path, kind: str = "LOCK_EX") -> str:
    """Whether another process could take the legacy lock now, with portalocker 3.2.0 (exclusive, or shared)."""
    return subprocess.run(  # noqa: S603 (our own interpreter and script)
        [sys.executable, "-c", TRY, str(lock), kind], capture_output=True, text=True, check=True
    ).stdout.strip()


def _lock_file(tmp_path: Path) -> Path:
    data = tmp_path / "legacy" / "data"
    data.mkdir(parents=True)
    lock = data / ".write.lock"
    lock.write_bytes(b"")
    return lock


def test_a_lock_the_legacy_app_holds_is_reported_held_without_waiting(tmp_path: Path) -> None:
    lock = _lock_file(tmp_path)
    with legacy_app_saving(lock):
        assert isinstance(legacy_locks_for_this_os().try_lock(str(lock)), LegacyHeld)
    assert lock.read_bytes() == b""


def test_a_free_lock_is_held_until_released_and_never_written(tmp_path: Path) -> None:
    lock = _lock_file(tmp_path)
    held = legacy_locks_for_this_os().try_lock(str(lock))
    assert not isinstance(held, LegacyAbsent | LegacyHeld)
    try:
        assert (legacy_state(lock), legacy_state(lock, "LOCK_SH")) == ("held", "held")
    finally:
        held.release()
    assert legacy_state(lock) == "free"
    assert lock.read_bytes() == b""


def test_an_absent_lock_is_reported_absent_and_never_created(tmp_path: Path) -> None:
    lock = tmp_path / "legacy" / "data" / ".write.lock"
    assert isinstance(legacy_locks_for_this_os().try_lock(str(lock)), LegacyAbsent)
    assert not lock.exists()
    assert not (tmp_path / "legacy").exists()


KINDS = ["folder", "link"] if sys.platform == "win32" else ["folder", "fifo", "link", "unreadable"]
"""Windows has no FIFO and no permission bits (`tests/windows_pending.py`: NO_FIFO, NO_UID)."""


@pytest.mark.parametrize("kind", KINDS)
def test_a_lock_that_cannot_be_tried_raises(tmp_path: Path, kind: str) -> None:
    """Any outcome other than held, free or absent is an error, so Copy refuses (fail closed, INV-22)."""
    lock = tmp_path / "legacy" / "data" / ".write.lock"
    lock.parent.mkdir(parents=True)
    match kind:
        case "folder":
            lock.mkdir()
        case "fifo":
            if sys.platform != "win32":  # never on Windows (`KINDS`)
                os.mkfifo(lock)
        case "link":
            (lock.parent / "real").write_bytes(b"")
            lock.symlink_to(lock.parent / "real")
        case _:
            lock.write_bytes(b"")
            lock.chmod(0o000)
    try:
        with pytest.raises(OSError, match=r"."):
            legacy_locks_for_this_os().try_lock(str(lock))
    finally:
        if kind == "unreadable":
            lock.chmod(0o600)
