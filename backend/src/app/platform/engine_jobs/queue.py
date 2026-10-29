"""The engine job queue's decisions: a pure table (S0053; `design/specs/engine-jobs.md` §3, §4, §5.1, §5.2, §12;
ADR-0064 decisions 1-5 and its amendment of 2026-10-28).

`transition(policy, state, event) -> Step(state, effects)` decides, with no clock, random value, environment or
global (times arrive as events; §12): which job runs next, when an engine may start, who may end a running job, the
commit-point step, and when the quit advice shows. The scheduler shell (BL-0150) runs it on one thread in the user
container (INV-11), feeds every observation in as an event and carries out the effects; until then nothing in the
product calls it. Each rule names its spec line.

- **One engine, one job** (§5.1; ADR-0064 decision 1). `running` holds at most one job; `slot` is the engine:
  `none`, `idle` (alive, ran only parse-only work, may serve the next job), `busy` (the running job's engine) or
  `ending` (we signalled it, or it died, and its group is not drained yet). A new engine starts only from `none` or
  `idle`, and only once the sidecar has waited out the engine lock (`engine_lock == "free"`): an engine of an earlier
  session must have ended before this session's first ledger work (simplification B).
- **The ordering rule** (§3): when the engine is free, the oldest eligible queued waited-for job starts, else the
  oldest eligible background job. A running job is never interrupted to start another. Eligible: during a switch's,
  a Reopen's or the boot's window (`exclusive`) only its own jobs (`owner == "switch"`); otherwise jobs of the open
  workspace and app-scoped ones (§3; INV-31).
- **Admission** (§3): a job of a workspace that is not open is refused `WORKSPACE_NOT_OPEN` (INV-28); beyond
  `max_queued` queued jobs `ENGINE_QUEUE_FULL` (INV-10); a second queued `r4` or background save of one workspace is
  not queued (`AlreadyQueued`; every job is idempotent at its start, §2).
- **Cancel** (§4): the job's own requester cancels it, queued (removed; nothing ran) or running (its engine ended
  with reason `cancel`, recorded before the signal, so it is never a stop; §5.2), except past its commit point ("too
  late"). Cancel of a queued job applies **the one rule** to the job running ahead of it: that job is ended only when
  nobody waits for it, the user did not confirm it, and it has not reached its commit point (an R4, a background save
  before its commit point, a read or an open whose requester has gone); otherwise the waiting user's own request is
  cancelled with the answer that the earlier work cannot be stopped from here (`cannot_stop_ahead`), unless the
  running job is a read, an open or a recover-all its own requester still waits for (theirs to cancel).
- **A client that goes away** (§4) cancels its preview, rebuild or BQL; a save, create, copy or open only loses its
  requester ("closing is not a cancel", ADR-0063 decision 1 step 3).
- **The commit point** (§4, §6.1 step 7, §8.2 step 6): the engine sends `gate` and waits; in one step the table
  marks the job past its commit point and answers `Proceed`. A Cancel processed before that step has already ended
  the engine, so its `gate` changes nothing; one after it is "too late".
- **Retire** (§5.1): after a job that ran user code its engine is ended (`retire`); a job may also retire its engine
  and ask for a fresh one mid-job (`RetireEngine`, `EngineNeeded`: the save's own R4 first, R4's confirm, §6.1, §6.7).
- **The advice on the timer** (§5.2, §5.4; model finding N3; simplification B): the shell arms a timer with the
  current `epoch` whenever the slot becomes `ending` and at boot; when it fires and the slot is still held by a
  process we signalled (or the engine lock is still waited for), `ShowAdvice` ("Finzytrack cannot stop the earlier
  ledger work. Quit and reopen Finzytrack."), whether or not a job waits. Never for a running job the user merely
  cannot end: that answer comes only to a Cancel.

Not here yet (BL-0109's later pieces): the engine's `Unavailable` (BL-0150), recover-all's Cancel per journal
(BL-0151), the quiesce, the quit and their screens (BL-0154). The code map's planned list names each
(`backend/tests/architecture/test_tla_mapping.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Final, Literal

from app.platform.tla import tla_action

TABLE_VERSION: Final = 1
"""Raised whenever a decision changes, so the decision log (§12) replays each recording with its own table."""

type JobKind = Literal["open", "rebuild", "preview", "save", "r4", "bql", "recover_all", "create", "copy"]
type Owner = Literal["user", "app", "switch"]
type Slot = Literal["none", "idle", "busy", "ending"]
type EngineLock = Literal["waiting", "free"]
type EndReason = Literal["cancel", "retire", "crashed"]
type RefusalCode = Literal["WORKSPACE_NOT_OPEN", "ENGINE_QUEUE_FULL"]

WRITING: frozenset[JobKind] = frozenset({"save", "create", "copy"})
"""Jobs that write and have a commit point (§1, §4)."""
READS: frozenset[JobKind] = frozenset({"preview", "rebuild", "bql"})
"""Jobs a client that goes away cancels (§4)."""


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    """`max_queued`: how many jobs may wait (§3, a setting, generous by default; INV-10)."""

    max_queued: int = 64

    def __post_init__(self) -> None:
        if self.max_queued < 1:
            msg = f"max_queued must be at least 1, not {self.max_queued!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class JobSpec:
    """A job as its requester asks for it. `workspace` is the open workspace's key, or None for app-scoped work
    (`create`, `copy`, `recover_all`); `waited_for` is False for background work (R4, a save the app started);
    `confirmed` is True for a save, create or copy the user confirmed; `owner` is `switch` for a switch's or the
    boot's own jobs."""

    kind: JobKind
    workspace: str | None
    waited_for: bool
    confirmed: bool
    owner: Owner


@dataclass(frozen=True, slots=True)
class Job:
    """A queued or running job. `requester_present` turns False when its client goes away (§4)."""

    id: int
    kind: JobKind
    workspace: str | None
    waited_for: bool
    confirmed: bool
    owner: Owner
    requester_present: bool


@dataclass(frozen=True, slots=True)
class Running:
    """The running job: past its commit point or not, and waiting for a fresh engine mid-job or not."""

    job: Job
    past_commit: bool = False
    wants_engine: bool = False


@dataclass(frozen=True, slots=True)
class QueueState:
    queued: tuple[Job, ...] = ()
    running: Running | None = None
    slot: Slot = "none"
    engine_lock: EngineLock = "waiting"
    open_workspace: str | None = None
    exclusive: bool = False
    advice: bool = False
    epoch: int = 0
    next_id: int = 1


# -- Events -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Boot:
    """The sidecar starts: it must wait for the engine lock before its first engine (§5.1)."""


@dataclass(frozen=True, slots=True)
class EngineLockFree:
    """The sidecar took the engine lock and released it: no engine of an earlier session holds it (§5.1)."""


@dataclass(frozen=True, slots=True)
class Submit:
    spec: JobSpec


@dataclass(frozen=True, slots=True)
class Cancel:
    """The job's own requester pressed Cancel (queued or running)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class ClientGone:
    """The job's requester went away: a tab closed, a request aborted (§4)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class Gate:
    """The engine reached a writing job's commit point and waits for `Proceed` (§4)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class JobDone:
    """The job's final reply; `ran_user_code`: its engine loaded the ledger with plugins (§1)."""

    job_id: int
    ran_user_code: bool


@dataclass(frozen=True, slots=True)
class EngineDied:
    """The engine ended without our signal (§5.3: noticed by its exit); `job_id` is the job it ran, if any."""

    job_id: int | None


@dataclass(frozen=True, slots=True)
class EngineEnded:
    """The ending engine was reaped and its group has no live member (§5.2)."""


@dataclass(frozen=True, slots=True)
class EngineNeeded:
    """The running job needs a fresh engine for its next part (§5.1, §6.1, §6.7, §8.1)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class RetireEngine:
    """The running job's engine ran user code and must end before the job's next part (§5.1)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class AdviceTimer:
    """The display delay armed at `epoch` has passed (§5.4)."""

    epoch: int


@dataclass(frozen=True, slots=True)
class WorkspaceOpened:
    key: str


@dataclass(frozen=True, slots=True)
class WorkspaceClosed:
    pass


@dataclass(frozen=True, slots=True)
class ExclusiveBegan:
    """A switch, Reopen or boot is past its close: only its own jobs start (§3)."""


@dataclass(frozen=True, slots=True)
class ExclusiveEnded:
    """The switch or boot has ended, or shows its failed or blocked screen (§3, model finding F4)."""


type Event = (
    Boot
    | EngineLockFree
    | Submit
    | Cancel
    | ClientGone
    | Gate
    | JobDone
    | EngineDied
    | EngineEnded
    | EngineNeeded
    | RetireEngine
    | AdviceTimer
    | WorkspaceOpened
    | WorkspaceClosed
    | ExclusiveBegan
    | ExclusiveEnded
)

EVENTS: tuple[type, ...] = (
    Boot,
    EngineLockFree,
    Submit,
    Cancel,
    ClientGone,
    Gate,
    JobDone,
    EngineDied,
    EngineEnded,
    EngineNeeded,
    RetireEngine,
    AdviceTimer,
    WorkspaceOpened,
    WorkspaceClosed,
    ExclusiveBegan,
    ExclusiveEnded,
)

# -- Effects ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaitEngineLock:
    """Take the engine lock, waiting, and release it at once; then feed `EngineLockFree` (§5.1)."""


@dataclass(frozen=True, slots=True)
class ArmAdviceTimer:
    """Feed `AdviceTimer(epoch)` after the display delay (§5.4)."""

    epoch: int


@dataclass(frozen=True, slots=True)
class ShowAdvice:
    """ "Finzytrack cannot stop the earlier ledger work. Quit and reopen Finzytrack." (§5.2, §11)."""


@dataclass(frozen=True, slots=True)
class HideAdvice:
    pass


@dataclass(frozen=True, slots=True)
class Queued:
    job: Job

    @property
    def job_id(self) -> int:
        return self.job.id


@dataclass(frozen=True, slots=True)
class Refused:
    code: RefusalCode


@dataclass(frozen=True, slots=True)
class AlreadyQueued:
    job_id: int


@dataclass(frozen=True, slots=True)
class Start:
    """Run `job` on the idle engine (`reuse`) or on a fresh one."""

    job: Job
    reuse: bool


@dataclass(frozen=True, slots=True)
class Proceed:
    """Let the engine past the commit point (§4)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class EndEngine:
    """Signal the engine's group with this reason, recorded before the signal (§5.2), and drain it; feed
    `EngineEnded` once it has no live member."""

    reason: EndReason


@dataclass(frozen=True, slots=True)
class Cancelled:
    """The job ended by a Cancel, before its commit point; `cannot_stop_ahead`: the earlier ledger work could not be
    stopped from here ("Your request is cancelled. The earlier ledger work cannot be stopped from here; if it never
    finishes, quit and reopen Finzytrack.", §4, §11)."""

    job_id: int
    cannot_stop_ahead: bool = False


@dataclass(frozen=True, slots=True)
class TooLate:
    """A Cancel after the commit point: the job finishes ("Finishing your change…", §4)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class EngineLost:
    """The running job's engine died; the job's own code says what that means (§5.3, §6.6)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class Finished:
    job_id: int


type Effect = (
    WaitEngineLock
    | ArmAdviceTimer
    | ShowAdvice
    | HideAdvice
    | Queued
    | Refused
    | AlreadyQueued
    | Start
    | Proceed
    | EndEngine
    | Cancelled
    | TooLate
    | EngineLost
    | Finished
)


@dataclass(frozen=True, slots=True)
class Step:
    state: QueueState
    effects: tuple[Effect, ...] = ()


# -- Rules ------------------------------------------------------------------------------------------------------------


def eligible(state: QueueState, job: Job) -> bool:
    """§3: during a switch's, a Reopen's or the boot's window only its own jobs start (INV-31); otherwise the open
    workspace's jobs and app-scoped ones."""
    if state.exclusive:
        return job.owner == "switch"
    return job.owner != "switch" and (job.workspace is None or job.workspace == state.open_workspace)


def endable_by_another(running: Running) -> bool:
    """§4's one rule: another job's Cancel ends the running job only when nobody waits for it, the user did not
    confirm it, and it has not reached its commit point. Recover-all is never ended this way (§8.1)."""
    job = running.job
    if job.kind == "r4":
        return True
    if job.kind == "save":
        return not job.confirmed and not running.past_commit
    return job.kind in (*READS, "open") and not job.requester_present


def _ending(state: QueueState, reason: EndReason) -> tuple[QueueState, tuple[Effect, ...]]:
    """The engine is signalled (or found dead) and drained: the slot is held until `EngineEnded` (§5.1, §5.2)."""
    epoch = state.epoch + 1
    return replace(state, slot="ending", epoch=epoch), (EndEngine(reason), ArmAdviceTimer(epoch))


def _end_running(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """Our `cancel` end of the running job: its engine is signalled if it holds one; a job between engines holds
    none (model `CancelEng`)."""
    state = replace(state, running=None)
    if state.slot == "busy":
        return _ending(state, "cancel")
    return state, ()


@tla_action("Dispatch")
def _dispatch(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """§3, §5.1: when the engine is free (none, or idle after parse-only work) and the engine lock was waited out,
    the running job gets the fresh engine it asked for; with no running job, the oldest eligible waited-for job
    starts, else the oldest eligible background job."""
    if state.engine_lock != "free" or state.slot not in ("none", "idle"):
        return state, ()
    reuse = state.slot == "idle"
    running = state.running
    if running is not None:
        if not running.wants_engine:
            return state, ()
        state = replace(state, running=replace(running, wants_engine=False), slot="busy")
        return state, (Start(running.job, reuse),)
    pool = [j for j in state.queued if eligible(state, j)]
    if not pool:
        return state, ()
    waited = [j for j in pool if j.waited_for]
    job = waited[0] if waited else pool[0]
    queued = tuple(j for j in state.queued if j.id != job.id)
    return replace(state, queued=queued, running=Running(job), slot="busy"), (Start(job, reuse),)


@tla_action("SpawnStep")
def _engine_needed(p: QueuePolicy, s: QueueState, e: EngineNeeded) -> Step:
    """§5.1: a job's next part gets a fresh engine only once the one before it has ended: the ask is kept, and the
    dispatch starts the engine when the slot is free (an ask made while the old engine still runs is not lost)."""
    r = s.running
    if r is None or r.job.id != e.job_id:
        return Step(s)
    return Step(replace(s, running=replace(r, wants_engine=True)))


@tla_action("FreshEnd")
def _retire_engine(p: QueuePolicy, s: QueueState, e: RetireEngine) -> Step:
    """§5.1: an engine that ran user code ends before the job's next part (reason `retire`, not a stop)."""
    r = s.running
    if r is None or r.job.id != e.job_id or s.slot != "busy":
        return Step(s)
    state, effects = _ending(s, "retire")
    return Step(state, effects)


def _boot(p: QueuePolicy, s: QueueState, e: Boot) -> Step:
    """§5.1: at start, before its first engine, the sidecar waits for the engine lock; the advice shows after the
    display delay while it waits."""
    epoch = s.epoch + 1
    return Step(replace(s, engine_lock="waiting", epoch=epoch), (WaitEngineLock(), ArmAdviceTimer(epoch)))


def _clear_advice(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """The advice goes once nothing holds the slot or the lock (model `Drain`, `OrphanEnd`)."""
    if state.slot == "ending" or state.engine_lock == "waiting":
        return state, ()
    state = replace(state, epoch=state.epoch + 1)
    if not state.advice:
        return state, ()
    return replace(state, advice=False), (HideAdvice(),)


def _engine_lock_free(p: QueuePolicy, s: QueueState, e: EngineLockFree) -> Step:
    state, effects = _clear_advice(replace(s, engine_lock="free"))
    return Step(state, effects)


def _submit(p: QueuePolicy, s: QueueState, e: Submit) -> Step:
    """§3: refused for a workspace that is not open (INV-28); one queued R4 and one queued background save per
    workspace; refused beyond `max_queued` (INV-10)."""
    spec = e.spec
    if spec.owner != "switch" and spec.workspace is not None and spec.workspace != s.open_workspace:
        return Step(s, (Refused("WORKSPACE_NOT_OPEN"),))
    if not spec.waited_for and spec.kind in ("r4", "save"):
        for job in s.queued:
            if job.kind == spec.kind and job.workspace == spec.workspace and not job.waited_for:
                return Step(s, (AlreadyQueued(job.id),))
    if len(s.queued) >= p.max_queued:
        return Step(s, (Refused("ENGINE_QUEUE_FULL"),))
    job = Job(s.next_id, spec.kind, spec.workspace, spec.waited_for, spec.confirmed, spec.owner, True)
    return Step(replace(s, queued=(*s.queued, job), next_id=s.next_id + 1), (Queued(job),))


def _behind_other_work(s: QueueState) -> bool:
    """A switch's or the boot's own queued job waits behind other work: a running job, a process not ended, or the
    engine lock (§9.1 step 3, §9.2; model `SwitchJobBehind`)."""
    return s.running is not None or s.slot not in ("none", "idle") or s.engine_lock == "waiting"


@tla_action("UserCancelQueued")
def _cancel_queued(s: QueueState, job: Job) -> Step:
    """§4: the waiting user's Cancel removes their queued job; for the job running ahead of it, the one rule."""
    if not (job.waited_for and job.requester_present):
        return Step(s)
    if job.owner == "switch" and not _behind_other_work(s):
        return Step(s)
    state = replace(s, queued=tuple(j for j in s.queued if j.id != job.id))
    r = state.running
    if r is not None and endable_by_another(r) and r.job.kind != "recover_all":
        state, ended = _end_running(state)
        return Step(state, (*ended, Cancelled(r.job.id), Cancelled(job.id)))
    own_to_cancel = r is not None and r.job.kind in (*READS, "open", "recover_all") and r.job.requester_present
    return Step(state, (Cancelled(job.id, cannot_stop_ahead=r is not None and not own_to_cancel),))


@tla_action("UserCancelRunning")
def _cancel_running(s: QueueState, r: Running) -> Step:
    """§4: the requester's Cancel ends its running job before the commit point (reason `cancel`, not a stop);
    after it the Cancel is "too late" and the job finishes. A background job has no requester to cancel it."""
    if not (r.job.waited_for and r.job.requester_present):
        return Step(s)
    if r.past_commit:
        return Step(s, (TooLate(r.job.id),))
    # TODO(BL-0151): a Cancel of recover-all after its listing ends only its engine and goes on with the next journal.
    state, ended = _end_running(s)
    return Step(state, (*ended, Cancelled(r.job.id)))


def _cancel(p: QueuePolicy, s: QueueState, e: Cancel) -> Step:
    r = s.running
    if r is not None and r.job.id == e.job_id:
        return _cancel_running(s, r)
    for job in s.queued:
        if job.id == e.job_id:
            return _cancel_queued(s, job)
    return Step(s)


@tla_action("UserCloseTab")
def _client_gone(p: QueuePolicy, s: QueueState, e: ClientGone) -> Step:
    """§4: a client that goes away cancels its queued or running preview, rebuild or BQL; any other job only loses
    its requester (closing is not a cancel). A switch's own jobs have no tab, except its open."""
    for job in s.queued:
        if job.id == e.job_id and job.requester_present and job.owner != "switch":
            if job.kind in READS:
                return Step(replace(s, queued=tuple(j for j in s.queued if j.id != job.id)), (Cancelled(job.id),))
            gone = replace(job, requester_present=False)
            return Step(replace(s, queued=tuple(gone if j.id == job.id else j for j in s.queued)))
    r = s.running
    if r is None or r.job.id != e.job_id or not r.job.requester_present:
        return Step(s)
    if r.job.owner == "switch" and r.job.kind != "open":
        return Step(s)
    if r.job.kind in READS and s.slot == "busy":
        state, ended = _end_running(s)
        return Step(state, (*ended, Cancelled(r.job.id)))
    return Step(replace(s, running=replace(r, job=replace(r.job, requester_present=False))))


@tla_action("S_Commit", "CR_Commit", "CP_Commit")
def _gate(p: QueuePolicy, s: QueueState, e: Gate) -> Step:
    """§4: the commit point, in one table step. A `gate` from an engine we already ended (a Cancel came first), from
    a job past its commit point, or from a job that does not write changes nothing."""
    r = s.running
    if r is None or r.job.id != e.job_id or r.job.kind not in WRITING or r.past_commit or s.slot != "busy":
        return Step(s)
    return Step(replace(s, running=replace(r, past_commit=True)), (Proceed(r.job.id),))


def _job_done(p: QueuePolicy, s: QueueState, e: JobDone) -> Step:
    """The final reply. §5.1: an engine that ran user code is retired; one that ran only parse-only work may serve
    the next job."""
    r = s.running
    if r is None or r.job.id != e.job_id:
        return Step(s)
    state = replace(s, running=None)
    if state.slot != "busy":
        return Step(state, (Finished(r.job.id),))
    if e.ran_user_code:
        state, ended = _ending(state, "retire")
        return Step(state, (Finished(r.job.id), *ended))
    return Step(replace(state, slot="idle"), (Finished(r.job.id),))


def _engine_died(p: QueuePolicy, s: QueueState, e: EngineDied) -> Step:
    """§5.3: a crash is noticed by the engine's exit. Its job is lost (not run again by itself); its group is
    drained before the next engine (§5.2)."""
    r = s.running
    if s.slot == "busy" and r is not None and e.job_id in (None, r.job.id):
        state, ended = _ending(replace(s, running=None), "crashed")
        return Step(state, (EngineLost(r.job.id), *ended))
    if s.slot == "idle" and e.job_id is None:
        state, ended = _ending(s, "crashed")
        return Step(state, ended)
    return Step(s)


def _engine_ended(p: QueuePolicy, s: QueueState, e: EngineEnded) -> Step:
    if s.slot != "ending":
        return Step(s)
    state, effects = _clear_advice(replace(s, slot="none"))
    return Step(state, effects)


@tla_action("AdviceTimer")
def _advice_timer(p: QueuePolicy, s: QueueState, e: AdviceTimer) -> Step:
    """§5.2, §5.1: after the display delay, the advice while the slot is held by a process we signalled (or that
    died) and that has not ended, or while the engine lock is still waited for; whether or not a job waits (N3).
    A timer armed for an earlier process (another epoch) shows nothing."""
    held = s.slot == "ending" or s.engine_lock == "waiting"
    if e.epoch != s.epoch or s.advice or not held:
        return Step(s)
    return Step(replace(s, advice=True), (ShowAdvice(),))


def _opened(p: QueuePolicy, s: QueueState, e: WorkspaceOpened) -> Step:
    return Step(replace(s, open_workspace=e.key))


def _closed(p: QueuePolicy, s: QueueState, e: WorkspaceClosed) -> Step:
    return Step(replace(s, open_workspace=None))


def _exclusive(p: QueuePolicy, s: QueueState, e: ExclusiveBegan) -> Step:
    return Step(replace(s, exclusive=True))


def _shared(p: QueuePolicy, s: QueueState, e: ExclusiveEnded) -> Step:
    return Step(replace(s, exclusive=False))


type Handler = Callable[[QueuePolicy, QueueState, Any], Step]

# One handler per event; every event is defined in every state (the exhaustive test sweeps an abstract space).
HANDLERS: tuple[tuple[type, Handler], ...] = (
    (Boot, _boot),
    (EngineLockFree, _engine_lock_free),
    (Submit, _submit),
    (Cancel, _cancel),
    (ClientGone, _client_gone),
    (Gate, _gate),
    (JobDone, _job_done),
    (EngineDied, _engine_died),
    (EngineEnded, _engine_ended),
    (EngineNeeded, _engine_needed),
    (RetireEngine, _retire_engine),
    (AdviceTimer, _advice_timer),
    (WorkspaceOpened, _opened),
    (WorkspaceClosed, _closed),
    (ExclusiveBegan, _exclusive),
    (ExclusiveEnded, _shared),
)


def transition(policy: QueuePolicy, state: QueueState, event: Event) -> Step:
    """The next state and the effects for the shell to carry out, in order: the event's own step, then the
    dispatch it may allow."""
    for kind, handler in HANDLERS:
        if type(event) is kind:
            step = handler(policy, state, event)
            after, started = _dispatch(step.state)
            return Step(after, (*step.effects, *started))
    msg = f"{type(event).__name__} is not an event of the engine job queue"
    raise TypeError(msg)
