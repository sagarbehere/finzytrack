"""One engine's process group: our signal, the drain before the reap, and "no live member" per OS (S0057;
`design/specs/engine-jobs.md` §5.2; review 4 M5, review 5 M3).

Written from the spec:
- "Each engine leads its own process group ... Our end signals the whole group, so a plugin's child processes go too
  and cannot keep the ledger-set locks: a `flock` lock is shared with every child that inherited its descriptor."
- "Drain before the reap, never a group signal after it: wait for the leader with `waitid(P_PID, pid, WEXITED |
  WNOWAIT)` so it stays an unreaped zombie and its group id cannot be reused; signal the group until it has **no live
  member**; then `wait4(pid)` for the status, `ru_maxrss` and CPU. After the reap no signal is sent to that group:
  its id may already belong to another program."
- "No live member", per OS (zombies do not count): macOS: `killpg(pgid, 0)` fails with `ESRCH` or `EPERM`; with the
  leader held as a zombie and nothing else left it answers `EPERM`. Linux: no process in `/proc` has that group id in
  a state other than `Z`.
- "A process that cannot be ended (stuck in the kernel ...) keeps the engine slot and its locks until it ends."
"""

from __future__ import annotations

import errno
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
    live_member_by_probe,
    live_member_in_proc,
    proc_stat_group,
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


# -- "No live member", per OS, as pure decisions --------------------------------------------------------------------


def test_macos_a_group_that_answers_the_probe_has_a_live_member() -> None:
    assert live_member_by_probe(None) is True


@pytest.mark.parametrize("err", [ProcessLookupError(errno.ESRCH, "no such"), PermissionError(errno.EPERM, "zombie")])
def test_macos_esrch_and_eperm_mean_no_live_member(err: OSError) -> None:
    """EPERM is the answer for a group whose only member is our zombie leader (review 5 spike, Darwin 25.6); every
    member is our own descendant under our user, so EPERM cannot mean a live member we may not signal."""
    assert live_member_by_probe(err) is False


def test_macos_any_other_probe_error_counts_as_a_live_member() -> None:
    """An answer the spec does not name keeps the drain going (the slot stays held, the advice shows): never a
    reap, and a reused group id, on a guess."""
    assert live_member_by_probe(OSError(errno.EINVAL, "invalid")) is True


@pytest.mark.parametrize(
    ("entries", "live"),
    [
        ([], False),
        ([(PGID, "Z")], False),  # the leader held as a zombie, nothing else: the drain ends
        ([(PGID, "Z"), (PGID, "S")], True),  # a zombie leader and a live grandchild: the drain goes on
        ([(PGID, "Z"), (PGID, "D")], True),  # a member stuck in the kernel is live
        ([(PGID, "Z"), (PGID, "R"), (PGID + 1, "S")], True),
        ([(PGID + 1, "S"), (1, "R")], False),  # other groups do not count
        ([(PGID, "Z"), (PGID, "X")], True),  # any state but Z counts as live
    ],
)
def test_linux_only_non_zombie_members_of_the_group_are_live(entries: list[tuple[int, str]], live: bool) -> None:
    assert live_member_in_proc(entries, PGID) is live


@pytest.mark.parametrize(
    ("text", "group"),
    [
        ("123 (python3) S 1 4242 4242 0 -1 4194560", (4242, "S")),
        ("124 (a b) c) (d) Z 1 77 77 0", (77, "Z")),  # a command name with spaces and parentheses
    ],
)
def test_a_proc_stat_line_gives_the_group_and_the_state(text: str, group: tuple[int, str]) -> None:
    assert proc_stat_group(text) == group


# -- The drain over a fake group port ---------------------------------------------------------------------------------


class FakeOps:
    """Records every call in order; `live` says how many probes find a live member before none is left."""

    def __init__(self, live: int = 0, *, stuck: bool = False, reap: Callable[[], Reaped] | None = None) -> None:
        self.calls: list[str] = []
        self._live = live
        self._stuck = stuck
        self._reap = reap
        self.leader_exited = threading.Event()
        self.leader_exited.set()

    def kill_group(self, pgid: int) -> None:
        self.calls.append(f"kill {pgid}")

    def wait_leader(self, pid: int) -> None:
        self.leader_exited.wait()
        self.calls.append(f"wait_leader {pid}")

    def live_member(self, pgid: int) -> bool:
        self.calls.append(f"probe {pgid}")
        if self._stuck:
            return True
        if self._live > 0:
            self._live -= 1
            return True
        return False

    def reap(self, pid: int) -> Reaped:
        self.calls.append(f"reap {pid}")
        return self._reap() if self._reap else Reaped(returncode=-9, max_rss_kib=1024)

    def pause(self, seconds: float) -> None:
        assert seconds > 0
        self.calls.append("pause")
        if self._stuck and self.calls.count("pause") >= 50:
            raise StopDraining


class StopDraining(Exception):
    """Ends a drain that would go on for ever (a stuck member), so the test can look at what it did."""


def test_the_leader_is_held_before_the_group_is_drained_and_reaped_last() -> None:
    ops = FakeOps(live=2)
    group = EngineGroup(ops, PGID)
    reaped = group.drain()
    assert reaped == Reaped(returncode=-9, max_rss_kib=1024)
    assert ops.calls[0] == f"wait_leader {PGID}"
    assert ops.calls[-1] == f"reap {PGID}"
    probes = [c for c in ops.calls if c.startswith("probe")]
    assert len(probes) == 3  # two with a live member, then none
    last_probe = len(ops.calls) - 1 - ops.calls[::-1].index(f"probe {PGID}")
    assert all(not c.startswith("kill") for c in ops.calls[last_probe:]), "a signal after no live member was found"
    first_kill = ops.calls.index(f"kill {PGID}")
    assert first_kill > 0  # never before the leader was held
    assert group.exited.is_set()
    assert group.reaped


def test_a_leader_that_exited_on_its_own_is_drained_too() -> None:
    """A crashed engine's plugin children are ended the same way (S0053 decision: the code is stricter than the
    model's `CrashEng`)."""
    ops = FakeOps(live=1)
    EngineGroup(ops, PGID).drain()
    assert f"kill {PGID}" in ops.calls


def test_no_signal_is_sent_after_the_reap() -> None:
    ops = FakeOps()
    group = EngineGroup(ops, PGID)
    assert group.signal() is True
    group.drain()
    before = list(ops.calls)
    assert group.signal() is False  # an end requested after the reap signals nothing
    assert ops.calls == before


def test_no_signal_is_sent_while_the_reap_is_under_way() -> None:
    """Once the drain found no live member it reaps; from that moment no signal goes out, even when the reap itself
    never returns (a fake reap that blocks)."""
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


def test_a_member_that_never_ends_keeps_the_drain_going_and_the_leader_unreaped() -> None:
    ops = FakeOps(stuck=True)
    with pytest.raises(StopDraining):
        EngineGroup(ops, PGID).drain()
    assert f"reap {PGID}" not in ops.calls
    assert ops.calls.count(f"kill {PGID}") >= 50  # it keeps signalling while a member lives


def test_our_signal_before_the_leader_exited_reaches_the_group_once() -> None:
    ops = FakeOps()
    ops.leader_exited.clear()
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
        assert gone(child) or wait_until(lambda: gone(child))
        assert lock_free(lock), "the lock outlived the drained group"
    finally:
        if not gone(child):
            os.kill(child, signal.SIGKILL)
        leader.stdout.close()


def test_a_group_whose_only_member_is_the_held_zombie_leader_has_no_live_member() -> None:
    """The macOS answer is EPERM (review 5 spike); Linux reads `/proc`. Either way the drain ends."""
    leader = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(3600)"], start_new_session=True)
    ops = PosixGroupOps()
    try:
        assert ops.live_member(leader.pid) is True
        os.kill(leader.pid, signal.SIGKILL)
        ops.wait_leader(leader.pid)  # held, unreaped
        assert ops.live_member(leader.pid) is False
        if sys.platform == "darwin":
            with pytest.raises(PermissionError):
                os.killpg(leader.pid, 0)
    finally:
        leader.returncode = ops.reap(leader.pid).returncode


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
        assert ops.live_member(leader.pid) is True
        os.kill(leader.pid, signal.SIGKILL)
        assert waited.wait(DEADLINE)
    finally:
        os.kill(leader.pid, signal.SIGKILL)
        leader.returncode = ops.reap(leader.pid).returncode
