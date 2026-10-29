"""The engine job scheduler shell (`app.platform.engine_jobs.scheduler`; S0058; `design/specs/engine-jobs.md` §3-§5,
§11, §14; ADR-0064).

The shell runs the queue table (`app.platform.engine_jobs.queue`, S0053) on one thread against an engine port. These
tests drive it over a fake port (`tests/platform/fake_job_engine.py`) and, at the end, over the real client and
engine. Each expected value is written from the spec's tables, row by row: §3 (the order, the bound, the open
workspace), §4 (Cancel, the one rule, the commit point, a client that goes away), §5.1-§5.3 (one engine, fresh after
user code, the lock before every spawn, our ends, a death by the engine's number, the bound) and §11 (the codes).

Every test has a hang guard (S0057 retro): nothing in the shell has a time limit, so a missed answer would wait for
ever; the guard ends the run with every thread's traceback. A wait for something that must happen is a wait on a
condition with a deadline (EP-28); where a test shows that something did *not* happen, it looks once after `ABSENCE`
seconds, far above the time the event takes (EP-28 deviation, as in S0053 and S0057).
"""

from __future__ import annotations

import faulthandler
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, override

import pytest

from app.platform.adapters.engine_client import EngineClient, EngineCommand
from app.platform.adapters.engine_lock import engine_lock_path, wait_engine_lock
from app.platform.engine import EndReason, EngineEnd, EngineEvent, EngineFailure, GateReached
from app.platform.engine_jobs.queue import JobKind, JobSpec, QueuePolicy
from app.platform.engine_jobs.scheduler import (
    Done,
    EngineJobs,
    Failed,
    JobContext,
    JobTicket,
    Outcome,
    Work,
    WorkResult,
)
from app.platform.engine_jobs.text import ADVICE, describe, notice
from app.platform.engine_supervision import Reset, RestartPolicy
from app.platform.errors import (
    EngineStoppedDetails,
    EngineUnavailableDetails,
    ErrorCode,
    JobCancelledDetails,
    WorkspaceNotOpenDetails,
    spec_for,
)
from app.platform.observability import Event
from app.platform.observability.events import ENGINE_EXIT, ENGINE_START_FAILED, JOB_INTERNAL_ERROR
from tests.platform.fake_job_engine import FakeEngine

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"
DEADLINE = 20.0
ABSENCE = 0.5
DELAY = 2.0  # §5.4's display delay; on the tests' clock, which moves only when a test moves it
W1 = "ws-1"
W2 = "ws-2"


@pytest.fixture(autouse=True)
def hang_guard() -> Iterator[None]:
    """The test's own hang detector, not the product's (S0057 retro, lesson 3)."""
    faulthandler.dump_traceback_later(15 * DEADLINE, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[Event, dict[str, object]]] = []
        self._lock = threading.Lock()

    def __call__(self, event: Event, /, **fields: object) -> None:
        with self._lock:
            self.lines.append((event, fields))

    def of(self, event: Event) -> list[dict[str, object]]:
        with self._lock:
            return [f for e, f in self.lines if e == event]


class LockWait:
    """The sidecar's wait for the engine lock (§5.1): held until `free()`, or raising until `fix()`."""

    def __init__(self) -> None:
        self.calls = 0
        self.failing = False
        self._open = threading.Event()
        self._open.set()

    def hold(self) -> None:
        self._open.clear()

    def free(self) -> None:
        self._open.set()

    def __call__(self) -> None:
        self.calls += 1
        if self.failing:
            msg = "a file where locks/ belongs"
            raise NotADirectoryError(msg)
        assert self._open.wait(DEADLINE), "the lock wait was never freed"


class Clock:
    """The scheduler's monotonic clock in these tests: it moves only when a test moves it, and `EngineJobs.tick()`
    makes the scheduler look at its timers then (owner's rule: wall time never decides a test; S0058 round 1)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    def __init__(
        self,
        *,
        policy: QueuePolicy | None = None,
        restart: RestartPolicy | None = None,
        engine: FakeEngine | None = None,
    ) -> None:
        self.engine = engine or FakeEngine()
        self.lock = LockWait()
        self.log = Log()
        self.clock = Clock()
        self.jobs = EngineJobs(
            self.engine,
            wait_engine_lock=self.lock,
            policy=policy or QueuePolicy(max_queued=8),
            restart=restart or RestartPolicy(),
            display_delay=DELAY,
            monotonic=self.clock,
            log=self.log,
        )
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended

    def boot(self) -> None:
        """The boot's own work (§9.2): the lock wait, the boot's recover-all (its first engine), the window's end;
        then W1 opens."""
        ticket = self.jobs.start()
        assert isinstance(ticket, JobTicket)
        assert outcome(ticket) == Done(None)
        wait_until(lambda: not self.jobs.status().queue.exclusive)
        self.jobs.workspace_opened(W1)

    def close(self) -> None:
        self.jobs.close()

    def later(self, seconds: float) -> None:
        """Time passes; the scheduler fires what is due before this returns."""
        self.clock.now += seconds
        self.jobs.tick()

    def advice_after_the_delay(self) -> bool:
        self.later(DELAY)
        return self.jobs.status().queue.advice


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    r.boot()
    yield r
    r.close()


def wait_until(cond: Callable[[], bool], timeout: float = DEADLINE) -> None:
    end = time.monotonic() + timeout
    step = threading.Event()
    while time.monotonic() < end:
        if cond():
            return
        step.wait(0.01)
    assert cond(), "the condition never held"


def absent_for_a_while() -> None:
    threading.Event().wait(ABSENCE)


def user(kind: JobKind, ws: str | None = W1) -> JobSpec:
    return JobSpec(kind=kind, workspace=ws, waited_for=True, confirmed=kind in ("save", "create", "copy"), owner="user")


def background(kind: JobKind, ws: str | None = W1) -> JobSpec:
    return JobSpec(kind=kind, workspace=ws, waited_for=False, confirmed=False, owner="app")


def calls(*ops: str, ran_user_code: bool = True, seen: list[object] | None = None) -> Work:
    """A job whose engine work is these requests, in order; it returns the last answer."""

    def work(ctx: JobContext) -> WorkResult:
        out: object = None
        for op in ops:
            out = ctx.call(op, {})
            if seen is not None:
                seen.append(out)
            if isinstance(out, EngineFailure):
                return WorkResult(out, ran_user_code=ran_user_code)
        return WorkResult(out, ran_user_code=ran_user_code)

    return work


def submit(rig: Rig, spec: JobSpec, work: Work) -> JobTicket:
    ticket = rig.jobs.submit(spec, work)
    assert isinstance(ticket, JobTicket), ticket
    return ticket


def outcome(ticket: JobTicket) -> Outcome:
    out = ticket.wait(DEADLINE)
    assert out is not None, f"job {ticket.id} was never answered"
    return out


def code_of(out: object) -> str:
    assert isinstance(out, Failed), out
    return out.error.code


def details(out: object) -> object:
    assert isinstance(out, Failed), out
    return out.error.details


def running(rig: Rig, ticket: JobTicket) -> None:
    wait_until(lambda: (r := rig.jobs.status().queue.running) is not None and r.job.id == ticket.id)


def blocked(rig: Rig, n: int = 1) -> None:
    """`n` requests have reached the fake engine and wait there."""
    wait_until(lambda: sum(1 for _, op in rig.engine.calls if op in ("block", "gate")) >= n)


def slot(rig: Rig) -> str:
    return rig.jobs.status().queue.slot


# -- a job's life (§3, §5.1) ------------------------------------------------------------------------------------------


def test_a_submitted_job_runs_on_the_engine_and_returns_its_value(rig: Rig) -> None:
    ticket = submit(rig, user("preview"), calls("ok"))
    assert outcome(ticket) == Done({"op": "ok"})


def test_an_engine_that_ran_user_code_is_retired_and_one_that_only_parsed_serves_the_next_job(rig: Rig) -> None:
    """§5.1: "An engine that ran user code is ended after its job ... An engine that ran only parse-only work or
    recovery may serve the next job." The boot's recover-all left its engine (serial 1) idle."""
    assert rig.engine.serial == 1
    assert outcome(submit(rig, user("bql"), calls("ok", ran_user_code=False))) == Done({"op": "ok"})
    assert rig.engine.calls[-1] == (1, "ok"), "parse-only work did not reuse the idle engine"
    assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})
    assert rig.engine.calls[-1] == (1, "ok")
    wait_until(lambda: (1, "retire") in rig.engine.ends)
    assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})
    assert rig.engine.calls[-1] == (2, "ok"), "the job after user code did not get a fresh engine"


def test_a_running_job_is_never_interrupted_and_the_waited_for_job_goes_first(rig: Rig) -> None:
    """§3: "when the engine is free, the scheduler starts the oldest queued waited-for job; if there is none, the
    oldest background job. A running job is never interrupted to start another." """
    order: list[str] = []

    def named(name: str, *ops: str) -> Work:
        inner = calls(*ops)

        def work(ctx: JobContext) -> WorkResult:
            order.append(name)
            return inner(ctx)

        return work

    first = submit(rig, user("bql"), named("bql", "block"))
    blocked(rig)
    bg = submit(rig, background("r4"), named("r4", "ok"))
    fg = submit(rig, user("preview"), named("preview", "ok"))
    absent_for_a_while()
    assert order == ["bql"], "a job started beside the running one"
    rig.engine.release()
    for t in (first, fg, bg):
        assert isinstance(outcome(t), Done)
    assert order == ["bql", "preview", "r4"]


def test_a_job_for_a_workspace_that_is_not_open_is_refused_at_once(rig: Rig) -> None:
    """§3, INV-28: "its request gets `WORKSPACE_NOT_OPEN`"."""
    out = rig.jobs.submit(user("preview", W2), calls("ok"))
    assert code_of(out) == ErrorCode.WORKSPACE_NOT_OPEN
    assert details(out) == WorkspaceNotOpenDetails(open_workspace=W1, switching=False)
    assert rig.engine.calls == []


def test_a_request_beyond_max_queued_is_refused_with_engine_queue_full() -> None:
    """§3: "A request beyond it gets `ENGINE_QUEUE_FULL` and is not queued"; §11 "queue full"."""
    rig = Rig(policy=QueuePolicy(max_queued=1))
    try:
        rig.boot()
        first = submit(rig, user("bql"), calls("block"))
        blocked(rig)
        second = submit(rig, user("preview"), calls("ok"))
        out = rig.jobs.submit(user("preview"), calls("ok"))
        assert code_of(out) == ErrorCode.ENGINE_QUEUE_FULL
        rig.engine.release()
        assert isinstance(outcome(first), Done)
        assert isinstance(outcome(second), Done)
    finally:
        rig.close()


def test_a_background_save_is_capped_at_one_queued_per_workspace(rig: Rig) -> None:
    """§3: "The app keeps at most one queued `r4` and one queued background save per workspace"; model
    `AppBgSave` (`~QHas(... j.k = "save" /\\ ~j.wf)`): a second one is the first one."""
    first = submit(rig, user("bql"), calls("block"))
    blocked(rig)
    save = rig.jobs.submit_background_save(W1, calls("ok"))
    again = rig.jobs.submit_background_save(W1, calls("ok"))
    assert isinstance(save, JobTicket)
    assert isinstance(again, JobTicket)
    assert again.id == save.id
    queued = rig.jobs.status().queue.queued
    assert [(j.kind, j.waited_for, j.owner) for j in queued] == [("save", False, "app")]
    rig.engine.release()
    assert isinstance(outcome(first), Done)
    assert isinstance(outcome(save), Done)


# -- Cancel (§4) ------------------------------------------------------------------------------------------------------


def test_cancel_of_a_queued_job_removes_it_and_nothing_ran(rig: Rig) -> None:
    first = submit(rig, user("bql"), calls("block"))
    blocked(rig)
    ran: list[object] = []
    queued = submit(rig, user("preview"), calls("ok", seen=ran))
    assert rig.jobs.cancel(queued.id) == "cancelled"
    assert code_of(outcome(queued)) == ErrorCode.JOB_CANCELLED
    assert details(outcome(queued)) == JobCancelledDetails(cannot_stop_ahead=False)
    rig.engine.release()
    assert isinstance(outcome(first), Done)
    absent_for_a_while()
    assert ran == []


def test_cancel_of_a_running_job_ends_its_engine_with_reason_cancel_and_the_next_job_gets_a_fresh_one(
    rig: Rig,
) -> None:
    """§4: "running, not a writing job ...: ends the engine (§5.2); the outcome is `cancelled`, not a stop"; §5.1: the
    next engine only after the previous group is drained."""
    ticket = submit(rig, user("preview"), calls("block"))
    blocked(rig)
    serial = rig.engine.serial
    assert rig.jobs.cancel(ticket.id) == "cancelled"
    assert code_of(outcome(ticket)) == ErrorCode.JOB_CANCELLED
    assert (serial, "cancel") in rig.engine.ends
    assert outcome(submit(rig, user("bql"), calls("ok"))) == Done({"op": "ok"})
    assert rig.engine.calls[-1] == (serial + 1, "ok")


def test_three_cancels_in_a_row_are_never_a_stop(rig: Rig) -> None:
    """§14 "Cancel": three Cancels in a row leave the engine startable at once, not unavailable."""
    for _ in range(3):
        ticket = submit(rig, user("preview"), calls("block"))
        blocked(rig, 1)
        assert rig.jobs.cancel(ticket.id) == "cancelled"
        assert code_of(outcome(ticket)) == ErrorCode.JOB_CANCELLED
        rig.engine.calls.clear()
    assert outcome(submit(rig, user("bql"), calls("ok"))) == Done({"op": "ok"})
    assert not rig.jobs.status().queue.unavailable


def test_a_cancel_at_the_gate_wins_and_nothing_proceeds(rig: Rig) -> None:
    """§4: "In one step the scheduler either ends the engine (a Cancel is pending: outcome `cancelled`, disk
    unchanged) or marks the job past the commit point and sends `proceed`"; here the Cancel is processed before the
    shell feeds the `gate` (the job's own `on_event` sees the gate first and cancels)."""

    def work(ctx: JobContext) -> WorkResult:
        def first(event: EngineEvent) -> None:
            if isinstance(event, GateReached):
                assert rig.jobs.cancel(ctx.job_id) == "cancelled"

        return WorkResult(ctx.call("gate", {}, on_event=first))

    ticket = submit(rig, user("save"), work)
    assert code_of(outcome(ticket)) == ErrorCode.JOB_CANCELLED
    absent_for_a_while()
    assert rig.engine.proceeded == 0, "a proceed was sent after the Cancel won"


def test_a_cancel_after_proceed_is_too_late_and_the_save_finishes(rig: Rig) -> None:
    """§4: "A Cancel that arrives after that step is answered 'too late' and changes nothing"; §11: "Finishing your
    change…", then its outcome."""
    ticket = submit(rig, user("save"), lambda ctx: WorkResult(ctx.call("gate", {})))
    wait_until(lambda: rig.engine.proceeded == 1)
    assert rig.jobs.cancel(ticket.id) == "too_late"
    shown = describe(rig.jobs.status(), ticket.id, now=rig.clock.now)
    assert shown is not None
    assert shown.text == "Finishing your change…"
    assert not shown.cancel
    rig.engine.release("gate")
    assert outcome(ticket) == Done({"wrote": True})
    assert not any(reason == "cancel" for _, reason in rig.engine.ends)


def test_the_one_rule_a_waiting_users_cancel_frees_the_engine_behind_an_r4(rig: Rig) -> None:
    """§4: "Another job's Cancel ends the running job only when nobody waits for it ...: an R4 load"; §14 "Cancel of
    a job queued behind a hung R4 load ... frees the engine"."""
    r4 = submit(rig, background("r4"), calls("block"))
    blocked(rig)
    waiting = submit(rig, user("preview"), calls("ok"))
    assert rig.jobs.cancel(waiting.id) == "cancelled"
    assert code_of(outcome(r4)) == ErrorCode.JOB_CANCELLED
    assert code_of(outcome(waiting)) == ErrorCode.JOB_CANCELLED
    assert any(reason == "cancel" for _, reason in rig.engine.ends)
    wait_until(lambda: slot(rig) == "none")


def test_the_one_rule_behind_a_confirmed_save_the_waiting_users_own_request_is_cancelled(rig: Rig) -> None:
    """§4: "Otherwise the waiting user cannot end the running job ... a save ... the user confirmed. The waiting job
    shows the usual 'Waiting for: <label>…' and, after the display delay, 'Still working…' with Cancel, never quit
    advice on a timer ... their own request is cancelled and they read 'Your request is cancelled. The earlier ledger
    work cannot be stopped from here; ...'"."""
    save = submit(rig, user("save"), calls("block"))
    blocked(rig)
    waiting = submit(rig, user("preview"), calls("ok"))
    shown = describe(rig.jobs.status(), waiting.id, now=rig.clock.now, delay=DELAY)
    assert shown is not None
    assert shown.text.startswith("Waiting for: ")
    assert shown.cancel
    later = describe(rig.jobs.status(), waiting.id, now=rig.clock.now + 10 * DELAY, delay=DELAY)
    assert later is not None
    assert later.text == "Still working…"
    assert not rig.advice_after_the_delay(), "quit advice on a timer for work that may only be slow"
    assert rig.jobs.cancel(waiting.id) == "cancelled"
    assert details(outcome(waiting)) == JobCancelledDetails(cannot_stop_ahead=True)
    assert save.outcome is None, "the confirmed save was ended"
    assert not any(reason == "cancel" for _, reason in rig.engine.ends)
    rig.engine.release()
    assert isinstance(outcome(save), Done)


def test_a_client_that_goes_away_cancels_its_read_but_never_a_save(rig: Rig) -> None:
    """§4: "A client that goes away ... cancels its queued or running preview, rebuild or BQL. It never cancels a
    save"."""
    read = submit(rig, user("preview"), calls("block"))
    blocked(rig)
    rig.jobs.client_gone(read.id)
    assert code_of(outcome(read)) == ErrorCode.JOB_CANCELLED
    rig.engine.calls.clear()
    save = submit(rig, user("save"), calls("block"))
    blocked(rig)
    rig.jobs.client_gone(save.id)
    absent_for_a_while()
    assert save.outcome is None
    rig.engine.release()
    assert isinstance(outcome(save), Done)


def test_a_background_job_offers_no_cancel_and_ignores_one(rig: Rig) -> None:
    r4 = submit(rig, background("r4"), calls("block"))
    blocked(rig)
    shown = describe(rig.jobs.status(), r4.id, now=rig.clock.now)
    assert shown is not None
    assert not shown.cancel
    assert rig.jobs.cancel(r4.id) == "nothing"
    rig.engine.release()
    assert isinstance(outcome(r4), Done)


def test_a_cancel_that_comes_before_the_jobs_engine_has_started_still_ends_that_engine(rig: Rig) -> None:
    """§4, §5.2 (found on self-review): the job's Cancel can reach the client before the job's worker has begun the
    start of its fresh engine; the client then has nothing to end and starts the engine anyway. That engine must be
    ended too, or the slot waits for it for ever (and no other job starts)."""
    assert isinstance(outcome(submit(rig, user("preview"), calls("ok"))), Done)  # the boot's engine is retired
    wait_until(lambda: slot(rig) == "none")
    cancelled: list[str] = []

    def cancel_first() -> None:
        rig.engine.before_start = None
        wait_until(lambda: rig.jobs.status().queue.running is not None)
        running_now = rig.jobs.status().queue.running
        assert running_now is not None
        cancelled.append(rig.jobs.cancel(running_now.job.id))

    rig.engine.before_start = cancel_first
    ticket = submit(rig, user("preview"), calls("ok"))
    assert code_of(outcome(ticket)) == ErrorCode.JOB_CANCELLED
    wait_until(lambda: cancelled == ["cancelled"])  # the answer reaches the worker after the ticket's outcome
    assert outcome(submit(rig, user("bql"), calls("ok"))) == Done({"op": "ok"}), "the slot waits for an engine"
    started = rig.engine.serial
    assert (started - 1, "cancel") in rig.engine.ends, "the engine started after the Cancel was never ended"


def test_while_a_cancelled_jobs_start_is_under_way_the_slot_is_held(rig: Rig) -> None:
    """§5.1 ("A new engine is spawned ... only after the previous one is reaped and its process group has no live
    member"), §5.2 (the advice for a signalled process that has not ended): the engine a cancelled job's start
    returns is the slot's until its end is reported, so no other engine starts beside it and, if it never drains,
    the advice shows. The slot must not be freed while the start is still under way."""
    assert isinstance(outcome(submit(rig, user("preview"), calls("ok"))), Done)  # the boot's engine is retired
    wait_until(lambda: slot(rig) == "none")

    def cancel_first() -> None:
        rig.engine.before_start = None
        rig.engine.stuck = True  # the engine this start spawns will never report its end
        wait_until(lambda: rig.jobs.status().queue.running is not None)
        running_now = rig.jobs.status().queue.running
        assert running_now is not None
        rig.jobs.cancel(running_now.job.id)

    rig.engine.before_start = cancel_first
    ticket = submit(rig, user("preview"), calls("ok"))
    assert code_of(outcome(ticket)) == ErrorCode.JOB_CANCELLED
    wait_until(lambda: rig.engine.serial == 2)  # the cancelled job's start went on and spawned its engine
    wait_until(lambda: (2, "cancel") in rig.engine.ends)  # and the job ended it
    spawned = rig.engine.serial
    waiting = submit(rig, user("bql"), calls("ok"))
    assert rig.advice_after_the_delay()
    absent_for_a_while()
    assert rig.engine.serial == spawned, "an engine started beside one whose end was never reported"
    assert slot(rig) == "ending"
    rig.engine.release_ends()
    assert outcome(waiting) == Done({"op": "ok"})


def test_a_job_learns_it_has_ended_before_its_engine_is_signalled(rig: Rig) -> None:
    """The order inside one step (found on self-review, with the test above): the Cancel's end reaches the client
    before the job's start has an engine (dropped there), and the start returns before the scheduler has told the
    job it ended. The job must already know, so it ends that engine instead of running on it; the fake holds the
    start until that end and holds the end until the job has acted."""
    assert isinstance(outcome(submit(rig, user("preview"), calls("ok"))), Done)  # the boot's engine is retired
    wait_until(lambda: slot(rig) == "none")
    rig.engine.hold_start = True
    ran: list[object] = []
    ticket = submit(rig, user("preview"), calls("ok", seen=ran))
    running(rig, ticket)  # its start is held (or about to be), with no engine yet
    assert rig.jobs.cancel(ticket.id) == "cancelled"
    assert code_of(outcome(ticket)) == ErrorCode.JOB_CANCELLED
    rig.engine.hold_start = False
    assert outcome(submit(rig, user("bql"), calls("ok"))) == Done({"op": "ok"}), "the slot waits for an engine"
    assert ran == [], "the cancelled job ran on the engine its start returned"


# -- crashes and deaths (§5.3) ----------------------------------------------------------------------------------------


def test_a_crash_during_a_read_is_engine_stopped_and_the_next_job_waits_for_the_end_report() -> None:
    """§5.3: "A read job ends with `ENGINE_STOPPED` and Try again"; §11 "a read job's engine stopped"; §5.2: the next
    engine only after the dead one's group is drained (its end reported)."""
    rig = Rig()
    try:
        rig.boot()
        rig.engine.stuck = True
        crashed = submit(rig, user("preview"), calls("crash"))
        assert code_of(outcome(crashed)) == ErrorCode.ENGINE_STOPPED
        assert details(outcome(crashed)) == EngineStoppedDetails(reason="crashed")
        before = rig.engine.serial
        nxt = submit(rig, user("bql"), calls("ok"))
        absent_for_a_while()
        assert rig.engine.serial == before, "a new engine started before the dead one's end was reported"
        assert nxt.outcome is None
        rig.engine.release_ends()
        assert outcome(nxt) == Done({"op": "ok"})
        assert rig.engine.calls[-1] == (before + 1, "ok")
    finally:
        rig.close()


def test_an_idle_engine_that_dies_is_reported_by_its_number_and_never_reused(rig: Rig) -> None:
    """§5.3: "The shell reports a death by that number"; an idle engine that died is drained and the next job gets
    a fresh one (S0053 verifier round 2: "report every engine death by the number its `Start` carried")."""
    assert isinstance(outcome(submit(rig, user("bql"), calls("ok", ran_user_code=False))), Done)
    assert slot(rig) == "idle"
    dead = rig.engine.die()
    wait_until(lambda: slot(rig) == "none")
    assert outcome(submit(rig, user("bql"), calls("ok"))) == Done({"op": "ok"})
    assert rig.engine.calls[-1] == (dead + 1, "ok")


def test_a_late_report_of_an_earlier_engine_changes_nothing(rig: Rig) -> None:
    """§5.3: "a report about an earlier engine, already ended and drained (§5.2), changes nothing"."""
    assert isinstance(outcome(submit(rig, user("bql"), calls("ok", ran_user_code=False))), Done)
    before = rig.jobs.status().queue
    assert before.slot == "idle"
    rig.jobs.engine_ended(EngineEnd(serial=999, pid=1, reason=None, returncode=-11, max_rss_kib=1))
    rig.jobs.engine_ended(EngineEnd(serial=0, pid=1, reason=None, returncode=-11, max_rss_kib=1))
    absent_for_a_while()
    assert rig.jobs.status().queue.slot == "idle"
    assert outcome(submit(rig, user("bql"), calls("ok", ran_user_code=False))) == Done({"op": "ok"})


def test_the_starts_bytecode_retry_is_not_a_death() -> None:
    """§5.1: "If an engine fails to start while using the directory, the directory is deleted and the engine is
    started once without it": one start, two engines; the first's end is not the job's engine dying."""
    rig = Rig()
    try:
        rig.engine.spawns_per_start = 2
        rig.boot()
        assert rig.engine.serial == 2
        assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})
        assert rig.engine.calls[-1] == (2, "ok")
    finally:
        rig.close()


def test_a_jobs_engine_that_died_between_its_requests_is_not_replaced_behind_its_back(rig: Rig) -> None:
    """§5.3: "The crashed job is not run again by itself" (S0035: a request is never resent): the job's next request
    fails instead of starting another engine inside the job (`call(spawn=False)`)."""
    first_done = threading.Event()
    go_on = threading.Event()

    def work(ctx: JobContext) -> WorkResult:
        ctx.call("ok", {})
        first_done.set()
        assert go_on.wait(DEADLINE)
        return WorkResult(ctx.call("ok", {}))

    ticket = submit(rig, user("preview"), work)
    assert first_done.wait(DEADLINE)
    serial = rig.engine.die()
    go_on.set()
    assert code_of(outcome(ticket)) == ErrorCode.ENGINE_STOPPED
    assert rig.engine.serial == serial, "an engine was started inside the job"


def test_a_signalled_engine_that_never_ends_gets_the_advice_and_no_next_engine() -> None:
    """§5.2: "Whenever the slot is held by a process we have already signalled that has not ended ... the app shows,
    after the display delay, 'Finzytrack cannot stop the earlier ledger work. Quit and reopen Finzytrack.', whether
    or not a job is waiting"; §14: "a fake engine that Cancel cannot end shows 'Quit and reopen Finzytrack' and
    starts no other engine"."""
    rig = Rig()
    try:
        rig.boot()
        ticket = submit(rig, user("preview"), calls("block"))
        blocked(rig)
        rig.engine.stuck = True
        assert rig.jobs.cancel(ticket.id) == "cancelled"
        assert rig.advice_after_the_delay()
        assert notice(rig.jobs.status()) == ADVICE, "no notice while no job waits (N3)"
        waiting = submit(rig, user("bql"), calls("ok"))
        shown = describe(rig.jobs.status(), waiting.id, now=rig.clock.now)
        assert shown is not None
        assert shown.text == ADVICE
        assert notice(rig.jobs.status()) is None, "the advice shows in the waiting job's place"
        before = rig.engine.serial
        absent_for_a_while()
        assert rig.engine.serial == before
        rig.engine.release_ends()
        assert outcome(waiting) == Done({"op": "ok"})
        assert not rig.jobs.status().queue.advice
    finally:
        rig.close()


# -- the engine lock (§5.1, §15; S0053 verifier round 2) --------------------------------------------------------------


def test_no_engine_starts_before_the_boots_wait_for_the_engine_lock_ends_and_the_advice_shows_meanwhile() -> None:
    """§5.1: "At start, before its first engine, the sidecar takes the lock itself, waiting ... While the sidecar
    waits, jobs stay queued ... and after the display delay the app shows 'Finzytrack cannot stop the earlier ledger
    work. Quit and reopen Finzytrack.' whether or not a job waits"."""
    rig = Rig()
    try:
        rig.lock.hold()
        boot = rig.jobs.start()
        assert isinstance(boot, JobTicket)
        assert not rig.jobs.status().queue.advice, "the advice before the display delay"
        assert rig.advice_after_the_delay()
        assert rig.engine.starts == 0, "an engine started while the engine lock was waited for"
        shown = describe(rig.jobs.status(), boot.id, now=rig.clock.now)
        assert shown is not None
        assert shown.text == ADVICE
        assert shown.cancel, "the boot's own job waiting for the lock is the user's to cancel (§9.2)"
        rig.lock.free()
        assert outcome(boot) == Done(None)
        assert rig.engine.starts == 1
        assert not rig.jobs.status().queue.advice
    finally:
        rig.close()


def test_a_failed_wait_for_the_engine_lock_starts_no_engine_and_ends_the_boots_job_unavailable() -> None:
    """S0053 verifier F2 and round 2: a wait that fails (an `OSError`) starts no engine, since an earlier session's
    engine may still run; it is logged `engine.start_failed`; the next job that needs an engine waits again."""
    rig = Rig()
    try:
        rig.lock.failing = True
        boot = rig.jobs.start()
        assert isinstance(boot, JobTicket)
        out = outcome(boot)
        assert code_of(out) == ErrorCode.ENGINE_UNAVAILABLE
        assert details(out) == EngineUnavailableDetails(reason="cannot_start")
        assert rig.engine.starts == 0
        assert [f["kind"] for f in rig.log.of(ENGINE_START_FAILED)] == ["start_failed"]
        wait_until(lambda: not rig.jobs.status().queue.exclusive)
        rig.jobs.workspace_opened(W1)
        rig.lock.failing = False
        assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})
        assert rig.lock.calls == 2
    finally:
        rig.close()


def test_a_log_sink_that_raises_loses_the_line_never_a_decision() -> None:
    """S0041's rule (S0053 verifier R2-F3 for the boot it replaces): a failed wait whose log line raises still ends
    the boot's job unavailable and starts no engine; an end report whose line raises still frees the slot."""

    def broken(event: Event, /, **fields: object) -> None:
        msg = "the log is gone"
        raise OSError(msg)

    rig = Rig()
    rig.jobs = EngineJobs(rig.engine, wait_engine_lock=rig.lock, display_delay=DELAY, log=broken)
    rig.engine.spawned = rig.jobs.engine_spawned
    rig.engine.ended = rig.jobs.engine_ended
    try:
        rig.lock.failing = True
        boot = rig.jobs.start()
        assert isinstance(boot, JobTicket)
        assert code_of(outcome(boot)) == ErrorCode.ENGINE_UNAVAILABLE
        assert rig.engine.starts == 0
        wait_until(lambda: not rig.jobs.status().queue.exclusive)
        rig.jobs.workspace_opened(W1)
        rig.lock.failing = False
        assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})
        wait_until(lambda: slot(rig) == "none")  # the retire's end report was taken in, its line lost
    finally:
        rig.close()


def test_the_engine_lock_is_waited_for_again_before_every_fresh_engine(rig: Rig) -> None:
    """S0053 verifier round 2: "wait for the engine lock before every spawn"; §15: a held lock makes "the next
    engine ... wait for it, with the advice"."""
    assert rig.lock.calls == 1
    assert isinstance(outcome(submit(rig, user("preview"), calls("ok"))), Done)  # reuses the boot's idle engine
    assert rig.lock.calls == 1
    wait_until(lambda: slot(rig) == "none")
    rig.lock.hold()
    nxt = submit(rig, user("bql"), calls("ok"))
    wait_until(lambda: rig.lock.calls == 2)
    assert rig.advice_after_the_delay()
    starts = rig.engine.starts
    absent_for_a_while()
    assert rig.engine.starts == starts, "an engine started before the lock was waited for"
    rig.lock.free()
    assert outcome(nxt) == Done({"op": "ok"})


# -- the engine unavailable (§5.3, §11; model `DispatchUnavail`) ---------------------------------------------------


def test_after_the_bound_jobs_end_unavailable_without_an_engine_until_the_window_lets_a_stop_go() -> None:
    """§5.3: "three stops in 300 s make the engine `Unavailable` until the oldest leaves the window, or Try again";
    §11 "bound reached ... `ENGINE_UNAVAILABLE`". On the tests' clock: the window ends exactly when the oldest
    counted stop is one window old, never earlier (S0040: half-open)."""
    rig = Rig()
    try:
        rig.boot()
        t = rig.clock.now
        rig.engine.unavailable((t, t, t))
        crashed = submit(rig, user("preview"), calls("crash"))
        assert code_of(outcome(crashed)) == ErrorCode.ENGINE_STOPPED
        wait_until(lambda: rig.jobs.status().queue.unavailable)
        starts = rig.engine.starts
        refused = rig.jobs.submit(user("bql"), calls("ok"))
        out = refused if isinstance(refused, Failed) else outcome(refused)
        assert code_of(out) == ErrorCode.ENGINE_UNAVAILABLE
        assert details(out) == EngineUnavailableDetails(reason="stopped_often")
        assert rig.engine.starts == starts, "an engine was started while unavailable"
        rig.later(RestartPolicy().window - 1.0)
        assert rig.jobs.status().queue.unavailable, "available before the oldest stop left the window"
        rig.later(1.0)
        assert not rig.jobs.status().queue.unavailable
        rig.engine.available()
        assert outcome(submit(rig, user("bql"), calls("ok"))) == Done({"op": "ok"})
    finally:
        rig.close()


def test_at_the_windows_end_the_stops_are_counted_again() -> None:
    """§5.3: unavailable "until the oldest leaves the window": the shell reads the client's stops again when its
    timer fires; stops made since keep the bound, and the window is waited for again (S0058 verifier F6)."""
    rig = Rig()
    try:
        rig.boot()
        t = rig.clock.now
        rig.engine.unavailable((t, t, t))
        assert code_of(outcome(submit(rig, user("preview"), calls("crash")))) == ErrorCode.ENGINE_STOPPED
        wait_until(lambda: rig.jobs.status().queue.unavailable)
        window = RestartPolicy().window
        rig.engine.unavailable((t + 100.0, t + 100.0, t + 100.0))  # three newer stops (the client's own count)
        rig.later(window)
        assert rig.jobs.status().queue.unavailable, "available while three stops are still in the window"
        rig.later(100.0)
        assert not rig.jobs.status().queue.unavailable
    finally:
        rig.close()


def test_try_again_resets_the_engine_and_lets_jobs_start() -> None:
    """§5.3: "... or **Try again**"; an incompatible engine is never retried until a reset (S0035). Try again is a
    step of the scheduler; the reset runs beside it, and jobs start once it is done."""
    rig = Rig()
    try:
        rig.boot()
        rig.engine.incompatible()
        rig.engine.die()
        wait_until(lambda: slot(rig) == "none")
        first = submit(rig, user("preview"), calls("ok"))
        assert code_of(outcome(first)) == ErrorCode.ENGINE_UNAVAILABLE
        assert details(outcome(first)) == EngineUnavailableDetails(reason="incompatible")
        wait_until(lambda: rig.jobs.status().queue.unavailable)
        rig.engine.available()
        rig.jobs.try_again()
        wait_until(lambda: not rig.jobs.status().queue.unavailable)
        assert rig.engine.resets == 1
        assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})
    finally:
        rig.close()


def test_r2_a_try_again_is_ignored_while_the_table_lags_the_clients_unavailable(rig: Rig) -> None:
    """Failure-modes E1 "After the bound ... Try again (reset)"; model `UserTryAgain` guards on the client's own
    `Unavail` (S0058 verifier R2-F1). The client is unavailable but the table has not heard it yet (an idle engine's
    death is fed as `EngineDied`, which reads no availability): Try again reads the client first, so it resets."""
    t = rig.clock.now
    rig.engine.unavailable((t, t, t))
    rig.engine.die()
    wait_until(lambda: slot(rig) == "none")
    assert not rig.jobs.status().queue.unavailable
    rig.jobs.try_again()
    wait_until(lambda: rig.engine.resets == 1)
    wait_until(lambda: not rig.jobs.status().queue.unavailable)
    assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})


def test_try_again_while_the_engine_is_available_changes_nothing(rig: Rig) -> None:
    """Model `UserTryAgain` needs `Unavail`: with the engine available there is nothing to reset."""
    rig.jobs.try_again()
    assert rig.engine.resets == 0
    assert outcome(submit(rig, user("preview"), calls("ok"))) == Done({"op": "ok"})


def test_try_again_answers_at_once_while_a_job_runs(rig: Rig) -> None:
    """The scheduler's contract: "each caller waits only for that step's answer, never for the engine" (S0058
    verifier F2: the reset ran on the caller's thread and waited for the client's call lock)."""
    job = submit(rig, user("preview"), calls("block"))
    blocked(rig)
    done = threading.Event()
    threading.Thread(target=lambda: (rig.jobs.try_again(), done.set()), daemon=True).start()
    assert done.wait(DEADLINE), "try_again() waited for the running job's engine call"
    rig.engine.release()
    assert isinstance(outcome(job), Done)


# -- a fresh engine mid-job (§5.1), the boot, the log, close ---------------------------------------------------------


def test_a_job_can_ask_for_a_fresh_engine_for_its_next_part(rig: Rig) -> None:
    """§5.1, §6.1: a job's next part gets a fresh engine after the one that ran user code has ended (`FreshEnd`,
    then `SpawnStep`)."""

    def work(ctx: JobContext) -> WorkResult:
        ctx.call("ok", {})
        failure = ctx.fresh_engine()
        assert failure is None, failure
        return WorkResult(ctx.call("ok", {}))

    first = rig.engine.serial
    assert outcome(submit(rig, user("save"), work)) == Done({"op": "ok"})
    assert [s for s, _ in rig.engine.calls[-2:]] == [first, first + 1]
    assert (first, "retire") in rig.engine.ends


def test_a_recover_all_between_engines_offers_no_cancel(rig: Rig) -> None:
    """S0053 verifier round 2: "show no Cancel for a recover-all between engines that would do nothing (the table
    answers none there, as the model does), or explain the wait"."""
    rig.jobs.workspace_closed()
    rig.engine.stuck = True
    asked = threading.Event()

    def work(ctx: JobContext) -> WorkResult:
        asked.set()
        return WorkResult(None, ran_user_code=False) if ctx.fresh_engine() is None else WorkResult(None)

    spec = JobSpec(kind="recover_all", workspace=None, waited_for=True, confirmed=False, owner="switch")
    rig.jobs.exclusive_began()
    ticket = submit(rig, spec, work)
    assert asked.wait(DEADLINE)
    wait_until(lambda: slot(rig) == "ending")
    shown = describe(rig.jobs.status(), ticket.id, now=rig.clock.now)
    assert shown is not None
    assert not shown.cancel
    assert rig.advice_after_the_delay()
    shown = describe(rig.jobs.status(), ticket.id, now=rig.clock.now)
    assert shown is not None
    assert shown.text == ADVICE, "the wait is explained"
    rig.engine.release_ends()
    assert outcome(ticket) == Done(None)


def test_every_engine_end_is_logged_with_its_job() -> None:
    """BL-0150: "log each engine's end report (exit, peak memory) with its job"; content-free fields only. The line
    names the queue's engine number, not the client's serial (a start can spawn twice, §5.1), the kind of the last
    job started on that engine, and the report's own peak memory (S0058 verifier F3)."""
    engine = FakeEngine(spawns_per_start=2)  # the boot's start spawns serials 1 and 2: both are engine 1
    rig = Rig(engine=engine)
    try:
        rig.boot()
        wait_until(lambda: len(rig.log.of(ENGINE_EXIT)) == 1)  # the boot's first spawn, ended by the retry
        assert isinstance(outcome(submit(rig, user("bql"), calls("ok", ran_user_code=False))), Done)
        assert isinstance(outcome(submit(rig, user("rebuild"), calls("ok"))), Done)  # user code: retired
        wait_until(lambda: len(rig.log.of(ENGINE_EXIT)) == 2)
        lines = sorted(rig.log.of(ENGINE_EXIT), key=lambda f: str(f["reason"]))
        assert lines == [
            {"engine": 1, "job": "rebuild", "reason": "retire", "exit_code": None, "signal": 9, "max_rss_kib": 2002},
            {"engine": 1, "job": "recover_all", "reason": "stop", "exit_code": 1, "signal": None, "max_rss_kib": 1001},
        ]
    finally:
        rig.close()


def test_a_work_that_raises_is_internal_and_its_engine_is_not_reused(rig: Rig) -> None:
    """ARCHITECTURE §6.3: the job runner is one of the three places that turn an exception into `INTERNAL`, logged
    once; the engine it ran on may hold anything, so it is retired (a fresh engine after user code, §5.1)."""

    def work(ctx: JobContext) -> WorkResult:
        ctx.call("ok", {})
        msg = "a bug in a job"
        raise RuntimeError(msg)

    first = rig.engine.serial
    assert code_of(outcome(submit(rig, user("bql"), work))) == ErrorCode.INTERNAL
    assert [f["exception_type"] for f in rig.log.of(JOB_INTERNAL_ERROR)] == ["RuntimeError"]
    assert outcome(submit(rig, user("bql"), calls("ok"))) == Done({"op": "ok"})
    assert rig.engine.calls[-1] == (first + 1, "ok")


def test_an_effect_that_raises_does_not_cut_its_step_short_and_is_logged() -> None:
    """The step rule (S0058 verifier F1): every effect of a step is carried out, also when one of them raises (a bug:
    effects must not raise); the raise is logged `jobs.internal_error`. Here the engine's end raises when the slot's
    engine is retired: the job's answer and the advice timer of the same step still happen, so the held slot is
    explained instead of wedged in silence."""

    class EndRaises(FakeEngine):
        @override
        def end(self, reason: EndReason) -> None:
            if reason == "retire":
                msg = "end refused"
                raise RuntimeError(msg)
            super().end(reason)

    rig = Rig(engine=EndRaises())
    try:
        rig.boot()
        ticket = submit(rig, user("preview"), calls("ok"))
        assert outcome(ticket) == Done({"op": "ok"})
        assert [f["exception_type"] for f in rig.log.of(JOB_INTERNAL_ERROR)] == ["RuntimeError"]
        assert slot(rig) == "ending"
        assert rig.advice_after_the_delay(), "the step's advice timer was lost with the raise"
    finally:
        rig.close()


def test_the_status_read_after_an_answer_shows_that_answer(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """A request's answer and the published status agree: once `submit` has returned, `status()` shows its step
    (found in round 1 as the likely cause of the one loaded failure of round 0: the status was published after the
    caller was answered). The publish is held open here; the answer must wait for it."""
    original = EngineJobs._publish  # pyright: ignore[reportPrivateUsage]
    held = threading.Event()
    hold = threading.Event()

    def slow_publish(self: EngineJobs) -> None:
        if hold.is_set():
            assert held.wait(DEADLINE)
        original(self)

    monkeypatch.setattr(EngineJobs, "_publish", slow_publish)
    first = submit(rig, user("bql"), calls("block"))
    blocked(rig)
    answered: list[JobTicket] = []
    hold.set()
    t = threading.Thread(target=lambda: answered.append(submit(rig, user("preview"), calls("ok"))), daemon=True)
    t.start()
    t.join(ABSENCE)
    if answered:  # answered while its step was not published yet: the status must show it all the same
        queued = answered[0]
        assert any(j.id == queued.id for j in rig.jobs.status().queue.queued), "the status lags the answer"
    held.set()
    t.join(DEADLINE)
    hold.clear()
    (queued,) = answered
    assert any(j.id == queued.id for j in rig.jobs.status().queue.queued)
    rig.engine.release()
    assert isinstance(outcome(first), Done)


def test_a_save_whose_engine_died_past_its_commit_point_is_not_told_its_files_are_safe(rig: Rig) -> None:
    """§5.3: "a save's outcome is resolved from disk (§6.6)"; OutcomeTruthful (S0058 verifier F5). Until BL-0151
    reads the disk, the answer is that the outcome is not known, never "Your ledger files are safe"."""
    save = submit(rig, user("save"), calls("gate_die"))
    out = outcome(save)
    assert rig.engine.proceeded == 1
    assert code_of(out) == ErrorCode.CHANGE_OUTCOME_UNKNOWN
    unknown = spec_for(ErrorCode.CHANGE_OUTCOME_UNKNOWN)
    assert unknown is not None
    assert not unknown.data_safe
    assert not unknown.retryable, "a change that may have landed must not be repeated by a Retry"


def test_a_save_whose_engine_died_before_its_commit_point_is_engine_stopped(rig: Rig) -> None:
    save = submit(rig, user("save"), calls("crash"))
    assert code_of(outcome(save)) == ErrorCode.ENGINE_STOPPED


def test_close_answers_every_waiting_job(rig: Rig) -> None:
    first = submit(rig, user("bql"), calls("block"))
    blocked(rig)
    queued = submit(rig, user("preview"), calls("ok"))
    rig.jobs.close()
    assert details(outcome(queued)) == EngineUnavailableDetails(reason="closing")
    rig.engine.release()
    assert outcome(first) is not None
    later = rig.jobs.submit(user("preview"), calls("ok"))
    assert details(later) == EngineUnavailableDetails(reason="closing")


def test_close_leaves_a_job_past_its_commit_point_unanswered(rig: Rig) -> None:
    """§4: a writing job past its commit point finishes; telling its requester "not done" at close would be untrue
    (OutcomeTruthful; the quit's own path is §9.3, BL-0154)."""
    save = submit(rig, user("save"), lambda ctx: WorkResult(ctx.call("gate", {})))
    wait_until(lambda: rig.engine.proceeded == 1)
    rig.jobs.close()
    absent_for_a_while()
    assert save.outcome is None
    rig.engine.release("gate")


# -- with the real client and engine (§14 "Cancel", "Engine") ------------------------------------------------------


def real_rig(tmp_path: Path) -> tuple[EngineJobs, EngineClient]:
    if not (ENGINE_VENV / "bin" / "python").exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    lock = engine_lock_path(tmp_path)
    holder: list[EngineJobs] = []
    client = EngineClient(
        EngineCommand.for_venv(ENGINE_VENV, test_ops=True, engine_lock=lock),
        on_engine_spawned=lambda s: holder[0].engine_spawned(s),
        on_engine_ended=lambda e: holder[0].engine_ended(e),
    )
    jobs = EngineJobs(client, wait_engine_lock=lambda: wait_engine_lock(lock), display_delay=DELAY)
    holder.append(jobs)
    return jobs, client


@pytest.mark.skipif(os.name != "posix", reason="process groups and flock are POSIX; Windows is BL-0087")
def test_with_the_real_engine_the_boot_starts_it_and_a_stalled_read_is_cancelled(tmp_path: Path) -> None:
    jobs, client = real_rig(tmp_path)
    try:
        boot = jobs.start()
        assert isinstance(boot, JobTicket)
        assert outcome(boot) == Done(None)
        first_pid = client.pid
        assert first_pid is not None
        wait_until(lambda: not jobs.status().queue.exclusive)
        jobs.workspace_opened(W1)
        stalled = jobs.submit(user("preview"), calls("test.stall"))
        assert isinstance(stalled, JobTicket)
        running_wait = time.monotonic() + DEADLINE
        while jobs.status().queue.running is None and time.monotonic() < running_wait:
            threading.Event().wait(0.01)
        assert jobs.cancel(stalled.id) == "cancelled"
        assert code_of(outcome(stalled)) == ErrorCode.JOB_CANCELLED
        ok = jobs.submit(user("bql"), calls("ping"))
        assert isinstance(ok, JobTicket)
        assert outcome(ok) == Done({"status": "ok"})
        assert client.pid != first_pid, "the job after a Cancel ran on the cancelled engine"
    finally:
        jobs.close()
        client.close()


@pytest.mark.skipif(os.name != "posix", reason="process groups and flock are POSIX; Windows is BL-0087")
def test_with_the_real_engine_a_cancel_at_the_gate_leaves_no_file_and_after_proceed_is_too_late(
    tmp_path: Path,
) -> None:
    """§14 "Cancel": "a save cancelled before `gate` leaves every file ... byte-identical; a Cancel arriving in the
    same step as `gate` wins, one arriving after `proceed` is 'too late' and the save finishes"; the engine's
    `test.gate` writes its file only after `proceed`."""
    jobs, client = real_rig(tmp_path)
    try:
        boot = jobs.start()
        assert isinstance(boot, JobTicket)
        assert outcome(boot) == Done(None)
        wait_until(lambda: not jobs.status().queue.exclusive)
        jobs.workspace_opened(W1)
        target = tmp_path / "written"

        def cancelled_at_the_gate(ctx: JobContext) -> WorkResult:
            def first(event: EngineEvent) -> None:
                if isinstance(event, GateReached):
                    assert jobs.cancel(ctx.job_id) == "cancelled"

            return WorkResult(ctx.call("test.gate", {"path": str(target)}, on_event=first))

        save = jobs.submit(user("save"), cancelled_at_the_gate)
        assert isinstance(save, JobTicket)
        assert code_of(outcome(save)) == ErrorCode.JOB_CANCELLED
        assert not target.exists()
        save = jobs.submit(user("save"), lambda ctx: WorkResult(ctx.call("test.gate", {"path": str(target)})))
        assert isinstance(save, JobTicket)
        assert outcome(save) == Done({"status": "ok"})
        assert target.read_text(encoding="utf-8") == "post"
    finally:
        jobs.close()
        client.close()


class ResetHeld(EngineClient):
    """The real client, whose `reset` holds the call lock until released: the window a reset occupies when it takes
    the lock as a job's final call returns (the verifier's repro, S0058 round 1)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.hold = threading.Event()
        self.entered = threading.Event()

    @override
    def reset(self) -> None:
        with self._call():
            self.entered.set()
            assert self.hold.wait(DEADLINE)
            self._drive(Reset())


@pytest.mark.skipif(os.name != "posix", reason="process groups and flock are POSIX; Windows is BL-0087")
def test_with_the_real_engine_a_retire_during_another_client_call_does_not_wedge_the_queue(tmp_path: Path) -> None:
    """S0058 verifier F1: a user-code job finishing while another public client call is in flight (a reset) made
    `EngineClient.end("retire")` raise on the scheduler thread, and the slot stayed `ending` for the session. Our
    ends never raise; the retire is carried out when the other call has returned."""
    if not (ENGINE_VENV / "bin" / "python").exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    lock = engine_lock_path(tmp_path)
    holder: list[EngineJobs] = []

    def spawned(serial: int) -> None:
        holder[0].engine_spawned(serial)

    def ended(end: EngineEnd) -> None:
        holder[0].engine_ended(end)

    client = ResetHeld(
        EngineCommand.for_venv(ENGINE_VENV, test_ops=True, engine_lock=lock),
        on_engine_spawned=spawned,
        on_engine_ended=ended,
    )
    jobs = EngineJobs(client, wait_engine_lock=lambda: wait_engine_lock(lock), display_delay=DELAY)
    holder.append(jobs)
    try:
        boot = jobs.start()
        assert isinstance(boot, JobTicket)
        assert outcome(boot) == Done(None)
        wait_until(lambda: not jobs.status().queue.exclusive)
        jobs.workspace_opened(W1)
        go = threading.Event()
        pinged = threading.Event()

        def work(ctx: JobContext) -> WorkResult:
            ctx.call("ping", {})
            pinged.set()
            assert go.wait(DEADLINE)
            return WorkResult("v", ran_user_code=True)  # its engine is retired

        ticket = jobs.submit(user("preview"), work)
        assert isinstance(ticket, JobTicket)
        assert pinged.wait(DEADLINE)
        threading.Thread(target=client.reset, daemon=True).start()
        assert client.entered.wait(DEADLINE)
        go.set()  # the job returns while the reset is inside the client's call
        assert outcome(ticket) == Done("v")
        client.hold.set()
        nxt = jobs.submit(user("bql"), calls("ping"))
        assert isinstance(nxt, JobTicket)
        q = jobs.status().queue
        assert outcome(nxt) == Done({"status": "ok"}), f"queue wedged: slot={q.slot} advice={q.advice}"
    finally:
        client.hold.set()
        jobs.close()
        client.close()


@pytest.mark.skipif(os.name != "posix", reason="process groups and flock are POSIX; Windows is BL-0087")
def test_with_the_real_engine_try_again_pressed_while_jobs_run_never_wedges_the_queue(tmp_path: Path) -> None:
    """The verifier's hammer, bounded: Try again pressed over and over while jobs that run user code come and go."""
    jobs, client = real_rig(tmp_path)
    stop = threading.Event()

    def hammer() -> None:
        while not stop.is_set():
            jobs.try_again()

    try:
        boot = jobs.start()
        assert isinstance(boot, JobTicket)
        assert outcome(boot) == Done(None)
        wait_until(lambda: not jobs.status().queue.exclusive)
        jobs.workspace_opened(W1)
        for _ in range(2):
            threading.Thread(target=hammer, daemon=True).start()
        for i in range(40):
            ticket = jobs.submit(user("preview"), calls("ping"))
            assert isinstance(ticket, JobTicket)
            q = jobs.status().queue
            assert outcome(ticket) == Done({"status": "ok"}), f"job {i}: slot={q.slot} advice={q.advice}"
    finally:
        stop.set()
        jobs.close()
        client.close()
