"""A test-only hook (BL-0195, S0156): the tests' own hang paths signal an engine only while the client's reaper still
holds its leader unreaped.

The client spawns each engine as the leader of its own process group, and its reaper holds the exited leader unreaped
(`PosixGroupOps.wait_leader`, `waitid` with `WNOWAIT`) until the group has no live member, then reaps it
(`PosixGroupOps.reap`, `wait4`; engine-jobs §5.2). While the leader is unreaped, alive or a zombie, its process id
and its group id cannot be given to another process, so a signal to either reaches only our engine and its group.
After the reap they may belong to any program. A test that waited and found a call still hanging (`interrupt`'s and
`bounded_call`'s `killpg`, `frozen_until_released`'s `SIGCONT` in `test_engine_client.py`) reads `c.pid` and then
signals it; if a broken client had reaped that engine meanwhile, the signal could reach a reused id.

`install` wraps the two operations on the class, so every `PosixGroupOps` of the test, subclasses included, records
the leaders it holds: added when the reaper starts to wait for one, removed under the same lock just before its reap.
`signal_held` sends its signal under that lock, and only to a leader still held, so no reap can come between the check
and the signal. A leader the reaper has not started to wait for yet is not signalled: the hang paths fire only long
after the spawn, and a missed signal fails the test loudly, never quietly.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading

import pytest

from app.platform.adapters.engine_group import PosixGroupOps, Reaped

_lock = threading.Lock()
_held: set[int] = set()


def held(pid: int) -> bool:
    """Whether the reaper holds `pid` unreaped now."""
    with _lock:
        return pid in _held


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record the leaders every `PosixGroupOps` holds, for the test's lifetime (`monkeypatch` undoes it)."""
    real_wait = PosixGroupOps.wait_leader
    real_reap = PosixGroupOps.reap

    def wait_leader(self: PosixGroupOps, pid: int) -> None:
        with _lock:
            _held.add(pid)
        real_wait(self, pid)

    def reap(self: PosixGroupOps, pid: int) -> Reaped:
        with _lock:  # the leader has exited (the drain reaps only then): `wait4` returns at once
            _held.discard(pid)
            return real_reap(self, pid)

    monkeypatch.setattr(PosixGroupOps, "wait_leader", wait_leader)
    monkeypatch.setattr(PosixGroupOps, "reap", reap)


def signal_held(pid: int, sig: int, *, group: bool) -> bool:
    """Send `sig` to the engine `pid` (`group`: its whole process group) only while the reaper still holds it; whether
    it was sent. A signal the OS refuses (the group already gone but for our zombie) is not an error."""
    with _lock:
        if pid not in _held:
            return False
        with contextlib.suppress(OSError):
            if not group:
                os.kill(pid, sig)
            elif sys.platform != "win32":  # process groups are POSIX (the hang paths' tests skip on Windows)
                os.killpg(pid, sig)
        return True
