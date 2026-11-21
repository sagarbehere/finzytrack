"""The engine's Job Object over a fake Win32 API, on every OS (S0129 round 1, verifier V1-V3; after the verifier's own
repro). `windows_job` is imported with `sys.platform` set to `win32` for the import only; its `_Api` is replaced by
`FakeWin`, which models what the drain must survive:

- one leader (pid 1) and one plugin child (pid 2) in the job;
- `TerminateJobObject` takes the child off the job's list at once, while its process object stays unsignalled for
  `exiting_checks` more looks (it is still closing its handles: the module's docstring, "No live member");
- the listing can fail (`fail_listings` times), or list fewer pids than are assigned (`partial` times);
- the child's process can refuse our open (`deny_open`: a plugin's child with a DACL on itself), for as long as the
  test says;
- `IsProcessInJob` can fail (`in_job_fails`), for as long as the test says (S0137, BL-0291 (1));
- `extra` more members (pids from `EXTRA`), each ended and gone at once by `TerminateJobObject`, so that a job can
  have more members than the module's first buffer holds (64; S0137, BL-0291 (2)): a buffer too small for the whole
  list fails with `ERROR_MORE_DATA`, or, with `short_succeeds`, succeeds with as many pids as fit and
  `NumberOfProcessIdsInList` below `NumberOfAssignedProcesses` (the other answer Microsoft's
  `JOBOBJECT_BASIC_PROCESS_ID_LIST` documents for a short buffer; S0137, verifier F4).

`reaped_early` records whether the reap read the leader while the child was still running or closing its handles:
the false "drained" the drain must never give.
"""

from __future__ import annotations

import ctypes
import importlib
import sys
from types import ModuleType
from typing import Any

ERROR_ACCESS_DENIED = 5
ERROR_GEN_FAILURE = 31
ERROR_INVALID_PARAMETER = 87
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
LEADER, CHILD = 1, 2
EXTRA = 1000


def windows_job() -> ModuleType:
    """`app.platform.adapters.windows_job`, imported as on Windows (its platform statement read as `win32`)."""
    importlib.import_module("app.platform.adapters.engine_group")  # for real first: only windows_job sees win32
    return as_on_windows("app.platform.adapters.windows_job")


def windows_files() -> ModuleType:
    """`app.platform.adapters.windows_files`, imported as on Windows."""
    return as_on_windows("app.platform.adapters.windows_files")


def as_on_windows(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    real = sys.platform
    sys.platform = "win32"
    try:
        return importlib.import_module(name)
    finally:
        sys.platform = real


class FakeWin:
    """A fake `windows_job._Api` (the module's docstring)."""

    def __init__(
        self,
        *,
        fail_listings: int = 0,
        partial: int = 0,
        deny_open: bool = False,
        assign_fails: bool = False,
        in_job_fails: bool = False,
        extra: int = 0,
        short_succeeds: bool = False,
    ) -> None:
        self.short_succeeds = short_succeeds
        self.extras = [EXTRA + i for i in range(extra)]
        self.extras_ended = False
        self.listed = [LEADER, CHILD, *self.extras]
        self.leader_ended = False
        self.child_exiting = False
        self.child_signalled = False
        self.exiting_checks = 3
        self.fail_listings = fail_listings
        self.partial = partial
        self.deny_open = deny_open
        self.assign_fails = assign_fails
        self.in_job_fails = in_job_fails
        self.listings = 0
        self.job_terminations = 0
        self.leader_terminations = 0
        self.reaped_early: bool | None = None
        self.handles: dict[int, tuple[str, int]] = {}
        self.closed: list[int] = []
        self.error = 0
        self._next = 100

    def handle(self, kind: str, pid: int) -> int:
        self._next += 1
        self.handles[self._next] = (kind, pid)
        return self._next

    # -- the calls `windows_job._Api` binds ----------------------------------------------------------------------

    def CreateJobObjectW(self, attrs: object, name: object) -> int:
        return self.handle("job", 0)

    def SetInformationJobObject(self, *args: object) -> int:
        return 1

    def AssignProcessToJobObject(self, job: int, proc: int) -> int:
        if self.assign_fails:
            self.error = ERROR_ACCESS_DENIED
            return 0
        return 1

    def QueryInformationJobObject(self, job: int, cls: int, buf: Any, size: int, ret: object) -> int:
        wj = windows_job()
        if cls != wj.JOB_OBJECT_BASIC_PROCESS_ID_LIST:
            return 0  # accounting: no CPU known
        self.listings += 1
        if self.fail_listings > 0:
            self.fail_listings -= 1
            self.error = ERROR_GEN_FAILURE
            return 0
        ids = list(self.listed)
        shown = ids
        if self.partial > 0 and len(ids) > 1:
            self.partial -= 1
            shown = ids[:1]
        pid_size = ctypes.sizeof(ctypes.c_size_t)
        if self.short_succeeds and 8 + len(shown) * pid_size > size:
            shown = shown[: (size - 8) // pid_size]  # as many as fit, and success
        raw = len(ids).to_bytes(4, "little") + len(shown).to_bytes(4, "little")
        raw += b"".join(p.to_bytes(pid_size, "little") for p in shown)
        if len(raw) > size:
            self.error = wj.ERROR_MORE_DATA
            return 0
        ctypes.memmove(buf, raw, len(raw))
        return 1

    def TerminateJobObject(self, job: int, code: int) -> int:
        self.job_terminations += 1
        self.leader_ended = True
        if LEADER in self.listed:
            self.listed.remove(LEADER)
        if CHILD in self.listed:
            self.listed.remove(CHILD)  # it leaves the list at once ...
            self.child_exiting = True  # ... while it is still closing its handles
        self.extras_ended = True
        self.listed = [p for p in self.listed if p not in self.extras]  # ended and gone at once
        return 1

    def TerminateProcess(self, h: int, code: int) -> int:
        self.leader_terminations += 1
        self.leader_ended = True
        if LEADER in self.listed:
            self.listed.remove(LEADER)
        return 1

    def IsProcessInJob(self, h: int, job: int, out: Any) -> int:
        if self.in_job_fails:
            self.error = ERROR_ACCESS_DENIED
            return 0
        ctypes.cast(out, ctypes.POINTER(ctypes.c_int)).contents.value = 1
        return 1

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        if pid == CHILD and self.deny_open:
            self.error = ERROR_ACCESS_DENIED
            return 0
        if pid == CHILD and not self.child_signalled:
            return self.handle("proc", CHILD)
        if pid == LEADER and not self.leader_ended:
            return self.handle("proc", LEADER)
        if pid in self.extras and not self.extras_ended:
            return self.handle("proc", pid)
        self.error = ERROR_INVALID_PARAMETER  # gone
        return 0

    def WaitForSingleObject(self, h: int, ms: int) -> int:
        kind, pid = self.handles.get(h, ("", 0))
        if kind == "proc" and pid == CHILD:
            if self.child_exiting:
                self.exiting_checks -= 1
                if self.exiting_checks <= 0:
                    self.child_signalled = True
            return WAIT_OBJECT_0 if self.child_signalled else WAIT_TIMEOUT
        if kind == "leader" or (kind == "proc" and pid == LEADER):
            return WAIT_OBJECT_0 if self.leader_ended else WAIT_TIMEOUT
        if kind == "proc" and pid in self.extras:
            return WAIT_OBJECT_0 if self.extras_ended else WAIT_TIMEOUT
        return WAIT_OBJECT_0

    def GetExitCodeProcess(self, h: int, out: Any) -> int:
        self.reaped_early = not self.child_signalled  # running, or still closing its handles
        return 1

    def K32GetProcessMemoryInfo(self, *args: object) -> int:
        return 0

    def CloseHandle(self, h: int) -> int:
        self.closed.append(h)
        self.handles.pop(h, None)
        return 1
