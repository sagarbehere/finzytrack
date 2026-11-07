"""One engine's process group: our signal, the drain before the reap, and "no live member" per OS (S0057; cut in
S0073 and restored in S0075, ADR-0068 note of 2026-11-01; `design/specs/engine-jobs.md` §5.2; review 4 M5, review 5
M3).

- **The engine leads its own process group** (`start_new_session`), so a plugin's child processes are in it, and our
  end signals them too: a `flock` lock is shared with every child that inherited its descriptor, so a child left
  alive would keep the engine lock.
- **Drain before the reap, never a signal after it.** `EngineGroup.drain` (on the engine's reaper thread) waits for
  the leader with `waitid(P_PID, pid, WEXITED | WNOWAIT)`, so it stays an unreaped zombie and its group id cannot be
  reused; signals the group (`SIGKILL`) until it has no live member; then reaps it with `wait4`. From the moment the
  drain decides to reap, `signal()` sends nothing: after the reap the group id may belong to another program. A
  leader that exited by itself (a crash) is drained the same way. One signal is not enough: on macOS it misses a
  member forked while it is delivered (S0073's reviews; BL-0190). A member stuck in the kernel keeps the drain going,
  and so the engine slot held (§5.2, limit "stuck process": an engine or a member of its group "keeps the engine slot
  (the drain waits for it) and any engine lock it holds").
- **"No live member"**, so that none of the group's files (its `flock` locks) is still open: macOS: no process but
  the held leader is listed in the group (libproc `proc_listpids(PROC_PGRP_ONLY)`), not counting a listed member
  whose status reads zombie (`sysctl(KERN_PROC_PID)`'s `p_stat == SZOMB`: its files are closed; BL-0158: a zombie
  whose parent left the group and never reaps it would otherwise keep the drain going). Not `killpg(pgid, 0)`'s
  answer: it says `EPERM` both for the held zombie leader alone and while a SIGKILLed member is still exiting with
  its files open (found in S0057's push: the lock outlived "no live member" in 36 of 40 runs); a member still
  exiting (listed, not yet a zombie) keeps the drain going, and so do a listing that fails and a status that cannot
  be read. Linux: no process in `/proc` has that group in a state other than `Z` (a zombie's files are closed before
  it becomes one; it still answers `killpg(pgid, 0)` there).
- **Windows** has no process groups: the Job Object nested in the shell's is BL-0087.

`GroupOps` is the port to the OS, so a test can record every signal; `PosixGroupOps` is the real one. The pure
decisions (`live_member_in_group_listing`, `live_member_in_proc`, `proc_stat_group`) are tested on their own.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import sys
import threading
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

FIRST_PAUSE = 0.005  # the drain's first pause between two signals; it doubles up to `LONGEST_PAUSE`
LONGEST_PAUSE = 0.25  # a member stuck in the kernel is probed four times a second, for as long as it lives


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

    def live_member(self, pgid: int) -> bool:
        """Whether the group still has a member that may hold a file: on macOS any process listed in the group but
        the exited leader we hold and members whose status reads zombie (BL-0158), on Linux any member not in state
        `Z`."""
        ...

    def leader_exited(self, pid: int) -> bool:
        """Whether the leader has exited (not reaped), without waiting."""
        ...

    def reap(self, pid: int) -> Reaped:
        """Reap the exited leader."""
        ...

    def pause(self, seconds: float) -> None:
        """Wait between two signals of the drain."""
        ...


def live_member_in_group_listing(listing: list[int] | None, leader: int, zombies: Collection[int]) -> bool:
    """macOS: a live member is any process listed in the group but the leader we hold and the `zombies`, the listed
    pids whose status was read as a zombie (files closed; BL-0158) (§5.2). A failed listing (None) counts as live, so
    the drain never reaps on a guess; a member whose status could not be read is not in `zombies`, so it counts."""
    return listing is None or any(pid != leader and pid not in zombies for pid in listing)


PROC_PGRP_ONLY = 2  # <sys/proc_info.h>
CTL_KERN, KERN_PROC, KERN_PROC_PID = 1, 14, 1  # <sys/sysctl.h>
SZOMB = 5  # <sys/proc.h>: the process has exited, its files closed, and waits for its parent's reap
KP_PROC_P_STAT = 36  # offsets in `struct kinfo_proc` (`kp_proc`, an `extern_proc`) on 64-bit macOS
KP_PROC_P_PID = 40


def _group_listing(pgid: int) -> list[int] | None:
    """macOS: the pids libproc lists in the process group `pgid`, exiting members and zombies included; None if
    the listing fails."""
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError:
        return None
    size = ctypes.sizeof(ctypes.c_int)
    room = 64
    while True:
        buffer = (ctypes.c_int * room)()
        filled = libproc.proc_listpids(PROC_PGRP_ONLY, pgid, buffer, room * size)
        if filled < 0:
            return None
        count = filled // size
        if count < room:
            return [pid for pid in buffer[:count] if pid > 0]
        room *= 4  # the group may have more members than the buffer held: list again


def status_reads_zombie(pid: int) -> bool:
    """macOS: whether `pid`'s status reads zombie (`p_stat == SZOMB` in `sysctl(KERN_PROC_PID)`). Only a positive read
    says so: a process that is exiting (not a zombie yet, its files may still be open), gone, or unreadable is not.
    Not `proc_pidinfo(PROC_PIDTBSDINFO)`: it fails with ESRCH for a zombie (S0075, macOS 26)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return False
    mib = (ctypes.c_int * 4)(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 4, None, ctypes.byref(size), None, 0) != 0 or size.value <= KP_PROC_P_PID + 4:
        return False
    buffer = (ctypes.c_uint8 * size.value)()
    if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0 or size.value <= KP_PROC_P_PID + 4:
        return False  # gone between the two calls (size 0), or a failure
    read_pid = int.from_bytes(bytes(buffer[KP_PROC_P_PID : KP_PROC_P_PID + 4]), sys.byteorder, signed=True)
    return read_pid == pid and buffer[KP_PROC_P_STAT] == SZOMB


def is_exit_report(si_code: int) -> bool:
    """A `waitid` report of the leader's end (exited, killed, dumped core), not of a stop or a continue."""
    return si_code in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED)


def proc_stat_group(text: str) -> tuple[int, str]:
    """The process group and the state from a `/proc/<pid>/stat` line. The command name sits in parentheses and may
    hold spaces and parentheses itself, so the fields are read after the last `)`."""
    rest = text[text.rindex(")") + 1 :].split()
    return int(rest[2]), rest[0]  # state, ppid, pgrp, ...


def live_member_in_proc(entries: Iterable[tuple[int, str]], pgid: int) -> bool:
    """Linux: a live member is a process of the group in any state but `Z`."""
    return any(group == pgid and state != "Z" for group, state in entries)


def _proc_entries() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            out.append(proc_stat_group((entry / "stat").read_text(encoding="utf-8", errors="replace")))
        except (OSError, ValueError, IndexError):  # the process ended while the listing was read
            continue
    return out


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

    def live_member(self, pgid: int) -> bool:
        """Every member counts until it is gone; the leader (our child, `pgid`) counts until it has exited."""
        if sys.platform.startswith("linux"):
            return live_member_in_proc(_proc_entries(), pgid)
        listing = _group_listing(pgid)
        zombies = frozenset(pid for pid in listing or () if pid != pgid and status_reads_zombie(pid))
        if live_member_in_group_listing(listing, pgid, zombies):
            return True
        return listing is not None and pgid in listing and not self._exited(pgid)

    def _exited(self, pid: int) -> bool:
        try:
            return self.leader_exited(pid)
        except ChildProcessError:  # not our child (any more): it cannot be a member we hold
            return True

    def reap(self, pid: int) -> Reaped:
        _, status, usage = os.wait4(pid, 0)
        rss = usage.ru_maxrss if sys.platform.startswith("linux") else usage.ru_maxrss // 1024  # macOS: bytes
        return Reaped(os.waitstatus_to_exitcode(status), rss)

    def pause(self, seconds: float) -> None:
        threading.Event().wait(seconds)


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
        """The drain has found no live member and reaps, or has reaped: no signal goes out any more."""
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
        """Hold the exited leader, signal the group until it has no live member, then reap (§5.2)."""
        self._ops.wait_leader(self._pid)
        self.exited.set()
        pause = FIRST_PAUSE
        while True:
            with self._lock:
                self._ops.kill_group(self._pid)
                if not self._ops.live_member(self._pid):
                    self._reaping = True
                    break
            self._ops.pause(pause)
            pause = min(2 * pause, LONGEST_PAUSE)
        return self._ops.reap(self._pid)


__all__ = [
    "EngineGroup",
    "GroupOps",
    "PosixGroupOps",
    "Reaped",
    "is_exit_report",
    "live_member_in_group_listing",
    "live_member_in_proc",
    "proc_stat_group",
    "status_reads_zombie",
]
