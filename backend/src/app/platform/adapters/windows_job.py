"""One engine's Job Object on Windows: its process group there (S0129, BL-0281; `design/specs/engine-jobs.md` §5.2,
§5.5, §15; ADR-0064). The POSIX group is `app.platform.adapters.engine_group`'s; this module gives the same
`GroupOps` port over a Job Object, so `EngineGroup`'s drain (hold the leader, signal until no live member, reap, never
signal after the reap) runs unchanged on Windows.

- **One job per engine, nested in the shell's.** `EngineJob.around(proc)` makes an unnamed job and puts the engine in
  it right after its spawn, before the client sends its first line (the handshake): the engine starts no process
  before it reads a request, so every process it or a plugin starts is born in the job (a child of a member is a
  member). The sidecar runs in the shell's job, so the engine's job is nested in it (Windows 8 and later); a job that
  cannot be made or joined fails the start (`start_failed`), and the engine, still alone, is ended.
- **Kill-on-close** (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`): the sidecar holds the only handle on the job (not
  inheritable; the engine gets none). When the sidecar ends, however it ends, Windows closes that handle and ends every
  member: the engine's end "when the sidecar is gone" (§5.2) on Windows, the engine's own end-of-input exit aside.
- **No breakaway.** The job sets neither `BREAKAWAY_OK` nor `SILENT_BREAKAWAY_OK`, so a member that asks to leave
  (`CREATE_BREAKAWAY_FROM_JOB`) is refused, or, where a job above allows breakaway, leaves only that one and stays in
  the engine's (observed on the runner, whose job allows it: the 2026-11-18 runs 3 and 4): no process the engine starts
  leaves the drain's reach that way.
  A process another program starts on a member's behalf (a service, the task scheduler, a COM server, the shell's
  `ShellExecute`) is not in the job and is out of reach, as a `setsid` child is on POSIX (§15).
- **Our signal** (`kill_group`): first no new member (`JOB_OBJECT_LIMIT_ACTIVE_PROCESS` of 1: a process started by a
  member is ended as it is made, before it runs; nothing outside the job can add one, since only we hold its handle),
  then a handle on every listed member, then `TerminateJobObject`. The handles are held until the reap: a pid we hold
  cannot pass to another program, and each one tells when that member has ended. The job is ended only in a round
  where every listed member is held; otherwise (a failed or partial listing, a member we cannot open) the leader
  alone is ended and the drain lists again (S0129 round 1, V1; owner, 2026-11-18: "b").
- **"No live member"** (`live_member`): every listed member (`JobObjectBasicProcessIdList`) held, and every member we
  hold signalled (one still listed but signalled has closed its files; our handle keeps its pid from passing to another
  program). A process object is signalled only after Windows has closed its handles, but it leaves the job's list
  earlier, while it is still exiting, so the list alone would let the drain reap while a member still holds a file
  (the macOS listing's "exiting member" case, §5.2); the held handles close that gap. A failed listing, or a listed
  member we cannot open (other than one already gone), counts as live: the drain never reaps on a guess.
- **The reap**: the leader's exit code, its peak working set, and the job's CPU (its accounting's user and kernel time,
  every member's, ended ones included: on Windows the job's CPU is not a lower bound, as POSIX's is); then every
  handle held is closed, the job's last. Our end's exit code is `ENDED_BY_US` (Windows has no signal number).

Windows only: importing it elsewhere fails.
"""

from __future__ import annotations

import contextlib
import ctypes
import subprocess
import sys
import threading
from ctypes import wintypes
from typing import Any, Final

from app.platform.adapters.engine_group import Reaped, live_member_in_job

# The one statement of this module's platform: elsewhere the import fails, and pyright checks the rest for Windows
# only (`scripts/pyright_platforms.py`).
if sys.platform != "win32":
    raise ImportError("the engine's Job Object is Windows only")  # noqa: TRY003 (one raise site)

ENDED_BY_US: Final = 137
"""The exit code `TerminateJobObject` gives every member (128 + SIGKILL, as a POSIX shell reports a killed child); a
recorded reason wins over the exit status anyway (§5.2)."""
JOB_OBJECT_LIMIT_ACTIVE_PROCESS: Final = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION: Final = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST: Final = 3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final = 9
SYNCHRONIZE: Final = 0x00100000
PROCESS_TERMINATE: Final = 0x0001
PROCESS_SET_QUOTA: Final = 0x0100
PROCESS_VM_READ: Final = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
LEADER_ACCESS: Final = (
    SYNCHRONIZE | PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION
)
MEMBER_ACCESS: Final = SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
INFINITE: Final = 0xFFFFFFFF
WAIT_OBJECT_0: Final = 0
WAIT_TIMEOUT: Final = 0x102
ERROR_INVALID_PARAMETER: Final = 87
ERROR_MORE_DATA: Final = 234


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class IO_COUNTERS(ctypes.Structure):
    _fields_ = (
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    )


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    )


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = (
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    )


def _sig(fn: Any, args: list[Any], res: Any) -> Any:
    fn.argtypes = args
    fn.restype = res
    return fn


class _Api:
    """The system calls, typed (bound once per job: no state across engines)."""

    def __init__(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h, d, b, p = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_void_p
        self.CreateJobObjectW = _sig(k32.CreateJobObjectW, [p, wintypes.LPCWSTR], h)
        self.SetInformationJobObject = _sig(k32.SetInformationJobObject, [h, ctypes.c_int, p, d], b)
        self.QueryInformationJobObject = _sig(
            k32.QueryInformationJobObject, [h, ctypes.c_int, p, d, ctypes.POINTER(d)], b
        )
        self.AssignProcessToJobObject = _sig(k32.AssignProcessToJobObject, [h, h], b)
        self.TerminateJobObject = _sig(k32.TerminateJobObject, [h, wintypes.UINT], b)
        self.TerminateProcess = _sig(k32.TerminateProcess, [h, wintypes.UINT], b)
        self.IsProcessInJob = _sig(k32.IsProcessInJob, [h, h, ctypes.POINTER(b)], b)
        self.OpenProcess = _sig(k32.OpenProcess, [d, b, d], h)
        self.WaitForSingleObject = _sig(k32.WaitForSingleObject, [h, d], d)
        self.GetExitCodeProcess = _sig(k32.GetExitCodeProcess, [h, ctypes.POINTER(d)], b)
        self.K32GetProcessMemoryInfo = _sig(k32.K32GetProcessMemoryInfo, [h, p, d], b)
        self.CloseHandle = _sig(k32.CloseHandle, [h], b)


def _error(what: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(None, f"{what}: {ctypes.FormatError(code).strip()}", None, code)


class EngineJob:
    """One engine's Job Object and the handles our drain holds (the module's docstring). Thread-safe: our signal comes
    from any thread, the drain runs on the engine's reaper thread."""

    def __init__(self, api: _Api, job: int, leader: int) -> None:
        self._api = api
        self._job: int | None = job
        self._leader = leader
        self._members: dict[int, int] = {}  # pid -> a handle held on it until the reap
        self._lock = threading.Lock()
        self.ops = JobOps(self)

    @classmethod
    def around(cls, proc: subprocess.Popen[Any], api: _Api | None = None) -> EngineJob:
        """A new job with kill-on-close and no breakaway, the engine `proc` in it; `OSError` if Windows refuses, every
        handle opened for it closed. `api`: the system calls (a test's fake; the real ones by default)."""
        api = api or _Api()
        leader = api.OpenProcess(LEADER_ACCESS, False, proc.pid)  # `proc` holds its own handle: the pid is its
        if not leader:
            raise _error("OpenProcess")
        job = api.CreateJobObjectW(None, None)
        if not job:
            err = _error("CreateJobObjectW")
            api.CloseHandle(leader)
            raise err
        made = cls(api, int(job), int(leader))
        try:
            made._set_limits(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 0)
            if not api.AssignProcessToJobObject(job, leader):
                raise _error("AssignProcessToJobObject")
        except OSError:
            made.close()  # the engine is not in it: kill-on-close ends nothing
            raise
        return made

    def _set_limits(self, flags: int, active: int) -> None:
        assert self._job is not None  # noqa: S101 (called while open)
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = flags
        info.BasicLimitInformation.ActiveProcessLimit = active
        if not self._api.SetInformationJobObject(
            self._job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise _error("SetInformationJobObject")

    def member_ids(self) -> list[int] | None:
        """The pids Windows lists in the job now (a process still exiting may be gone from it already); None if the
        listing fails or the job is closed."""
        with self._lock:
            return self._listing()

    def _listing(self) -> list[int] | None:
        """Every pid the job has, or None (not judged): the listing failed, or it listed fewer pids than the job has
        assigned (`NumberOfProcessIdsInList` below `NumberOfAssignedProcesses`) even with room for them all (S0129
        round 1, V2: a partial list is never taken for the whole job)."""
        if self._job is None:
            return None
        room = 64
        while True:
            size = 8 + room * ctypes.sizeof(ctypes.c_size_t)
            buf = ctypes.create_string_buffer(size)
            if self._api.QueryInformationJobObject(self._job, JOB_OBJECT_BASIC_PROCESS_ID_LIST, buf, size, None):
                assigned = int.from_bytes(buf.raw[0:4], "little")
                listed = int.from_bytes(buf.raw[4:8], "little")
                if listed < assigned:
                    if room >= assigned:
                        return None
                    room = 2 * assigned  # more members than the buffer held: list again
                    continue
                ids = (ctypes.c_size_t * listed).from_buffer_copy(
                    buf.raw[8 : 8 + listed * ctypes.sizeof(ctypes.c_size_t)]
                )
                return [int(pid) for pid in ids]
            if ctypes.get_last_error() != ERROR_MORE_DATA:
                return None
            room *= 4  # more members than the buffer held: list again

    def _hold(self, pids: list[int]) -> bool:
        """Open and keep a handle on each listed pid not held yet; False if a member could not be judged (fail
        closed). A pid that is gone already (its object deleted: its handles closed) or that is not in the job (it
        left the list and its pid passed to another program before our open) is no member of ours."""
        assert self._job is not None  # noqa: S101 (called while open)
        judged = True
        for pid in pids:
            if pid in self._members:
                continue
            h = self._api.OpenProcess(MEMBER_ACCESS, False, pid)
            if not h:
                if ctypes.get_last_error() != ERROR_INVALID_PARAMETER:  # not "no such process"
                    judged = False
                continue
            inside = wintypes.BOOL(False)
            if not self._api.IsProcessInJob(h, self._job, ctypes.byref(inside)):
                self._api.CloseHandle(h)
                judged = False
                continue
            if inside.value:
                self._members[pid] = int(h)
            else:
                self._api.CloseHandle(h)
        return judged

    def _ended(self, handle: int) -> bool:
        return self._api.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0

    def terminate(self) -> None:
        """Our signal (§5.2): no new member, a handle on every member, then every member ended. Nothing once closed.

        The job is ended only in a round where every member is held (S0129 round 1, V1; owner, 2026-11-18: "b"): a
        member we do not hold would leave the job's list as it is ended, still closing its handles, and the drain
        would reap on a guess. In a round whose listing is not judged (it failed, or was partial) or that lists a
        member we cannot open, only the leader is ended (held by its own handle: a Cancel still ends the engine), and
        the drain's next round lists again. A member that can never be opened keeps the job unended and the drain
        going, with the advice on the timer (§5.2, "stuck process")."""
        with self._lock:
            if self._job is None:
                return
            # A failed limit leaves members free to start processes during the end; each later signal of the drain
            # lists and ends them, as POSIX's repeated signal does.
            with contextlib.suppress(OSError):
                self._set_limits(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS, 1)
            listed = self._listing()
            if listed is not None and self._hold(listed):
                self._api.TerminateJobObject(self._job, ENDED_BY_US)
            elif self._leader:
                self._api.TerminateProcess(self._leader, ENDED_BY_US)

    def live_member(self) -> bool:
        """Whether a member may still hold a file (the module's docstring, "No live member")."""
        with self._lock:
            if self._job is None:
                return False
            listed = self._listing()
            judged = listed is not None and self._hold(listed)
            return live_member_in_job(listed, judged, {pid: self._ended(h) for pid, h in self._members.items()})

    def wait_leader(self) -> None:
        self._api.WaitForSingleObject(self._leader, INFINITE)

    def leader_exited(self) -> bool:
        return self._ended(self._leader)

    def reap(self) -> Reaped:
        """The leader's exit code and peak memory, the job's CPU; then every handle closed."""
        with self._lock:
            code = wintypes.DWORD(0)
            if not self._api.GetExitCodeProcess(self._leader, ctypes.byref(code)):
                raise _error("GetExitCodeProcess")
            memory = PROCESS_MEMORY_COUNTERS()
            memory.cb = ctypes.sizeof(memory)
            peak = 0
            if self._api.K32GetProcessMemoryInfo(self._leader, ctypes.byref(memory), memory.cb):
                peak = int(memory.PeakWorkingSetSize) // 1024
            cpu = self._cpu_ns()
        self.close()
        return Reaped(int(code.value), peak, cpu)

    def _cpu_ns(self) -> int | None:
        if self._job is None:
            return None
        acct = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not self._api.QueryInformationJobObject(
            self._job, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(acct), ctypes.sizeof(acct), None
        ):
            return None
        return (int(acct.TotalUserTime) + int(acct.TotalKernelTime)) * 100  # 100 ns units

    def close(self) -> None:
        """Close every handle we hold, the job's last (kill-on-close: any member still there ends then). Idempotent."""
        with self._lock:
            for h in self._members.values():
                self._api.CloseHandle(h)
            self._members.clear()
            if self._job is not None:
                self._api.CloseHandle(self._job)
                self._job = None
            if self._leader:
                self._api.CloseHandle(self._leader)
                self._leader = 0


class JobOps:
    """`engine_group.GroupOps` over one engine's job: the pid arguments name the leader, which the job already
    knows."""

    def __init__(self, job: EngineJob) -> None:
        self._job = job

    def kill_group(self, pgid: int) -> None:
        self._job.terminate()

    def wait_leader(self, pid: int) -> None:
        self._job.wait_leader()

    def live_member(self, pgid: int) -> bool:
        return self._job.live_member()

    def leader_exited(self, pid: int) -> bool:
        return self._job.leader_exited()

    def reap(self, pid: int) -> Reaped:
        return self._job.reap()

    def pause(self, seconds: float) -> None:
        threading.Event().wait(seconds)


__all__ = ["ENDED_BY_US", "EngineJob", "JobOps"]
