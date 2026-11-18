"""The engine's Job Object and its drain over a fake Win32 API, on every OS (S0129 round 1, verifier V1-V3;
`tests/platform/fake_win32.py`; `design/specs/engine-jobs.md` §5.2).

Written from the spec and the verifier's repro: "the drain never reaps on a guess". A member Windows takes off the
job's list while it is still closing its handles must be held before the job is ended, so our signal never ends the
job in a round where the listing failed, listed fewer pids than are assigned, or listed a member that could not be
opened: that round ends the leader alone (so a Cancel still ends the engine), the job counts as live, and the drain
lists again (owner, 2026-11-18: "b"). A member that can never be opened keeps the drain going for ever: the advice of
§5.2's stuck process, never a reap.
"""

from __future__ import annotations

import ctypes
import subprocess
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.platform.adapters.engine_group import EngineGroup
from tests.platform.fake_win32 import CHILD, LEADER, FakeWin, windows_job

DEADLINE = 10.0


@pytest.fixture
def fake_errors(monkeypatch: pytest.MonkeyPatch) -> list[FakeWin]:
    """`ctypes.get_last_error` reads the fake's last error, and `ctypes.FormatError` (Windows only) exists: set the
    fake with `fake_errors.append(fake)`."""
    current: list[FakeWin] = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: current[-1].error if current else 0, raising=False)

    def format_error(code: int) -> str:
        return f"error {code}"

    monkeypatch.setattr(ctypes, "FormatError", format_error, raising=False)
    return current


def job_over(fake: FakeWin) -> Any:
    return windows_job().EngineJob(fake, fake.handle("job", 0), fake.handle("leader", LEADER))


def crashed_leader(**kw: Any) -> FakeWin:
    """The engine crashed by itself: only its plugin child is left in the job."""
    fake = FakeWin(**kw)
    fake.leader_ended = True
    fake.listed = [CHILD]
    return fake


def drain_in_thread(job: Any) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    t = threading.Thread(target=lambda: out.append(EngineGroup(job.ops, LEADER).drain()), daemon=True)
    t.start()
    return t, out


def until(cond: Callable[[], bool]) -> bool:
    tick = threading.Event()
    for _ in range(int(DEADLINE / 0.01)):
        if cond():
            return True
        tick.wait(0.01)
    return cond()


def test_the_drain_reaps_only_once_the_child_has_closed_its_handles(fake_errors: list[FakeWin]) -> None:
    """The control: the child is held before the job is ended, so its leaving the list does not end the drain. Kills
    the mutant that skips the hold (`_hold`). @regression-guard (it passes on the round's base 18f8a43e, whose
    listing worked; it must keep passing with the fix)."""
    fake = crashed_leader()
    fake_errors.append(fake)
    EngineGroup(job_over(fake).ops, LEADER).drain()
    assert fake.job_terminations >= 1
    assert fake.reaped_early is False, "the drain reaped while the child was still closing its handles"


def test_a_failed_listing_in_our_signal_does_not_end_the_job_in_that_round(fake_errors: list[FakeWin]) -> None:
    """V1: a listing that fails at our signal ends nothing but the leader; the next round lists, holds and ends."""
    fake = crashed_leader(fail_listings=1)
    fake_errors.append(fake)
    EngineGroup(job_over(fake).ops, LEADER).drain()
    assert fake.reaped_early is False, "the drain reaped while the child was still closing its handles"


def test_a_member_whose_open_is_refused_keeps_the_job_unended_and_the_drain_going(
    fake_errors: list[FakeWin],
) -> None:
    """V1: a plugin's child with a DACL that refuses our open (`ACCESS_DENIED`) cannot be held, so the job is not
    ended and the drain does not reap for as long as it lasts; once it can be opened, the drain ends it and reaps
    after its handles are closed."""
    fake = crashed_leader(deny_open=True)
    fake_errors.append(fake)
    t, out = drain_in_thread(job_over(fake))
    assert until(lambda: fake.listings >= 6), "the drain stopped listing"
    assert not out, "the drain reaped while a member could not be judged"
    assert fake.job_terminations == 0, "the job was ended with a member we do not hold"
    fake.deny_open = False
    t.join(DEADLINE)
    assert out, "the drain did not end once the member could be opened"
    assert fake.reaped_early is False


def test_a_listing_shorter_than_the_jobs_members_is_not_judged(fake_errors: list[FakeWin]) -> None:
    """V2: a listing that names fewer pids than the job has (`NumberOfProcessIdsInList` below
    `NumberOfAssignedProcesses`) is not taken for the whole job. Our Cancel meets it with the engine running: the
    leader alone is ended, the job is not, and the drain then holds the child before it ends the job."""
    fake = FakeWin(partial=1)
    fake_errors.append(fake)
    job = job_over(fake)
    job.ops.kill_group(LEADER)  # Cancel: our one signal
    assert fake.job_terminations == 0, "the job was ended on a partial listing"
    assert fake.leader_terminations == 1, "the Cancel did not end the engine"
    EngineGroup(job.ops, LEADER).drain()
    assert fake.reaped_early is False


def test_a_job_windows_refuses_closes_what_it_opened_and_raises(fake_errors: list[FakeWin]) -> None:
    """V3: `EngineJob.around` with an assignment Windows refuses raises `OSError` and closes the job and the leader's
    handle (the client then ends the engine, still alone: `test_engine_client_spawn.py`)."""
    fake = FakeWin(assign_fails=True)
    fake_errors.append(fake)
    proc = cast("subprocess.Popen[bytes]", SimpleNamespace(pid=LEADER))  # all `around` reads of it
    with pytest.raises(OSError, match="AssignProcessToJobObject"):
        windows_job().EngineJob.around(proc, api=fake)
    assert not fake.handles, f"handles left open: {fake.handles}"
