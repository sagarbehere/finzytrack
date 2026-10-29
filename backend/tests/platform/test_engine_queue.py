"""The engine job queue's decisions (`app.platform.engine_jobs.queue`; S0053; `design/specs/engine-jobs.md` §3, §4,
§5.1, §5.2, §12; ADR-0064 and its amendment of 2026-10-28).

Every expected value is written from the spec, not read from the code: the ordering rule (§3: the oldest queued
waited-for job, else the oldest background job; a running job is never interrupted), one engine at a time (§5.1: a new
one only after the previous one has ended, and only once the sidecar has waited out the engine lock; reused only
after parse-only work), the bound and the per-workspace caps (§3), the open-workspace gate (§3, INV-28, INV-31), the
exclusive window of a switch or boot (§3), Cancel and the one rule (§4), the commit-point step (§4), a client that
goes away (§4), and the quit advice on the timer (§5.2, §5.1; model findings N3 and simplification B).

The model's queue properties (`formal/tla/engine-jobs/MAPPING.md` §1.4) are checked after every step of a Hypothesis
walk and of an exhaustive sweep over a finite abstract state space (§14 "the exhaustive (state x event) test").
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Iterable, Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.platform.engine_jobs.queue import (
    EVENTS,
    READS,
    TABLE_VERSION,
    WRITING,
    AdviceTimer,
    AlreadyQueued,
    ArmAdviceTimer,
    Boot,
    Cancel,
    Cancelled,
    ClientGone,
    Effect,
    EndEngine,
    EngineDied,
    EngineEnded,
    EngineLockFree,
    EngineLost,
    EngineNeeded,
    Event,
    ExclusiveBegan,
    ExclusiveEnded,
    Finished,
    Gate,
    HideAdvice,
    Job,
    JobDone,
    JobKind,
    JobSpec,
    Owner,
    Proceed,
    Queued,
    QueuePolicy,
    QueueState,
    Refused,
    RetireEngine,
    Running,
    ShowAdvice,
    Start,
    Step,
    Submit,
    TooLate,
    WaitEngineLock,
    WorkspaceClosed,
    WorkspaceOpened,
    transition,
)

POLICY = QueuePolicy(max_queued=4)
W1 = "ws-1"
W2 = "ws-2"


# -- builders ---------------------------------------------------------------------------------------------------------


def user(kind: JobKind, ws: str | None = W1) -> JobSpec:
    """A job the user waits for; a save, create or copy the user confirmed."""
    return JobSpec(kind=kind, workspace=ws, waited_for=True, confirmed=kind in WRITING, owner="user")


def background(kind: JobKind, ws: str | None = W1) -> JobSpec:
    """Work nobody waits for: R4, and a save the app started itself (prices)."""
    return JobSpec(kind=kind, workspace=ws, waited_for=False, confirmed=False, owner="app")


def switch_job(kind: JobKind, ws: str | None = None) -> JobSpec:
    """A switch's or the boot's own job: its `recover_all`, then the target's `open`."""
    return JobSpec(kind=kind, workspace=ws, waited_for=True, confirmed=False, owner="switch")


def ready(open_ws: str | None = W1) -> QueueState:
    """After the boot: the engine lock waited out, no engine yet, `open_ws` open."""
    return QueueState(engine_lock="free", open_workspace=open_ws)


def run(state: QueueState, *events: Event) -> tuple[QueueState, list[Effect]]:
    effects: list[Effect] = []
    for event in events:
        step = transition(POLICY, state, event)
        state = step.state
        effects.extend(step.effects)
    return state, effects


def submit(state: QueueState, *specs: JobSpec) -> tuple[QueueState, list[int]]:
    """Submit each spec; returns the ids the queue gave them."""
    ids: list[int] = []
    for spec in specs:
        step = transition(POLICY, state, Submit(spec))
        state = step.state
        queued = [e for e in step.effects if isinstance(e, Queued)]
        assert len(queued) == 1, step.effects
        ids.append(queued[0].job_id)
    return state, ids


def started(effects: Iterable[Effect]) -> list[int]:
    return [e.job.id for e in effects if isinstance(e, Start)]


def ends(effects: Iterable[Effect]) -> list[str]:
    return [e.reason for e in effects if isinstance(e, EndEngine)]


def busy_with(spec: JobSpec, state: QueueState | None = None) -> tuple[QueueState, int]:
    """`spec` submitted and started on a fresh engine."""
    state, (jid,) = submit(state or ready(), spec)
    assert state.running is not None
    assert state.running.job.id == jid
    return state, jid


def past_commit(state: QueueState, jid: int) -> QueueState:
    state, effects = run(state, Gate(jid))
    assert effects == [Proceed(jid)]
    return state


# -- boot and the engine lock (§5.1) ----------------------------------------------------------------------------------


def test_the_table_is_versioned_for_the_decision_log() -> None:
    assert TABLE_VERSION >= 1


def test_the_boot_waits_for_the_engine_lock_and_arms_the_advice_timer() -> None:
    step = transition(POLICY, QueueState(), Boot())
    assert step.effects == (WaitEngineLock(), ArmAdviceTimer(step.state.epoch))
    assert step.state.engine_lock == "waiting"


def test_the_boot_opens_the_exclusive_window_so_only_its_own_jobs_start() -> None:
    """§3: "During a switch, a Reopen and the boot, from the moment the current workspace has closed (at boot, from
    the start) ... only its own jobs start"; model `SwitchExclusive` holds from `Init` (`ses.app = "boot"`). A create
    submitted at boot waits for the boot's recover-all, whatever order the shell feeds events in."""
    state, _ = run(QueueState(), Boot())
    assert state.exclusive
    state, (create,) = submit(state, user("create", None))
    state, (recover,) = submit(state, switch_job("recover_all"))
    state, effects = run(state, EngineLockFree())
    assert started(effects) == [recover]
    state, effects = run(state, JobDone(recover, ran_user_code=False), ExclusiveEnded())
    assert started(effects) == [create]


def test_the_boots_own_job_waiting_for_the_engine_lock_is_the_users_to_cancel() -> None:
    """§5.1: "While the sidecar waits, jobs stay queued (the boot's own job can be cancelled, §9.2)"; model
    `SwitchJobBehind` (`~SlotFree`, which holds while an earlier session's engine holds the lock)."""
    state, _ = run(QueueState(), Boot())
    state, (recover,) = submit(state, switch_job("recover_all"))
    state, effects = run(state, Cancel(recover))
    assert effects == [Cancelled(recover)]
    assert state.queued == ()


def test_no_engine_starts_while_the_engine_lock_is_waited_for() -> None:
    state, (jid,) = submit(QueueState(open_workspace=W1), user("preview"))
    assert state.running is None
    state, effects = run(state, EngineLockFree())
    assert started(effects) == [jid]


def test_the_advice_shows_while_the_engine_lock_is_waited_for_with_or_without_a_job() -> None:
    for specs in ((), (user("preview"),)):
        state, _ = run(QueueState(open_workspace=W1), Boot())
        state, _ = submit(state, *specs)
        state, effects = run(state, AdviceTimer(state.epoch))
        assert effects == [ShowAdvice()]
        assert state.advice
        state, effects = run(state, EngineLockFree())
        assert HideAdvice() in effects
        assert not state.advice


def test_a_stale_advice_timer_shows_nothing() -> None:
    state, _ = run(QueueState(), Boot())
    old = state.epoch
    state, _ = run(state, EngineLockFree())
    state, effects = run(state, AdviceTimer(old))
    assert effects == []
    assert not state.advice


# -- dispatch (§3, §5.1) ----------------------------------------------------------------------------------------------


def test_a_job_starts_at_once_on_a_fresh_engine_when_nothing_runs() -> None:
    state, effects = run(ready(), Submit(user("preview")))
    assert [type(e) for e in effects] == [Queued, Start]
    start = effects[1]
    assert isinstance(start, Start)
    assert not start.reuse
    assert state.slot == "busy"


def test_a_running_job_is_never_interrupted_to_start_another() -> None:
    state, _ = busy_with(background("r4"))
    state, effects = run(state, Submit(user("preview")), Submit(user("save")))
    assert started(effects) == []
    assert state.running is not None
    assert state.running.job.kind == "r4"


def test_a_waited_for_job_goes_ahead_of_an_older_background_job() -> None:
    state, running = busy_with(user("bql"))
    state, (bg, fg1, fg2) = submit(state, background("r4"), user("preview"), user("save"))
    state, effects = run(state, JobDone(running, ran_user_code=False))
    assert started(effects) == [fg1]
    state, effects = run(state, JobDone(fg1, ran_user_code=False))
    assert started(effects) == [fg2]
    state, effects = run(state, JobDone(fg2, ran_user_code=False))
    assert started(effects) == [bg]


def test_background_jobs_run_oldest_first_when_nobody_waits() -> None:
    state, running = busy_with(user("bql"))
    state, (r4, bg_save) = submit(state, background("r4"), background("save", W1))
    state, effects = run(state, JobDone(running, ran_user_code=False))
    assert started(effects) == [r4]
    state, effects = run(state, JobDone(r4, ran_user_code=False))
    assert started(effects) == [bg_save]


def test_an_engine_that_ran_user_code_is_retired_and_the_next_job_waits_for_its_end() -> None:
    state, first = busy_with(user("preview"))
    state, (second,) = submit(state, user("bql"))
    state, effects = run(state, JobDone(first, ran_user_code=True))
    assert ends(effects) == ["retire"]
    assert started(effects) == []
    assert state.slot == "ending"
    state, effects = run(state, EngineEnded())
    assert [(e.job.id, e.reuse) for e in effects if isinstance(e, Start)] == [(second, False)]


def test_an_engine_that_ran_only_parse_only_work_serves_the_next_job() -> None:
    state, first = busy_with(switch_job("recover_all"), QueueState(engine_lock="free", exclusive=True))
    state, (second,) = submit(state, switch_job("open", W2))
    state, effects = run(state, JobDone(first, ran_user_code=False))
    assert ends(effects) == []
    assert [(e.job.id, e.reuse) for e in effects if isinstance(e, Start)] == [(second, True)]


def test_an_idle_engine_that_dies_is_drained_and_never_reused() -> None:
    """§5.3: a crash is noticed by the engine's exit, also between jobs; §5.2: its group is drained before the next
    engine; "Queued jobs stay queued and run on the next engine". The shell reports the death with the
    number the engine's `Start` carried (S0053 verifier F4, R2-F1)."""
    state, effects = run(ready(), Submit(user("preview")))
    (start,) = [e for e in effects if isinstance(e, Start)]
    state, _ = run(state, JobDone(start.job.id, ran_user_code=False))
    assert state.slot == "idle"
    state, effects = run(state, EngineDied(start.engine))
    assert ends(effects) == ["crashed"]
    assert state.slot == "ending"
    state, (second,) = submit(state, user("bql"))
    assert state.running is None, "a job started on an engine that died"
    state, effects = run(state, EngineEnded())
    starts = [e for e in effects if isinstance(e, Start)]
    assert [(e.job.id, e.reuse) for e in starts] == [(second, False)]
    assert starts[0].engine != start.engine, "a fresh engine took the dead engine's number"


def test_each_spawn_gets_a_new_number_and_a_reused_engine_keeps_its_own() -> None:
    """§5.3 (S0053 verifier R2-F1): the number names one engine for its whole life; the shell reports a death by it.
    A reuse keeps it; every spawn, also mid-job after a retire, gets one never used before."""
    state, effects = run(ready(), Submit(user("preview")))
    (first,) = [e for e in effects if isinstance(e, Start)]
    state, effects = run(state, JobDone(first.job.id, ran_user_code=False), Submit(user("bql")))
    (reused,) = [e for e in effects if isinstance(e, Start)]
    assert reused.reuse
    assert reused.engine == first.engine
    state, effects = run(state, JobDone(reused.job.id, ran_user_code=True), EngineEnded(), Submit(user("save")))
    (save,) = [e for e in effects if isinstance(e, Start)]
    state, effects = run(state, RetireEngine(save.job.id), EngineNeeded(save.job.id), EngineEnded())
    (again,) = [e for e in effects if isinstance(e, Start)]
    assert again.job.id == save.job.id
    assert not save.reuse
    assert not again.reuse
    numbers = [first.engine, save.engine, again.engine]
    assert len(set(numbers)) == 3, numbers
    assert state.engine == again.engine


def test_a_death_noticed_before_a_reuse_but_fed_after_it_ends_the_reused_engine() -> None:
    """§5.3, §5.2 (S0053 verifier R2-F1): the idle engine exits; before the shell feeds `EngineDied`, a Submit starts
    the next job on it (`reuse`). The report names that engine, which is still in the slot: its job is lost and the
    group drained, so the queue does not stall behind a job "running" on a dead engine (a background R4 has no
    Cancel)."""
    state, effects = run(ready(), Submit(user("preview")))
    (first,) = [e for e in effects if isinstance(e, Start)]
    state, _ = run(state, JobDone(first.job.id, ran_user_code=False))
    state, effects = run(state, Submit(background("r4")))
    (reuse,) = [e for e in effects if isinstance(e, Start)]
    assert reuse.reuse
    state, (waiting,) = submit(state, user("bql"))
    state, effects = run(state, EngineDied(first.engine))
    assert EngineLost(reuse.job.id) in effects
    assert ends(effects) == ["crashed"]
    assert state.running is None
    state, effects = run(state, EngineEnded())
    assert [(e.job.id, e.reuse) for e in effects if isinstance(e, Start)] == [(waiting, False)]


def test_a_death_reported_for_an_earlier_engine_leaves_the_current_one_alone() -> None:
    """The `EngineDied` contract: `engine` is the number of the engine that died; a report naming an earlier engine
    (its end was ours and drained, §5.2) changes nothing, whether the current engine is busy or idle, also when the
    current engine was reused."""
    state, first = busy_with(user("preview"))
    earlier = state.engine
    assert earlier is not None
    state, _ = run(state, Cancel(first), EngineEnded())
    state, second = busy_with(user("bql"), state)
    assert state.engine != earlier
    state, effects = run(state, EngineDied(earlier))
    assert effects == []
    assert state.slot == "busy"
    state, _ = run(state, JobDone(second, ran_user_code=False))
    state, effects = run(state, EngineDied(earlier))
    assert effects == []
    assert state.slot == "idle"
    state, effects = run(state, Submit(user("preview")))
    assert [e.reuse for e in effects if isinstance(e, Start)] == [True]
    state, effects = run(state, EngineDied(earlier))
    assert effects == []
    assert state.slot == "busy"


def test_an_engine_that_died_is_drained_before_the_next_one_starts() -> None:
    state, first = busy_with(user("preview"))
    state, (second,) = submit(state, user("bql"))
    assert state.engine is not None
    state, effects = run(state, EngineDied(state.engine))
    assert EngineLost(first) in effects
    assert ends(effects) == ["crashed"]
    assert started(effects) == []
    state, effects = run(state, EngineEnded())
    assert started(effects) == [second]


def test_a_job_that_needs_a_fresh_engine_mid_job_gets_one_only_after_the_old_one_ended() -> None:
    """§5.1 and §6.1: a save runs its owed R4 in its own engines, then the save in a fresh one (`FreshEnd`, then
    `SpawnStep`)."""
    state, jid = busy_with(user("save"))
    state, effects = run(state, RetireEngine(jid))
    assert ends(effects) == ["retire"]
    state, effects = run(state, EngineNeeded(jid))
    assert started(effects) == []
    state, effects = run(state, EngineEnded())
    assert [(e.job.id, e.reuse) for e in effects if isinstance(e, Start)] == [(jid, False)]


def test_an_ask_for_a_fresh_engine_made_before_the_old_one_ended_is_kept() -> None:
    state, jid = busy_with(user("save"))
    state, effects = run(state, EngineNeeded(jid), RetireEngine(jid))
    assert started(effects) == []
    state, effects = run(state, EngineEnded())
    assert started(effects) == [jid]


def test_a_retire_signals_the_engine_once_and_never_after_it_ended() -> None:
    """§5.2: "After the reap no signal is sent to that group: its id may already belong to another program"; model
    `FreshEnd` needs `EngOK` (the job's engine busy). A second `RetireEngine`, while the first end drains or after
    it, signals nothing and does not hold the slot."""
    state, jid = busy_with(user("save"))
    state, effects = run(state, RetireEngine(jid))
    assert ends(effects) == ["retire"]
    state, effects = run(state, RetireEngine(jid))
    assert ends(effects) == [], "a second signal to a group already being ended"
    state, _ = run(state, EngineEnded())
    assert state.slot == "none"
    state, effects = run(state, RetireEngine(jid))
    assert ends(effects) == [], "a signal sent to a group after its reap"
    assert state.slot == "none"
    state, effects = run(state, EngineNeeded(jid))
    assert started(effects) == [jid]


def test_a_job_between_engines_gets_one_only_when_it_asks() -> None:
    """§5.1: the job decides when its next part needs an engine; the slot being free is not enough."""
    state, jid = busy_with(user("save"))
    state, _ = run(state, RetireEngine(jid))
    state, effects = run(state, EngineEnded())
    assert started(effects) == []
    state, effects = run(state, EngineNeeded(jid))
    assert started(effects) == [jid]


# -- the open-workspace gate, the bound, the caps (§3) ----------------------------------------------------------------


def test_a_job_for_a_workspace_that_is_not_open_is_never_queued() -> None:
    state, effects = run(ready(W1), Submit(user("save", W2)))
    assert effects == [Refused("WORKSPACE_NOT_OPEN")]
    assert state.queued == ()
    assert state.running is None
    state, effects = run(ready(None), Submit(user("preview", W1)))
    assert effects == [Refused("WORKSPACE_NOT_OPEN")]


def test_app_scoped_jobs_need_no_open_workspace() -> None:
    _, effects = run(ready(None), Submit(user("create", None)))
    assert [type(e) for e in effects] == [Queued, Start]


def test_a_request_beyond_max_queued_is_refused_and_not_queued() -> None:
    state, _ = busy_with(user("save"))
    state, _ = submit(state, *(user("preview") for _ in range(POLICY.max_queued)))
    before = state
    state, effects = run(state, Submit(user("bql")))
    assert effects == [Refused("ENGINE_QUEUE_FULL")]
    assert state == before


def test_at_most_one_queued_r4_and_one_queued_background_save_per_workspace() -> None:
    state, _ = busy_with(user("bql"))
    state, (r4, bg) = submit(state, background("r4"), background("save"))
    state, effects = run(state, Submit(background("r4")), Submit(background("save")))
    assert effects == [AlreadyQueued(r4), AlreadyQueued(bg)]
    assert len(state.queued) == 2
    state, _ = run(state, WorkspaceOpened(W2))
    state, _ = submit(state, background("r4", W2))  # another workspace's is its own


def test_a_user_save_is_never_capped_as_a_background_save() -> None:
    state, _ = busy_with(user("bql"))
    state, _ = submit(state, background("save"), user("save"), user("save"))
    assert len(state.queued) == 3


# -- the exclusive window of a switch, a Reopen and the boot (§3) -----------------------------------------------------


def test_during_a_switch_only_its_own_jobs_start_and_a_create_starts_after_it() -> None:
    state, _ = run(ready(None), ExclusiveBegan())
    state, (create,) = submit(state, user("create", None))
    assert state.running is None
    state, (recover,) = submit(state, switch_job("recover_all"))
    assert state.running is not None
    assert state.running.job.id == recover
    state, (open_,) = submit(state, switch_job("open", W2))
    state, effects = run(state, JobDone(recover, ran_user_code=False))
    assert started(effects) == [open_]
    state, effects = run(state, JobDone(open_, ran_user_code=True), EngineEnded())
    assert started(effects) == []
    state, effects = run(state, WorkspaceOpened(W2), ExclusiveEnded())
    assert started(effects) == [create]


def test_a_switch_job_never_starts_outside_its_window() -> None:
    _, effects = run(ready(W1), Submit(switch_job("recover_all")))
    assert started(effects) == []


def test_a_switch_job_with_nothing_ahead_of_it_is_not_the_users_to_cancel() -> None:
    """§9.1 step 3, §9.2 (model `CancelableQ`): the switch's own queued job is the user's to cancel only while it
    waits behind other work (a running job, a process not ended, the engine lock)."""
    state, (jid,) = submit(ready(W1), switch_job("recover_all"))
    state, effects = run(state, Cancel(jid))
    assert effects == []
    assert [j.id for j in state.queued] == [jid]


def test_a_closed_workspaces_queued_jobs_do_not_start_while_it_is_closed() -> None:
    """§3, INV-31: no job of a workspace that is not open starts. Dropping them at the close is the quiesce's
    (§9.1 step 2, BL-0154), which this table does not do yet: they stay queued (and count against `max_queued`)."""
    state, running = busy_with(user("bql"))
    state, (queued,) = submit(state, user("preview"))
    state, effects = run(state, WorkspaceClosed(), JobDone(running, ran_user_code=False))
    assert started(effects) == []
    assert [j.id for j in state.queued] == [queued]


def test_jobs_queued_before_a_close_start_after_a_reopen_until_the_quiesce_drops_them() -> None:
    """Pinned current behaviour, TODO(BL-0154): §9.1 step 2's quiesce drops a closed workspace's queued reads (their
    requests get `WORKSPACE_NOT_OPEN {switching}`) before the close; until it is built a read queued before the
    close starts after a Reopen of that workspace. BL-0154 inverts this test (S0053 verifier F6)."""
    state, running = busy_with(user("bql"))
    state, (queued,) = submit(state, user("preview"))
    state, _ = run(state, WorkspaceClosed(), JobDone(running, ran_user_code=False))
    state, effects = run(state, WorkspaceOpened(W1))
    assert started(effects) == [queued]


# -- Cancel (§4) ------------------------------------------------------------------------------------------------------


def test_cancel_of_a_queued_job_removes_it_and_nothing_ran() -> None:
    state, running = busy_with(user("bql"))
    state, (jid,) = submit(state, user("preview"))
    state, effects = run(state, Cancel(jid))
    assert effects == [Cancelled(jid)]
    assert all(j.id != jid for j in state.queued)
    assert state.running is not None
    assert state.running.job.id == running, "the running read is its own requester's to cancel"


def test_cancel_of_a_running_read_ends_its_engine_with_reason_cancel() -> None:
    state, jid = busy_with(user("preview"))
    state, effects = run(state, Cancel(jid))
    assert effects == [EndEngine("cancel"), ArmAdviceTimer(state.epoch), Cancelled(jid)]
    assert state.running is None
    assert state.slot == "ending"


def test_three_cancels_in_a_row_are_three_cancel_ends_never_a_crash() -> None:
    """§14 Cancel: the reason is recorded before the signal, so the supervision never counts these as stops."""
    state = ready()
    reasons: list[str] = []
    for _ in range(3):
        state, jid = busy_with(user("bql"), state)
        state, effects = run(state, Cancel(jid), EngineEnded())
        reasons += ends(effects)
    assert reasons == ["cancel", "cancel", "cancel"]


def test_a_save_cancelled_before_its_commit_point_ends_and_the_gate_after_it_is_ignored() -> None:
    """§4: the commit point is one table step. A Cancel processed before the `gate` wins: the engine is ended, the
    outcome is `cancelled`, and the gate the dying engine may still send changes nothing."""
    state, jid = busy_with(user("save"))
    state, effects = run(state, Cancel(jid))
    assert effects == [EndEngine("cancel"), ArmAdviceTimer(state.epoch), Cancelled(jid)]
    state, effects = run(state, Gate(jid))
    assert effects == []
    assert Proceed(jid) not in effects


def test_a_cancel_after_proceed_is_too_late_and_the_save_finishes() -> None:
    for kind in sorted(WRITING):
        state, jid = busy_with(user(kind, W1 if kind == "save" else None))
        state = past_commit(state, jid)
        state, effects = run(state, Cancel(jid))
        assert effects == [TooLate(jid)]
        assert state.running is not None
        assert state.running.past_commit
        assert state.slot == "busy"
        state, effects = run(state, JobDone(jid, ran_user_code=True))
        assert Finished(jid) in effects


def test_the_gate_is_answered_only_for_a_running_writing_job_before_its_commit_point() -> None:
    state, jid = busy_with(user("preview"))
    assert run(state, Gate(jid))[1] == []
    state, jid = busy_with(user("save"))
    state = past_commit(state, jid)
    assert run(state, Gate(jid))[1] == []
    assert run(state, Gate(jid + 100))[1] == []


def test_a_gate_from_an_engine_being_ended_changes_nothing() -> None:
    """§4: the commit point is decided only for the engine the job runs on; a `gate` from one we are ending must not
    put the job past its commit point, or the user's Cancel would turn into "too late" for work not committed."""
    state, jid = busy_with(user("save"))
    state, _ = run(state, RetireEngine(jid))
    state, effects = run(state, Gate(jid))
    assert effects == []
    assert state.running is not None
    assert not state.running.past_commit


def test_the_advice_stays_while_the_slot_is_still_held_when_the_lock_frees() -> None:
    """§5.2: the advice stays while a signalled process holds the slot, whatever else changes."""
    state = QueueState(slot="ending", engine_lock="waiting", advice=True, epoch=3)
    state, effects = run(state, EngineLockFree())
    assert state.advice
    assert HideAdvice() not in effects


def test_recover_all_between_engines_has_no_cancel() -> None:
    """Model `UserCancelRunning`: `RJ.k = "recover" => eng.st = "busy"`: recover-all waiting for a new engine holds
    none, so there is nothing to cancel (a stuck slot gets the advice on the timer, §5.2). §8.1's Cancel per
    journal ends the engine of the journal in hand (BL-0151)."""
    state, jid = busy_with(switch_job("recover_all"), QueueState(engine_lock="free", exclusive=True))
    state, _ = run(state, RetireEngine(jid), EngineNeeded(jid))
    assert state.slot == "ending"
    state, effects = run(state, Cancel(jid))
    assert effects == []
    assert state.running is not None
    assert state.running.job.id == jid
    state, effects = run(state, EngineEnded())
    assert started(effects) == [jid]


def test_a_save_between_engines_is_its_requesters_to_cancel_and_nothing_more_is_signalled() -> None:
    """§4: a running job before its commit point is its requester's to cancel, also while it waits for its next
    engine; model `UserCancelRunning` (`eng.st = "busy" \\/ ctl.pc \\in SpawnPcs`) and `CancelEng` (a job between two
    engines holds none, so no second signal)."""
    state, jid = busy_with(user("save"))
    state, _ = run(state, RetireEngine(jid), EngineNeeded(jid))
    assert state.slot == "ending"
    state, effects = run(state, Cancel(jid))
    assert effects == [Cancelled(jid)]
    assert state.running is None
    state, effects = run(state, EngineEnded())
    assert started(effects) == [], "the cancelled save got its next engine"


def test_a_background_job_has_no_cancel_of_its_own() -> None:
    state, jid = busy_with(background("r4"))
    state, effects = run(state, Cancel(jid))
    assert effects == []
    assert state.running is not None


# -- the one rule (§4; model `Endable`, `CancelReachIsTheOneRule`) ---------------------------------------------------


def waiting_behind(spec: JobSpec, *, past: bool = False, gone: bool = False) -> tuple[QueueState, int, int]:
    """`spec` runs (past its commit point if `past`, its requester gone if `gone`); a user's preview waits."""
    state, running = busy_with(spec)
    if past:
        state = past_commit(state, running)
    if gone:
        state, _ = run(state, ClientGone(running))
    state, (waiting,) = submit(state, user("preview"))
    return state, running, waiting


@pytest.mark.parametrize(
    ("spec", "past", "gone"),
    [
        (background("r4"), False, False),
        (background("save"), False, False),
        (user("preview"), False, True),
        (user("rebuild"), False, True),
        (user("bql"), False, True),
        (user("open"), False, True),
    ],
    ids=["hung-r4", "hung-bg-save-before-commit", "preview-tab-closed", "rebuild-gone", "bql-gone", "open-gone"],
)
def test_a_waiting_users_cancel_frees_the_engine_from_work_nobody_waits_for(
    spec: JobSpec, *, past: bool, gone: bool
) -> None:
    state, running, waiting = waiting_behind(spec, past=past, gone=gone)
    if gone and spec.kind in READS:  # a client that goes away already ended its running read (§4)
        assert state.running is None
        return
    state, effects = run(state, Cancel(waiting))
    assert EndEngine("cancel") in effects
    assert Cancelled(running) in effects
    assert Cancelled(waiting) in effects
    assert state.running is None


@pytest.mark.parametrize(
    ("spec", "past", "gone"),
    [
        (background("save"), True, False),
        (user("save"), True, False),
        (user("save"), False, True),
        (user("save"), False, False),
        (user("create", None), False, False),
        (user("create", None), False, True),
        (user("copy", None), False, False),
        (user("copy", None), True, True),
    ],
    ids=[
        "bg-save-past-commit",
        "user-save-past-commit",
        "confirmed-save-tab-closed",
        "confirmed-save",
        "create",
        "create-gone",
        "copy",
        "copy-past-gone",
    ],
)
def test_a_waiting_users_cancel_cannot_end_confirmed_or_committed_work(
    spec: JobSpec, *, past: bool, gone: bool
) -> None:
    state, running, waiting = waiting_behind(spec, past=past, gone=gone)
    state, effects = run(state, Cancel(waiting))
    assert effects == [Cancelled(waiting, cannot_stop_ahead=True)]
    assert state.running is not None
    assert state.running.job.id == running
    assert state.slot == "busy"
    state, effects = run(state, AdviceTimer(state.epoch))
    assert effects == [], "no quit advice on a timer for work that may only be slow"


@pytest.mark.parametrize("kind", ["preview", "rebuild", "bql", "open"])
def test_a_read_whose_requester_is_present_is_that_requesters_to_cancel(kind: JobKind) -> None:
    state, running, waiting = waiting_behind(user(kind))
    state, effects = run(state, Cancel(waiting))
    assert effects == [Cancelled(waiting)]
    assert state.running is not None
    assert state.running.job.id == running


def test_recover_all_is_never_ended_by_another_jobs_cancel() -> None:
    state, recover = busy_with(switch_job("recover_all"), QueueState(engine_lock="free", exclusive=True))
    state, (open_,) = submit(state, switch_job("open", W2))
    state, effects = run(state, Cancel(open_))
    assert effects == [Cancelled(open_)]
    assert state.running is not None
    assert state.running.job.id == recover


def test_recover_all_whose_requester_is_gone_is_not_the_waiting_users_to_cancel() -> None:
    """§4, model `UserCancelQueued`: the waiting user is spared the "cannot be stopped" answer only when the running
    job is a read, an open or a recover-all *whose own requester is present* (`RJ.req = "present"`). Today no event
    takes a switch job's requester away (`ClientGone` ignores it); the rule is the model's, for every state."""
    gone = Job(5, "recover_all", None, True, False, "switch", False)
    state = QueueState(
        queued=(Job(6, "create", None, True, True, "user", True),),
        running=Running(gone),
        slot="busy",
        engine_lock="free",
        exclusive=True,
        next_id=7,
    )
    state, effects = run(state, Cancel(6))
    assert effects == [Cancelled(6, cannot_stop_ahead=True)]


def test_a_switch_job_waiting_for_a_signalled_process_to_end_is_the_users_to_cancel() -> None:
    """§9.1 step 3: the switch's own job is the user's to cancel "while ... still queued behind other work (a create
    or copy started earlier, or a process that has not ended)"; model `SwitchJobBehind` (`~SlotFree`)."""
    state, jid = busy_with(user("preview"))
    state, _ = run(state, Cancel(jid), WorkspaceClosed(), ExclusiveBegan())
    assert state.slot == "ending"
    assert state.running is None
    state, (recover,) = submit(state, switch_job("recover_all"))
    state, effects = run(state, Cancel(recover))
    assert effects == [Cancelled(recover)]
    assert state.queued == ()


def test_a_switch_job_queued_behind_other_work_is_the_users_to_cancel() -> None:
    """§9.1 step 3, §9.2 (fidelity review 2, M4)."""
    state, create = busy_with(user("create", None), ready(None))
    state, _ = run(state, ExclusiveBegan())
    state, (recover,) = submit(state, switch_job("recover_all"))
    state, effects = run(state, Cancel(recover))
    assert effects == [Cancelled(recover, cannot_stop_ahead=True)]
    assert state.running is not None
    assert state.running.job.id == create


# -- a client that goes away (§4) -------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(READS))
def test_a_client_that_goes_away_cancels_its_read(kind: JobKind) -> None:
    state, jid = busy_with(user(kind))
    state, effects = run(state, ClientGone(jid))
    assert EndEngine("cancel") in effects
    assert Cancelled(jid) in effects
    state, _ = busy_with(user("bql"))
    state, (queued,) = submit(state, user(kind))
    state, effects = run(state, ClientGone(queued))
    assert effects == [Cancelled(queued)]
    assert state.queued == ()


def test_the_switchs_open_loses_its_requester_when_its_window_closes() -> None:
    """§4: another job's Cancel may end "a preview, rebuild, BQL or open build whose requester has gone (its request
    aborted or its window closed)"; model `UserCloseTab` (`RJ.own \\in {"user","app"} \\/ RJ.k = "open"`)."""
    state = QueueState(engine_lock="free", exclusive=True)
    state, open_ = busy_with(switch_job("open", W2), state)
    state, effects = run(state, ClientGone(open_))
    assert effects == []
    assert state.running is not None
    assert not state.running.job.requester_present


def test_a_switch_jobs_queued_job_has_no_tab_to_close() -> None:
    """Model `UserCloseTab`: a queued job loses its requester only when it is the user's or the app's
    (`q[i].own \\in {"user","app"}`); the switch's own queued job is ended by its Cancel or the switch (§9.1)."""
    state, _ = busy_with(user("create", None), ready(None))
    state, _ = run(state, ExclusiveBegan())
    state, (open_,) = submit(state, switch_job("open", W2))
    before = state
    state, effects = run(state, ClientGone(open_))
    assert effects == []
    assert state == before


@pytest.mark.parametrize("kind", ["save", "create", "copy"])
def test_a_client_that_goes_away_never_cancels_a_confirmed_job(kind: JobKind) -> None:
    ws = W1 if kind == "save" else None
    state, jid = busy_with(user(kind, ws))
    state, effects = run(state, ClientGone(jid))
    assert effects == []
    assert state.running is not None
    assert not state.running.job.requester_present
    state, _ = busy_with(user("bql"), ready())
    state, (queued,) = submit(state, user(kind, ws))
    state, effects = run(state, ClientGone(queued))
    assert effects == []
    assert [j.id for j in state.queued] == [queued]


# -- the advice on the timer (§5.2, N3) -------------------------------------------------------------------------------


def test_a_signalled_process_that_has_not_ended_gets_the_advice_with_or_without_a_waiting_job() -> None:
    for wait in (False, True):
        state, jid = busy_with(user("preview"))
        if wait:
            state, _ = submit(state, user("bql"))
        state, effects = run(state, Cancel(jid))
        timers = [e for e in effects if isinstance(e, ArmAdviceTimer)]
        assert timers == [ArmAdviceTimer(state.epoch)]
        state, effects = run(state, AdviceTimer(state.epoch))
        assert effects == [ShowAdvice()]
        state, effects = run(state, EngineEnded())
        assert effects[0] == HideAdvice()


def test_a_retire_whose_drain_never_finishes_gets_the_advice() -> None:
    state, jid = busy_with(user("preview"))
    state, _ = submit(state, user("bql"))
    state, _ = run(state, JobDone(jid, ran_user_code=True))
    state, effects = run(state, AdviceTimer(state.epoch))
    assert effects == [ShowAdvice()]


def test_an_old_timer_does_not_show_the_advice_for_a_later_process() -> None:
    state, jid = busy_with(user("preview"))
    state, _ = run(state, Cancel(jid))
    old = state.epoch
    state, _ = run(state, EngineEnded())
    state, jid = busy_with(user("bql"), state)
    state, _ = run(state, Cancel(jid))
    state, effects = run(state, AdviceTimer(old))
    assert effects == []


# -- the model's properties, after every step (MAPPING.md §1.4) ------------------------------------------------------

KINDS: tuple[JobKind, ...] = ("open", "rebuild", "preview", "save", "r4", "bql", "recover_all", "create", "copy")


def eligible(state: QueueState, job: Job) -> bool:
    """§3, written from the spec: during a switch's or the boot's window only its own jobs; otherwise jobs of the
    open workspace and app-scoped ones."""
    if state.exclusive:
        return job.owner == "switch"
    return job.owner != "switch" and (job.workspace is None or job.workspace == state.open_workspace)


def endable(r: Running) -> bool:
    """§4's one rule, written from the spec: nobody waits for it, the user did not confirm it, and it has not reached
    its commit point."""
    j = r.job
    if j.kind == "r4":
        return True
    if j.kind == "save":
        return not j.confirmed and not r.past_commit
    return j.kind in (*READS, "open") and not j.requester_present


def slot_at_dispatch(before: QueueState, event: Event) -> str:
    """The engine slot after the event's own work and before any dispatch, written from §5.1 and §5.2: an ended
    engine frees it; a job that ran user code retires its engine; parse-only work leaves it idle for the next job;
    our end, a retire or a death leaves it ending until the group is drained. A death is the engine's in the slot
    only when it names that engine's number (§5.3)."""
    r = before.running
    if isinstance(event, EngineDied) and before.slot in ("busy", "idle") and event.engine == before.engine:
        return "ending"
    if isinstance(event, EngineEnded) and before.slot == "ending":
        return "none"
    if isinstance(event, JobDone) and r is not None and r.job.id == event.job_id and before.slot == "busy":
        return "ending" if event.ran_user_code else "idle"
    return before.slot


def check_step(policy: QueuePolicy, before: QueueState, event: Event, step: Step) -> None:
    after, effects = step.state, step.effects
    starts = [e for e in effects if isinstance(e, Start)]
    cancelled = {e.job_id for e in effects if isinstance(e, Cancelled)}
    # AtMostOneJobRunning, AtMostOneEngine (also across a restart: none before the engine lock was waited out)
    assert len(starts) <= 1
    if starts:
        s = starts[0]
        assert after.engine_lock == "free"
        slot = slot_at_dispatch(before, event)
        assert slot in ("none", "idle"), f"an engine started while the slot was {slot}"
        assert s.reuse == (slot == "idle")
        # §5.3: a reused engine keeps its number; a fresh one takes a number never used before
        if s.reuse:
            assert s.engine == before.engine
        else:
            assert s.engine == before.next_engine
            assert after.next_engine == before.next_engine + 1
        assert after.engine == s.engine
        assert after.slot == "busy"
        assert after.running is not None
        assert after.running.job.id == s.job.id
        mid_job = before.running is not None and before.running.job.id == s.job.id
        if not mid_job:
            # WaitedForFirst: the oldest eligible waited-for job, else the oldest eligible job
            assert before.running is None or (
                before.running.job.id in cancelled or isinstance(event, (JobDone, EngineDied))
            )
            pool = [j for j in _queue_seen(before, effects) if j.id not in cancelled and eligible(after, j)]
            fg = [j for j in pool if j.waited_for]
            assert s.job.id == (fg[0] if fg else pool[0]).id
    if after.running is None:
        assert after.slot != "busy"
    # `engine` names the engine in the slot, and only while there is one; numbers are never given twice
    assert (after.engine is None) == (after.slot == "none")
    assert after.engine is None or after.engine < after.next_engine
    if not starts:
        assert after.next_engine == before.next_engine
        assert after.engine in (before.engine, None)
    assert len(after.queued) <= policy.max_queued
    # NoOwnEndAfterCommitPoint: our Cancel never ends a writing job past its commit point
    if any(e == EndEngine("cancel") for e in effects):
        assert before.running is not None
        assert not before.running.past_commit
        # CancelReachIsTheOneRule
        if isinstance(event, Cancel) and event.job_id != before.running.job.id:
            assert endable(before.running)
            assert before.running.job.kind != "recover_all"
    if any(isinstance(e, Proceed) for e in effects):
        assert before.running is not None
        assert before.running.job.kind in WRITING
        assert not before.running.past_commit
        assert after.running is not None
        assert after.running.past_commit
    # §4: the waiting user's own Cancel, when the job ahead is not ended: "cannot be stopped from here" unless the job
    # ahead is a read, an open or a recover-all its own requester still waits for (theirs to cancel), or none runs
    if isinstance(event, Cancel) and Cancelled(event.job_id) not in effects:
        mine = [e for e in effects if isinstance(e, Cancelled) and e.job_id == event.job_id]
        r = before.running
        if mine and (r is None or Cancelled(r.job.id) not in effects):
            theirs = r is not None and r.job.kind in (*READS, "open", "recover_all") and r.job.requester_present
            assert mine[0].cannot_stop_ahead == (r is not None and not theirs)
    # NoAdviceOnTimerForSlowWork
    if any(isinstance(e, ShowAdvice) for e in effects) or after.advice:
        assert after.slot == "ending" or after.engine_lock == "waiting"
    # a waited-for job that leaves the queue without starting is answered (EveryWaitedForJobEnds, safety half)
    left = {j.id for j in before.queued} - {j.id for j in after.queued} - {s.job.id for s in starts}
    assert left <= cancelled


def _queue_seen(before: QueueState, effects: Sequence[Effect]) -> list[Job]:
    """The queue the dispatch picked from: the queue before the event plus a job the event queued."""
    jobs = list(before.queued)
    for e in effects:
        if isinstance(e, Queued):
            jobs.append(e.job)
    return jobs


def spec_of(kind: JobKind, ws: str, fg: bool, owner: Owner) -> JobSpec:
    """A plausible job: app-scoped kinds have no workspace; R4 is background; a save is background or confirmed."""
    return JobSpec(
        kind=kind,
        workspace=None if kind in ("create", "copy", "recover_all") else ws,
        waited_for=kind != "r4" and (fg or kind != "save"),
        confirmed=kind in WRITING and fg and owner == "user",
        owner="switch" if kind == "recover_all" else ("app" if kind == "r4" else owner),
    )


OWNERS: tuple[Owner, ...] = ("user", "app")
SPECS = st.builds(spec_of, st.sampled_from(KINDS), st.sampled_from([W1, W2]), st.booleans(), st.sampled_from(OWNERS))


def events_for(state: QueueState) -> st.SearchStrategy[Event]:
    ids = sorted({j.id for j in state.queued} | ({state.running.job.id} if state.running else set[int]()) | {999})
    jid = st.sampled_from(ids)
    return st.one_of(
        SPECS.map(Submit),
        jid.map(Cancel),
        jid.map(ClientGone),
        jid.map(Gate),
        st.builds(JobDone, jid, st.booleans()),
        st.builds(EngineDied, st.integers(min_value=0, max_value=state.next_engine)),
        jid.map(EngineNeeded),
        jid.map(RetireEngine),
        st.just(EngineEnded()),
        st.just(EngineLockFree()),
        st.sampled_from([state.epoch, state.epoch - 1]).map(AdviceTimer),
        st.sampled_from([W1, W2]).map(WorkspaceOpened),
        st.just(WorkspaceClosed()),
        st.just(ExclusiveBegan()),
        st.just(ExclusiveEnded()),
    )


@settings(max_examples=400, deadline=None)
@given(st.data())
def test_the_models_queue_properties_hold_after_every_step_of_a_walk(data: st.DataObject) -> None:
    state, _ = run(QueueState(open_workspace=W1), Boot())
    for _ in range(40):
        event = data.draw(events_for(state))
        step = transition(POLICY, state, event)
        check_step(POLICY, state, event, step)
        state = step.state


def job_variants(jid: int) -> Iterable[Job]:
    """Every kind as the user's job (confirmed when it writes), a background R4 and save, and the switch's own jobs;
    each with its requester present or gone."""
    for present in (True, False):
        for kind in KINDS:
            ws = None if kind in ("create", "copy", "recover_all") else W1
            owner = "switch" if kind == "recover_all" else "user"
            yield Job(jid, kind, ws, kind != "r4", kind in WRITING, owner, present)
        yield Job(jid, "save", W1, False, False, "app", present)
        yield Job(jid, "open", W2, True, False, "switch", present)


def abstract_states() -> Iterable[QueueState]:
    """A finite abstract state space: every slot, lock and exclusive value; no running job, or one of every variant,
    before or past its commit point, holding its engine or waiting for a new one; no queued job, or one."""
    runnings: list[Running | None] = [None]
    for job in job_variants(5):
        for past, wants in itertools.product((False, True), (False, True)):
            if past and job.kind not in WRITING:
                continue
            runnings.append(Running(job, past_commit=past, wants_engine=wants))
    queues: list[tuple[Job, ...]] = [()]
    queues += [(Job(6, "preview", W1, True, False, "user", True),), (Job(6, "r4", W1, False, False, "app", True),)]
    queues += [(Job(6, "recover_all", None, True, False, "switch", True),)]
    for running, slot, lock, excl, queued, advice in itertools.product(
        runnings, ("none", "idle", "busy", "ending"), ("waiting", "free"), (False, True), queues, (False, True)
    ):
        if running is None and slot == "busy":
            continue
        if advice and not (slot == "ending" or lock == "waiting"):
            continue
        yield QueueState(
            queued=queued,
            running=running,
            slot=slot,
            engine_lock=lock,
            open_workspace=W1,
            exclusive=excl,
            advice=advice,
            epoch=3,
            next_id=7,
            engine=None if slot == "none" else 4,
            next_engine=5,
        )


def abstract_events(state: QueueState) -> Iterable[Event]:
    ids = (5, 6, 999)
    for jid in ids:
        yield from (Cancel(jid), ClientGone(jid), Gate(jid), JobDone(jid, True), JobDone(jid, False))
        yield from (EngineNeeded(jid), RetireEngine(jid))
    yield from (
        EngineDied(3),
        EngineDied(4),
        EngineEnded(),
        EngineLockFree(),
        AdviceTimer(3),
        AdviceTimer(2),
        Boot(),
    )
    yield from (WorkspaceOpened(W2), WorkspaceClosed(), ExclusiveBegan(), ExclusiveEnded())
    yield from (Submit(user("preview")), Submit(background("r4")), Submit(user("create", None)))
    yield Submit(switch_job("recover_all"))


def test_every_event_is_defined_in_every_abstract_state_and_the_properties_hold() -> None:
    """§14 "the exhaustive (state x event) test of `queue.transition`": the table answers every event in every
    state of the finite abstract space (no exception), and the model's queue properties hold after it."""
    pairs = 0
    for state in abstract_states():
        for event in abstract_events(state):
            check_step(POLICY, state, event, transition(POLICY, state, event))
            pairs += 1
    assert pairs > 10_000


def test_every_declared_event_is_exercised_by_the_sweep() -> None:
    seen = {type(e) for e in abstract_events(QueueState())}
    assert seen == set(EVENTS)


def test_the_policy_refuses_a_queue_that_holds_nothing() -> None:
    with pytest.raises(ValueError, match="max_queued"):
        QueuePolicy(max_queued=0)


def test_states_and_effects_are_immutable_values() -> None:
    state = QueueState()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.slot = "busy"  # type: ignore[misc]
