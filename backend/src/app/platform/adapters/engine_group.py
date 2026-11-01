"""One engine's process group: our signal and the drain before the reap (S0057; simplified in S0073, ADR-0068 D7;
`design/specs/engine-jobs.md` §5.2; review 4 M5).

- **The engine leads its own process group** (`start_new_session`), so a plugin's child processes are in it, and our
  end signals them too: a `flock` lock is shared with every child that inherited its descriptor, so a child left
  alive would keep the engine lock.
- **Signal the group, reap, never signal after the reap.** `EngineGroup.drain` (on the engine's reaper thread) waits
  for the leader with `waitid(P_PID, pid, WEXITED | WNOWAIT)`, so it stays an unreaped zombie and its group id cannot
  be reused while the group is signalled; signals the group once (`SIGKILL`, which also ends the members of a leader
  that exited by itself, a crash); then reaps it with `wait4`. From the moment the drain signals for the last time,
  `signal()` sends nothing: after the reap the group id may belong to another program. The drain does not wait for
  the members to be gone: a member still exiting (or stuck in the kernel) may hold the engine lock it inherited for a
  while, and the next engine waits for it at that lock (§5.1: the sidecar waits for it before every fresh engine, and
  every engine takes it, waiting, before any ledger work). A leader stuck in the kernel keeps the drain waiting, and
  so the engine slot held (§5.2).
- **Windows** has no process groups: the Job Object nested in the shell's is BL-0087.

`GroupOps` is the port to the OS, so a test can record every signal; `PosixGroupOps` is the real one.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Reaped:
    """What the reap tells: the exit as `subprocess` reports it (a negative number is the signal) and the peak
    resident memory in KiB."""

    returncode: int
    max_rss_kib: int


class GroupOps(Protocol):
    """The OS operations on one engine's process group (§5.2)."""

    def kill_group(self, pgid: int) -> None:
        """Send SIGKILL to the group; a group with no member left is not an error."""
        ...

    def wait_leader(self, pid: int) -> None:
        """Wait until the leader has exited, leaving it unreaped (a zombie holding its group id)."""
        ...

    def leader_exited(self, pid: int) -> bool:
        """Whether the leader has exited (not reaped), without waiting."""
        ...

    def reap(self, pid: int) -> Reaped:
        """Reap the exited leader."""
        ...


def is_exit_report(si_code: int) -> bool:
    """A `waitid` report of the leader's end (exited, killed, dumped core), not of a stop or a continue."""
    return si_code in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED)


class PosixGroupOps:
    """The real group operations (POSIX)."""

    def kill_group(self, pgid: int) -> None:
        # No member left, or only our zombie leader (macOS answers EPERM): nothing to signal.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)

    def wait_leader(self, pid: int) -> None:
        while True:
            report = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
            if report is None or is_exit_report(report.si_code):
                return
            # macOS reports a stop (SIGSTOP) here even without WSTOPPED: a frozen engine has not exited. Consume
            # that report without reaping, so the next wait blocks until the real exit.
            os.waitid(os.P_PID, pid, os.WSTOPPED | os.WCONTINUED | os.WNOHANG)

    def leader_exited(self, pid: int) -> bool:
        report = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        return report is not None and is_exit_report(report.si_code)

    def reap(self, pid: int) -> Reaped:
        _, status, usage = os.wait4(pid, 0)
        rss = usage.ru_maxrss if sys.platform.startswith("linux") else usage.ru_maxrss // 1024  # macOS: bytes
        return Reaped(os.waitstatus_to_exitcode(status), rss)


class EngineGroup:
    """One engine's group: `signal()` from any thread, never after the reap; `drain()` once, on its reaper thread."""

    def __init__(self, ops: GroupOps, pid: int) -> None:
        self._ops = ops
        self._pid = pid
        self._lock = threading.Lock()
        self._reaping = False
        self.exited = threading.Event()
        """Set once the leader has exited (it may still be draining)."""

    @property
    def reaped(self) -> bool:
        """The drain has sent its last signal and reaps, or has reaped: no signal goes out any more."""
        with self._lock:
            return self._reaping

    def leader_exited(self) -> bool:
        """The leader has exited by now (asked without waiting; False once reaped: `exited` says it then)."""
        with self._lock:
            if self._reaping:
                return True
            return self._ops.leader_exited(self._pid)

    def signal(self) -> bool:
        """Signal the whole group once; False (nothing sent) once the drain reaps or has reaped."""
        with self._lock:
            if self._reaping:
                return False
            self._ops.kill_group(self._pid)
            return True

    def drain(self) -> Reaped:
        """Hold the exited leader, signal the group once, then reap (§5.2; ADR-0068 D7)."""
        self._ops.wait_leader(self._pid)
        self.exited.set()
        with self._lock:
            self._ops.kill_group(self._pid)
            self._reaping = True
        return self._ops.reap(self._pid)


__all__ = [
    "EngineGroup",
    "GroupOps",
    "PosixGroupOps",
    "Reaped",
    "is_exit_report",
]
