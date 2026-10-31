"""The engine job queue's decisions: a pure table (S0053; `design/specs/engine-jobs.md` §3, §4, §5.1, §5.2, §12;
ADR-0064 decisions 1-5 and its amendment of 2026-10-28).

`transition(policy, state, event) -> Step(state, effects)` decides, with no clock, random value, environment or
global (times arrive as events; §12): which job runs next, when an engine may start, who may end a running job, the
commit-point step, and when the quit advice shows. The scheduler shell (BL-0150) runs it on one thread in the user
container (INV-11), feeds every observation in as an event and carries out the effects; until then nothing in the
product calls it. Each rule names its spec line.

- **One engine, one job** (§5.1; ADR-0064 decision 1). `running` holds at most one job; `slot` is the engine:
  `none`, `idle` (alive, ran only parse-only work, may serve the next job), `busy` (the running job's engine) or
  `ending` (we signalled it, or it died, and its group is not drained yet). A job starts only from `none` or `idle`.
  Each spawn gets its own number, carried in `Start` and named by `EngineDied`; a reused engine keeps its number, so
  a death report is never taken for an earlier engine's, and one about an earlier engine changes nothing (§5.3).
- **The engine lock before every fresh engine** (§5.1, §15; simplification B; S0053 verifier round 2, S0058): a
  fresh engine starts only once the sidecar has waited out the engine lock since the last fresh start
  (`engine_lock == "free"`; a fresh start makes it `unchecked` again). The boot waits at once (an engine of an
  earlier session must have ended before this session's first ledger work); later the dispatch asks for a wait when a
  fresh engine is needed, so a plugin's child that left the group holding the lock delays the next engine, with the
  advice, instead of an engine waiting at the lock unseen. A failed wait starts nothing: the jobs that need an engine
  end without one (`NoEngine`), and the next one waits again. A reuse needs no wait.
- **The engine unavailable** (§5.3; model `DispatchUnavail`, `SpawnStep`): the shell feeds `EngineAvailability`
  (the client's bound reached, or an incompatible engine; Try again or the restart window lifts it). Meanwhile no
  job starts; every eligible queued job, waited-for first, and a running job that asks for a fresh engine end without
  one (`NoEngine`), without waiting for the slot.
- **The ordering rule** (§3): when the engine is free, the oldest eligible queued waited-for job starts, else the
  oldest eligible background job. A running job is never interrupted to start another. Eligible: during a switch's,
  a Reopen's or the boot's window (`exclusive`; the boot's opens with `Boot`, "from the start") only its own jobs
  (`owner == "switch"`); otherwise jobs of the open workspace and app-scoped ones (§3; INV-31).
- **Admission** (§3): a job of a workspace that is not open is refused `WORKSPACE_NOT_OPEN` (INV-28); beyond
  `max_queued` queued jobs `ENGINE_QUEUE_FULL` (INV-10); a second queued `r4` or background save of one workspace is
  not queued (`AlreadyQueued`; every job is idempotent at its start, §2; the shell lets a newer save's R4 take the
  queued one's place, S0068).
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
  and ask for a fresh one mid-job (`RetireEngine`, `EngineNeeded`: a save whose engine died reads its outcome, §6.6).
- **The advice on the timer** (§5.2, §5.4; model finding N3; simplification B): the shell arms a timer with the
  current `epoch` whenever the slot becomes `ending` and at boot; when it fires and the slot is still held by a
  process we signalled (or the engine lock is still waited for), `ShowAdvice` ("Finzytrack cannot stop the earlier
  ledger work. Quit and reopen Finzytrack."), whether or not a job waits. Never for a running job the user merely
  cannot end: that answer comes only to a Cancel.

- **Recover-all goes on per journal** (§4's `recover_all` row, §8.1; S0062; model `UserCancelRunning`, `EngineCrash`,
  `SpawnStep`'s `rc_spawn` branch): the job reports its listing (`RecoveryListed`); after it, its own Cancel ends only
  its engine (`JournalCancelled`: the journal in hand counts `cancelled`, and the job asks for a fresh engine for the
  next one), before it the Cancel ends the job as for any job (nothing is marked; model I14). An engine's death
  during recover-all ends only that engine: the job is not lost, it asks for a fresh engine (the journal in hand
  counts `failed`; before the listing it lists again), after the client's backoff. When no engine can be had for it
  (the engine unavailable, the lock's wait failed), the job is not ended without its report: `EngineRefused` tells
  its work, which marks every journal not yet tried `not_tried` and returns.

- **A save whose engine died is not lost** (§5.3 "a save's outcome is resolved from disk (§6.6)"; S0063): its
  engine ends as `crashed` (a stop) and the job asks for a fresh engine that reads the outcome from disk; when none
  can be had, `EngineRefused` tells its work, which answers without one. The model ends the job at the crash and tells
  the disk's outcome at once (`EngineCrash`'s save branch); the code needs an engine to read the disk (the sidecar
  never reads ledger or journal files): the queue replay answers the save at once, and the engine's reading of the
  disk is replayed against the model's states in `engine/tests/ledger/test_save_conformance.py` (S0064).

Not here yet (BL-0109's later pieces): the quiesce, the quit and their screens (BL-0154). The code map's planned list
names each (`backend/tests/architecture/test_tla_mapping.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Final, Literal

from app.platform.tla import tla_action

TABLE_VERSION: Final = 7  # 2 (S0058): the lock before every fresh engine, the engine unavailable; 3: Try again;
# 4 (S0062): recover-all goes on per journal (Cancel after its listing, a death, no engine); 5 (S0063): a save whose
# engine died reads its outcome from disk in a fresh engine; 6 (S0066): so does an R4, to count its failure to run;
# 7 (S0068, ADR-0067): an R4 whose engine died is lost like a read (nothing is counted)
"""Raised whenever a decision changes, so the decision log (§12) replays each recording with its own table."""

type JobKind = Literal["open", "rebuild", "preview", "save", "r4", "bql", "recover_all", "create", "copy"]
type Owner = Literal["user", "app", "switch"]
type Slot = Literal["none", "idle", "busy", "ending"]
type EngineLock = Literal["unchecked", "waiting", "free"]
type EndReason = Literal["cancel", "retire", "crashed"]
type RefusalCode = Literal["WORKSPACE_NOT_OPEN", "ENGINE_QUEUE_FULL"]
type NoEngineWhy = Literal["unavailable", "lock_failed"]

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
    """The running job: past its commit point or not, waiting for a fresh engine mid-job or not, for a recover-all
    whether it has listed the journals (§8.1), and for a save whether its engine died, so that its outcome is read
    from disk next, in a fresh engine (§6.6; S0063)."""

    job: Job
    past_commit: bool = False
    wants_engine: bool = False
    listed: bool = False
    outcome_owed: bool = False


@dataclass(frozen=True, slots=True)
class QueueState:
    """`engine`: the spawn number of the engine in the slot (None when the slot is `none`); `next_engine`: the number
    the next fresh engine gets. Every spawn gets a new number and a reused engine keeps its own, so an `EngineDied`
    names one engine and never an earlier one (§5.3). `engine_lock`: `waiting` while the sidecar waits for it, `free`
    once waited out, `unchecked` after a fresh start or a failed wait (the next fresh engine waits again).
    `unavailable`: the engine cannot be started (§5.3); `resetting`: Try again's reset of the client is under way."""

    queued: tuple[Job, ...] = ()
    running: Running | None = None
    slot: Slot = "none"
    engine_lock: EngineLock = "waiting"
    open_workspace: str | None = None
    exclusive: bool = False
    advice: bool = False
    epoch: int = 0
    next_id: int = 1
    engine: int | None = None
    next_engine: int = 1
    unavailable: bool = False
    resetting: bool = False


# -- Events -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Boot:
    """The sidecar starts: it must wait for the engine lock before its first engine (§5.1)."""


@dataclass(frozen=True, slots=True)
class EngineLockFree:
    """The sidecar took the engine lock and released it: no engine of an earlier session holds it (§5.1)."""


@dataclass(frozen=True, slots=True)
class EngineLockFailed:
    """The sidecar's wait for the engine lock failed (an `OSError`): an earlier session's engine may still run, so no
    engine starts on it (S0053 verifier F2 and round 2)."""


@dataclass(frozen=True, slots=True)
class EngineAvailability:
    """Whether the engine can be started (§5.3): False once the restart bound is reached or the engine is
    incompatible; True when the restart window lets a stop go, or after Try again."""

    available: bool


@dataclass(frozen=True, slots=True)
class TryAgain:
    """The user pressed Try again on the engine unavailable (§5.3, §11)."""


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
    """The engine ended without our signal (§5.3: noticed by its exit), busy or idle. `engine` is the number its
    `Start` carried; a report naming another number is about an earlier engine, already ended and drained (§5.2),
    and changes nothing. A report about the engine in the slot is never mistaken for an earlier one's, even when a
    job was started on it (reused) after it died and before the report was fed (S0053 verifier R2-F1)."""

    engine: int


@dataclass(frozen=True, slots=True)
class EngineEnded:
    """The ending engine was reaped and its group has no live member (§5.2)."""


@dataclass(frozen=True, slots=True)
class EngineNeeded:
    """The running job needs a fresh engine for its next part (§5.1, §6.1, §6.7, §8.1)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class RecoveryListed:
    """The running recover-all has listed the journals (§8.1): from now on its Cancel ends only its engine."""

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
    | EngineLockFailed
    | EngineAvailability
    | TryAgain
    | Submit
    | Cancel
    | ClientGone
    | Gate
    | JobDone
    | EngineDied
    | EngineEnded
    | EngineNeeded
    | RecoveryListed
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
    EngineLockFailed,
    EngineAvailability,
    TryAgain,
    Submit,
    Cancel,
    ClientGone,
    Gate,
    JobDone,
    EngineDied,
    EngineEnded,
    EngineNeeded,
    RecoveryListed,
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
class ResetEngine:
    """Forget the client's stops and an incompatible verdict, then feed `EngineAvailability(True)` (Try again)."""


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
    """Run `job` on the idle engine (`reuse`, keeping its number) or on a fresh one, spawned as number `engine`; the
    shell names the engine by that number in `EngineDied` (§5.3)."""

    job: Job
    reuse: bool
    engine: int


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


@dataclass(frozen=True, slots=True)
class NoEngine:
    """The job ends without an engine (§5.3, §11 `ENGINE_UNAVAILABLE`): the engine is unavailable, or the wait for the
    engine lock failed. Nothing of it ran (a running job between engines keeps what its earlier parts did)."""

    job_id: int
    why: NoEngineWhy


@dataclass(frozen=True, slots=True)
class JournalCancelled:
    """A recover-all's Cancel after its listing (§4, §8.1): its engine is ended, the job goes on; the journal in hand
    counts `cancelled` (its work learns it from its call)."""

    job_id: int


@dataclass(frozen=True, slots=True)
class EngineRefused:
    """The running recover-all asked for an engine and none can be had (§8.1 "When the engine cannot run"): the job
    is not ended; its work marks every journal not yet tried `not_tried` and returns its report."""

    job_id: int
    why: NoEngineWhy


type Effect = (
    WaitEngineLock
    | ResetEngine
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
    | NoEngine
    | JournalCancelled
    | EngineRefused
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
        return not job.waited_for and not job.confirmed  # the background R4 (S0068: no Resume saving left)
    if job.kind == "save":
        return not job.confirmed and not running.past_commit
    return job.kind in (*READS, "open") and not job.requester_present


def _reports_itself(running: Running) -> bool:
    """A running job that ends with its own answer when no engine can be had for its next part (`EngineRefused`):
    a recover-all (its report, §8.1) and a save whose engine died (its outcome, §6.6)."""
    return running.job.kind == "recover_all" or running.outcome_owed


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


def _dispatch(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """After every event: the running job's ask for a fresh engine (`SpawnStep`); with no running job, the next
    queued job (`Dispatch`), or, while the engine is unavailable, the queued jobs end without one (`DispatchUnavail`).
    A running job is never interrupted (§3)."""
    effects: tuple[Effect, ...] = ()
    if state.running is not None:
        state, effects = _spawn_step(state)
    if state.running is not None:
        return state, effects
    state, more = _dispatch_unavailable(state) if state.unavailable else _dispatch_queued(state)
    return state, (*effects, *more)


def _pick(state: QueueState) -> Job | None:
    """§3: the oldest eligible waited-for job, else the oldest eligible background job."""
    pool = [j for j in state.queued if eligible(state, j)]
    waited = [j for j in pool if j.waited_for]
    return waited[0] if waited else (pool[0] if pool else None)


@tla_action("Dispatch")
def _dispatch_queued(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """§3, §5.1: with the engine free (none, or idle after parse-only work), the oldest eligible waited-for job
    starts, else the oldest eligible background job; a fresh engine waits for the engine lock first."""
    if state.slot not in ("none", "idle"):
        return state, ()
    job = _pick(state)
    if job is None:
        return state, ()
    state, effects = _engine_for(state, job)
    if not any(isinstance(e, Start) for e in effects):
        return state, effects
    return replace(state, queued=tuple(j for j in state.queued if j.id != job.id), running=Running(job)), effects


@tla_action("DispatchUnavail")
def _dispatch_unavailable(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """§5.3, §11; model `DispatchUnavail` (no `SlotFree`: nothing will start, so nothing waits for a drain): while the
    engine is unavailable, every eligible queued job ends without one, in the order it would have started."""
    effects: list[Effect] = []
    while (job := _pick(state)) is not None:
        state = replace(state, queued=tuple(j for j in state.queued if j.id != job.id))
        effects.append(NoEngine(job.id, "unavailable"))
    return state, tuple(effects)


@tla_action("SpawnStep")
def _spawn_step(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """§5.1: the running job that asked for a fresh engine gets one once the slot is free (after the lock's wait);
    model `SpawnStep`'s unavailable branch: while the engine is unavailable it ends without one ("the save
    could not get an engine: refused, nothing written")."""
    running = state.running
    if running is None or not running.wants_engine or state.slot not in ("none", "idle"):
        return state, ()
    if state.unavailable and _reports_itself(running):
        return replace(state, running=replace(running, wants_engine=False)), (
            EngineRefused(running.job.id, "unavailable"),
        )
    if state.unavailable:
        return replace(state, running=None), (NoEngine(running.job.id, "unavailable"),)
    state, effects = _engine_for(state, running.job)
    if any(isinstance(e, Start) for e in effects):
        state = replace(state, running=replace(running, wants_engine=False))
    return state, effects


def _engine_for(state: QueueState, job: Job) -> tuple[QueueState, tuple[Effect, ...]]:
    """§5.1: called with the slot `none` or `idle`. An idle engine serves the job at once; a fresh one starts only
    once the engine lock was waited out since the last fresh start, else a wait is asked for (one at a time)."""
    if state.engine is not None:
        return replace(state, slot="busy"), (Start(job, reuse=True, engine=state.engine),)
    if state.engine_lock == "free":
        number = state.next_engine
        state = replace(state, slot="busy", engine=number, next_engine=number + 1, engine_lock="unchecked")
        return state, (Start(job, reuse=False, engine=number),)
    if state.engine_lock == "waiting":
        return state, ()
    return _wait_lock(state)


def _wait_lock(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """§5.1: wait for the engine lock; the advice shows after the display delay while the wait lasts."""
    epoch = state.epoch + 1
    return replace(state, engine_lock="waiting", epoch=epoch), (WaitEngineLock(), ArmAdviceTimer(epoch))


def _engine_needed(p: QueuePolicy, s: QueueState, e: EngineNeeded) -> Step:
    """§5.1: a job's next part gets a fresh engine only once the one before it has ended: the ask is kept, and the
    dispatch starts the engine when the slot is free (an ask made while the old engine still runs is not lost)."""
    r = s.running
    if r is None or r.job.id != e.job_id:
        return Step(s)
    return Step(replace(s, running=replace(r, wants_engine=True)))


def _retire_engine(p: QueuePolicy, s: QueueState, e: RetireEngine) -> Step:
    """§5.1: an engine that ran user code ends before the job's next part (reason `retire`, not a stop). The model no
    longer has this step (its `FreshEnd` went with R4's confirm and the save's R4 first, S0068): the one job that
    still asks is a save reading its outcome from disk after a refusal past its gate, which the model tells at the
    crash (`formal/tla/engine-jobs/MAPPING.md` §5)."""
    r = s.running
    if r is None or r.job.id != e.job_id or s.slot != "busy":
        return Step(s)
    state, effects = _ending(s, "retire")
    return Step(state, effects)


def _recovery_listed(p: QueuePolicy, s: QueueState, e: RecoveryListed) -> Step:
    """§8.1: the running recover-all has listed the journals."""
    r = s.running
    if r is None or r.job.id != e.job_id or r.job.kind != "recover_all":
        return Step(s)
    return Step(replace(s, running=replace(r, listed=True)))


def _boot(p: QueuePolicy, s: QueueState, e: Boot) -> Step:
    """§5.1: at start, before its first engine, the sidecar waits for the engine lock; the advice shows after the
    display delay while it waits. §3: the boot's window is open "from the start" (model `SwitchExclusive` holds from
    `Init`): only its own jobs start until the shell feeds `ExclusiveEnded` when the boot ends (BL-0154)."""
    state, effects = _wait_lock(replace(s, exclusive=True))
    return Step(state, effects)


def _clear_advice(state: QueueState) -> tuple[QueueState, tuple[Effect, ...]]:
    """The advice goes once nothing holds the slot or the lock (model `Drain`, `OrphanEnd`)."""
    if state.slot == "ending" or state.engine_lock == "waiting":
        return state, ()
    state = replace(state, epoch=state.epoch + 1)
    if not state.advice:
        return state, ()
    return replace(state, advice=False), (HideAdvice(),)


def _engine_lock_free(p: QueuePolicy, s: QueueState, e: EngineLockFree) -> Step:
    """The wait ended: a fresh engine may start (the dispatch after this step starts it). An answer with no wait under
    way changes nothing."""
    if s.engine_lock != "waiting":
        return Step(s)
    state, effects = _clear_advice(replace(s, engine_lock="free"))
    return Step(state, effects)


def _engine_lock_failed(p: QueuePolicy, s: QueueState, e: EngineLockFailed) -> Step:
    """S0053 verifier F2 and round 2: a failed wait starts no engine (an earlier session's engine may still run). The
    jobs that need one now end without one: the eligible queued jobs and a running job that asked for an engine;
    a job that could not start yet stays queued, and the next need waits again."""
    if s.engine_lock != "waiting":
        return Step(s)
    state = replace(s, engine_lock="unchecked")
    effects: list[Effect] = []
    r = state.running
    if r is not None and r.wants_engine and state.slot != "busy" and _reports_itself(r):
        state = replace(state, running=replace(r, wants_engine=False))  # its report (§8.1) or its answer (§6.6) comes
        effects.append(EngineRefused(r.job.id, "lock_failed"))
    elif r is not None and r.wants_engine and state.slot != "busy":  # a job still on its engine keeps it and its ask
        state = replace(state, running=None)
        effects.append(NoEngine(r.job.id, "lock_failed"))
    if state.running is None:
        while (job := _pick(state)) is not None:
            state = replace(state, queued=tuple(j for j in state.queued if j.id != job.id))
            effects.append(NoEngine(job.id, "lock_failed"))
    state, cleared = _clear_advice(state)
    return Step(state, (*effects, *cleared))


def _engine_availability(p: QueuePolicy, s: QueueState, e: EngineAvailability) -> Step:
    """§5.3: the engine's bound reached or lifted (a reset under way is done once it is lifted); the dispatch after
    this step ends or starts the waiting jobs."""
    return Step(replace(s, unavailable=not e.available, resetting=s.resetting and not e.available))


@tla_action("UserTryAgain")
def _try_again(p: QueuePolicy, s: QueueState, e: TryAgain) -> Step:
    """§5.3; model `UserTryAgain` (`Unavail`): reset the client's bound, one reset at a time; the engine is available
    only once the reset is done (the shell feeds `EngineAvailability(True)` then), so no job meets a client that
    still refuses (S0058 verifier F2: the reset is an effect of this step, not the caller's own call)."""
    if not s.unavailable or s.resetting:
        return Step(s)
    return Step(replace(s, resetting=True), (ResetEngine(),))


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
    if r.job.kind == "recover_all" and s.slot != "busy":
        return Step(s)  # model: between engines it holds none, nothing to cancel; a stuck slot gets the advice (§5.2)
    if r.job.kind == "recover_all" and r.listed:
        state, ended = _ending(s, "cancel")  # §8.1: the journal in hand counts `cancelled`; the job goes on
        return Step(state, (*ended, JournalCancelled(r.job.id)))
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
    drained before the next engine (§5.2). Only a report naming the engine in the slot counts."""
    r = s.running
    if s.slot not in ("busy", "idle") or e.engine != s.engine:
        return Step(s)
    if s.slot == "busy" and r is not None and r.job.kind == "recover_all":
        state, ended = _ending(s, "crashed")  # §8.1: the journal in hand counts `failed`; the job goes on
        return Step(state, ended)
    if s.slot == "busy" and r is not None and r.job.kind == "save":
        # §6.6: the save's outcome is read from disk in a fresh engine (the job asks for it); a Cancel before the
        # commit point still ends the job (nothing was written), one past it is "too late" (S0063). An R4 whose engine
        # died is lost like a read (S0068: nothing is counted; ADR-0067)
        state, ended = _ending(replace(s, running=replace(r, outcome_owed=True)), "crashed")
        return Step(state, ended)
    if s.slot == "busy" and r is not None:
        state, ended = _ending(replace(s, running=None), "crashed")
        return Step(state, (EngineLost(r.job.id), *ended))
    state, ended = _ending(s, "crashed")
    return Step(state, ended)


def _engine_ended(p: QueuePolicy, s: QueueState, e: EngineEnded) -> Step:
    if s.slot != "ending":
        return Step(s)
    state, effects = _clear_advice(replace(s, slot="none", engine=None))
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
    (EngineLockFailed, _engine_lock_failed),
    (EngineAvailability, _engine_availability),
    (TryAgain, _try_again),
    (Submit, _submit),
    (Cancel, _cancel),
    (ClientGone, _client_gone),
    (Gate, _gate),
    (JobDone, _job_done),
    (EngineDied, _engine_died),
    (EngineEnded, _engine_ended),
    (EngineNeeded, _engine_needed),
    (RecoveryListed, _recovery_listed),
    (RetireEngine, _retire_engine),
    (AdviceTimer, _advice_timer),
    (WorkspaceOpened, _opened),
    (WorkspaceClosed, _closed),
    (ExclusiveBegan, _exclusive),
    (ExclusiveEnded, _shared),
)


def cancel_offered(policy: QueuePolicy, state: QueueState, job_id: int) -> bool:
    """§4, §5.4 ("Cancel where §4 allows it"): whether the job's own Cancel would end it or its own wait, read from
    the table itself, so the UI never shows a Cancel the table answers with nothing (a background job, a switch job
    with nothing ahead, a recover-all between engines: S0053 verifier round 2) or with "too late"."""
    step = transition(policy, state, Cancel(job_id))
    return any(isinstance(e, Cancelled | JournalCancelled) and e.job_id == job_id for e in step.effects)


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
