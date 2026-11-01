"""One engine's process group: our signal, the drain before the reap (S0057; simplified in S0073, ADR-0068 D7;
`design/specs/engine-jobs.md` §5.2; review 4 M5).

Written from the spec:
- "Each engine leads its own process group ... Our end signals the whole group, so a plugin's child processes go too
  and cannot outlive their engine." (A `flock` lock, the engine lock included, is shared with every child that
  inherited its descriptor.)
- ADR-0068 D7: the drain is simplified: signal the group, reap, never signal after the reap; the per-OS listing of the
  group's members goes. The leader is held unreaped (`waitid(P_PID, pid, WEXITED | WNOWAIT)`) while the group is
  signalled, so its group id cannot be reused by then; after the reap no signal is sent to that group. A member still
  exiting after the reap may still hold the engine lock: the next engine waits for it there (§5.1), not here.
- "A process that cannot be ended (stuck in the kernel ...) keeps the engine slot ... until it ends": the leader.
"""

from __future__ import annotations

import faulthandler
import fcntl
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.platform.adapters.engine_group import (
    EngineGroup,
    PosixGroupOps,
    Reaped,
    is_exit_report,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")

DEADLINE = 20.0
HANG_GUARD = 15 * DEADLINE  # the test's own hang detector (below), far above its longest wait (a bytecode fill)


@pytest.fixture(autouse=True)
def hang_guard() -> Iterator[None]:
    """Since S0057 the client has no time limits (engine-jobs §5.4), so a check that misses what it should catch (a
    violation, an end) would leave its test waiting for ever. This guard is the test's, not the product's: after
    `HANG_GUARD` seconds it ends the test process with every thread's traceback, which names the hung test (with
    xdist, the worker's crash names it too)."""
    faulthandler.dump_traceback_later(HANG_GUARD, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


PGID = 4242


# -- The drain over a fake group port ---------------------------------------------------------------------------------


class FakeOps:
    """Records every call in order. `live_member` stands for the per-OS member listing the drain no longer makes
    (ADR-0068 D7): it answers that a member lives for ever, so a drain that still probed would never reap."""

    def __init__(self, *, reap: Callable[[], Reaped] | None = None) -> None:
        self.calls: list[str] = []
        self._reap = reap
        self.leader_exited_event = threading.Event()
        self.leader_exited_event.set()

    def kill_group(self, pgid: int) -> None:
        self.calls.append(f"kill {pgid}")

    def wait_leader(self, pid: int) -> None:
        self.leader_exited_event.wait()
        self.calls.append(f"wait_leader {pid}")

    def leader_exited(self, pid: int) -> bool:
        return self.leader_exited_event.is_set() and "wait_leader" in " ".join(self.calls)

    def live_member(self, pgid: int) -> bool:
        self.calls.append(f"probe {pgid}")
        return True

    def reap(self, pid: int) -> Reaped:
        self.calls.append(f"reap {pid}")
        return self._reap() if self._reap else Reaped(returncode=-9, max_rss_kib=1024)

    def pause(self, seconds: float) -> None:
        self.calls.append("pause")
        if self.calls.count("pause") >= 50:
            raise StopDraining


class StopDraining(Exception):
    """Ends a drain that would go on for ever (one that still waits for its members), so the test can see it."""


def test_the_drain_holds_the_leader_signals_the_group_once_and_reaps() -> None:
    """ADR-0068 D7: signal the group, reap, never signal after the reap; no listing of the group's members, so a
    member that never ends (stuck in the kernel) does not keep the drain from reaping the leader."""
    ops = FakeOps()
    group = EngineGroup(ops, PGID)
    reaped = group.drain()
    assert reaped == Reaped(returncode=-9, max_rss_kib=1024)
    assert ops.calls == [f"wait_leader {PGID}", f"kill {PGID}", f"reap {PGID}"]
    assert group.exited.is_set()
    assert group.reaped


def test_a_leader_that_exited_on_its_own_is_drained_too() -> None:
    """A crashed engine's plugin children are ended the same way (S0053 decision: the code is stricter than the
    model's `CrashEng`)."""
    ops = FakeOps()
    EngineGroup(ops, PGID).drain()
    assert ops.calls == [f"wait_leader {PGID}", f"kill {PGID}", f"reap {PGID}"]


def test_no_signal_is_sent_after_the_reap() -> None:
    ops = FakeOps()
    group = EngineGroup(ops, PGID)
    assert group.signal() is True
    group.drain()
    before = list(ops.calls)
    assert group.signal() is False  # an end requested after the reap signals nothing
    assert ops.calls == before


def test_no_signal_is_sent_while_the_reap_is_under_way() -> None:
    """Once the drain has signalled the group it reaps; from that moment no signal goes out, even when the reap
    itself never returns (a fake reap that blocks)."""
    release = threading.Event()
    ops = FakeOps(reap=lambda: (release.wait(), Reaped(0, 0))[1])
    group = EngineGroup(ops, PGID)
    t = threading.Thread(target=group.drain, daemon=True)
    t.start()
    assert wait_until(lambda: f"reap {PGID}" in ops.calls)
    calls = list(ops.calls)
    assert group.signal() is False
    assert ops.calls == calls
    release.set()
    t.join(DEADLINE)


def test_our_signal_before_the_leader_exited_reaches_the_group_once() -> None:
    ops = FakeOps()
    ops.leader_exited_event.clear()
    group = EngineGroup(ops, PGID)
    assert group.signal() is True
    assert ops.calls == [f"kill {PGID}"]
    assert not group.exited.is_set()


# -- On real processes ------------------------------------------------------------------------------------------------

LEADER = r"""
import fcntl, os, subprocess, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"], pass_fds=(fd,))
print(child.pid, flush=True)
time.sleep(3600)
"""


def wait_until(cond: Callable[[], bool], timeout: float = DEADLINE) -> bool:
    step = threading.Event()
    end = threading.Event()
    timer = threading.Timer(timeout, end.set)
    timer.start()
    try:
        while not end.is_set():
            if cond():
                return True
            step.wait(0.02)
        return cond()
    finally:
        timer.cancel()


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    return False


def lock_free(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def test_a_child_holding_a_lock_is_ended_with_the_leader_and_the_lock_is_free(tmp_path: Path) -> None:
    lock = tmp_path / "ledger.lock"
    leader = subprocess.Popen(  # noqa: S603 (this interpreter with a fixed script)
        [sys.executable, "-I", "-c", LEADER, str(lock)], stdout=subprocess.PIPE, text=True, start_new_session=True
    )
    assert leader.stdout is not None
    child = int(leader.stdout.readline())
    try:
        assert os.getpgid(leader.pid) == leader.pid
        assert os.getpgid(child) == leader.pid
        assert not lock_free(lock)
        group = EngineGroup(PosixGroupOps(), leader.pid)
        assert group.signal() is True
        reaped = group.drain()
        leader.returncode = reaped.returncode  # reaped by the drain, as the client does
        assert reaped.returncode == -signal.SIGKILL
        assert wait_until(lambda: gone(child)), "the child outlived its signalled group"
        assert wait_until(lambda: lock_free(lock)), "the lock outlived the signalled group's members"
    finally:
        if not gone(child):
            os.kill(child, signal.SIGKILL)
        leader.stdout.close()


def test_only_an_end_is_an_exit_report() -> None:
    assert all(is_exit_report(code) for code in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED))
    assert not any(is_exit_report(code) for code in (os.CLD_STOPPED, os.CLD_CONTINUED, os.CLD_TRAPPED))


def test_a_frozen_leader_is_not_taken_for_an_exited_one() -> None:
    """macOS answers `waitid(WEXITED | WNOWAIT)` for a stopped child too (found in S0057): a frozen engine (SIGSTOP)
    must not be drained and reaped as if it had ended."""
    leader = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(3600)"], start_new_session=True)
    ops = PosixGroupOps()
    waited = threading.Event()
    try:
        os.kill(leader.pid, signal.SIGSTOP)
        t = threading.Thread(target=lambda: (ops.wait_leader(leader.pid), waited.set()), daemon=True)
        t.start()
        assert not waited.wait(0.5), "a stopped leader was taken for an exited one"
        assert ops.leader_exited(leader.pid) is False
        os.kill(leader.pid, signal.SIGKILL)
        assert waited.wait(DEADLINE)
    finally:
        os.kill(leader.pid, signal.SIGKILL)
        leader.returncode = ops.reap(leader.pid).returncode


def test_after_a_drain_the_signalled_member_lets_go_of_its_lock(tmp_path: Path) -> None:
    """§5.2 (ADR-0068 D7): the drain signals the group once after the leader died on its own (a crash), so the member
    that inherited the lock is ended by the drain itself. The lock may still be held at the reap (macOS: a SIGKILLed
    member exits with its files open for a moment); it is free once that member has exited, and until then the next
    engine waits at the engine lock (§5.1). Repeated, since the member's exit races the reap."""
    for i in range(20):
        lock = tmp_path / f"ledger-{i}.lock"
        leader = subprocess.Popen(  # noqa: S603 (this interpreter with a fixed script)
            [sys.executable, "-I", "-c", LEADER, str(lock)], stdout=subprocess.PIPE, text=True, start_new_session=True
        )
        assert leader.stdout is not None
        child = int(leader.stdout.readline())
        try:
            os.kill(leader.pid, signal.SIGKILL)
            reaped = EngineGroup(PosixGroupOps(), leader.pid).drain()
            leader.returncode = reaped.returncode
            assert wait_until(lambda c=child: gone(c)), f"run {i}: the member outlived the drain's signal"
            assert wait_until(lambda p=lock: lock_free(p)), f"run {i}: the lock outlived its member"
        finally:
            if not gone(child):
                os.kill(child, signal.SIGKILL)
            leader.stdout.close()
