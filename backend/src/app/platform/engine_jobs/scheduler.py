"""The engine job scheduler: the shell that runs the queue table on one thread (S0058; `design/specs/engine-jobs.md`
§3-§5, §11; ADR-0064).

`app.platform.engine_jobs.queue` decides; this shell observes and acts. One per user container (INV-11; registered
in `platform/state_registry.py`), it holds the `QueueState` on its **scheduler thread**, feeds every observation in as
a table event and carries out the effects, in order:

- **Requests** (`submit`, `submit_background_save`, `cancel`, `client_gone`, `try_again`, the workspace's open and
  close, the switch's window) are steps on the scheduler thread; each caller waits only for that step's answer, never
  for the engine. A job's outcome comes later on its `JobTicket`: its value, or a `Failed` with a registry error
  (`JOB_CANCELLED`, `ENGINE_STOPPED`, `ENGINE_UNAVAILABLE`, `ENGINE_QUEUE_FULL`, `WORKSPACE_NOT_OPEN`, `INTERNAL`).
- **The running job** runs on a worker thread of its own (one at a time: the table runs one job), with a
  `JobContext`: its requests go to the engine the table's `Start` gave it, and only to that one
  (`call(..., spawn=False)`): a job whose engine died is not run again on another (§5.3). A fresh `Start` starts the
  engine first ("Starting the ledger engine…"); the engine's progress becomes the job's phase (§5.4); its `gate` is
  fed to the table, whose `Proceed` the shell sends (§4: the commit point is the table's one step); a job may ask for
  a fresh engine for its next part (`fresh_engine`, §5.1). A job's own exception ends it `INTERNAL`, logged, and
  its engine is not reused (ARCHITECTURE §6.3: the job runner is one of the three catch-all places).
- **The engine's identity** (§5.3): the client reports every engine it spawns (`engine_spawned`, before that engine
  can end) and every end once the group is drained (`engine_ended`). The shell keeps the engines not yet ended and the
  current one, and maps each to the queue's number of the `Start` it serves. The slot's engine has ended
  (`EngineEnded`) once no engine is left undrained. A death is reported by the job's own failed call while the slot
  is busy (so the start's bytecode retry, two engines in one start, §5.1, is never a death), and by the end report
  of the current engine while the slot is idle (`EngineDied` with its number).
- **The engine lock** (§5.1): the table asks for a wait at boot and before every fresh engine; the wait runs on a
  thread of its own; a wait that fails (`OSError`) is logged `engine.start_failed` and fed as `EngineLockFailed`.
- **Availability** (§5.3): after a job's failed call the shell reads the client's state: `Unavailable` (the bound)
  or `Incompatible` is fed as `EngineAvailability(False)`; the restart window's end is a timer, after which the
  state is read again; Try again resets the client and feeds `EngineAvailability(True)`.
- **Timers** (§5.4): the display delay of the advice (`ArmAdviceTimer`) and the window run on the scheduler thread's
  own wait; only the newest of each counts (an older advice timer would show nothing anyway).
- **The log**: each engine's end report with its job (`engine.exit`: number, job kind, cause, exit code or signal,
  peak memory; content-free). A sink that raises loses its line, never a decision (S0041).

What the user reads is `app.platform.engine_jobs.text`, from `status()`. Bounded (INV-10): at most `max_queued`
queued jobs and one running, one worker per job (a cancelled job's worker ends once its engine has ended), one lock
wait, at most two engines in the books (the previous one until its end report, and the current one).

- **Recover-all** (S0062; §8.1): the boot's job runs the work the composition root gives
  (`app.platform.engine_jobs.recovery.recover_all_work`); its context reports the listing (`JobContext.listed`), and
  after it a Cancel or an engine's death ends only the job's engine: the work's call fails, it asks for a fresh engine
  (`fresh_engine`) and goes on with the next journal; when none can be had, `fresh_engine` answers the failure
  (`EngineRefused`) and the work returns its report.

Not here yet: the decision log and the flight recorder (§12; BL-0154), the quiesce, the switch and the quit (BL-0154;
`exclusive_began`/`exclusive_ended` are their seams), the job's CPU (BL-0082).
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from app.domain.errors import DomainError
from app.platform.engine import (
    EndReason,
    EngineEnd,
    EngineEvent,
    EngineFailure,
    EngineInfo,
    EngineRefusal,
    FailureKind,
    GateReached,
    Phase,
    Progress,
)
from app.platform.engine_jobs.queue import (
    AdviceTimer,
    AlreadyQueued,
    ArmAdviceTimer,
    Boot,
    Cancel,
    Cancelled,
    ClientGone,
    Effect,
    EndEngine,
    EngineAvailability,
    EngineDied,
    EngineEnded,
    EngineLockFailed,
    EngineLockFree,
    EngineLost,
    EngineNeeded,
    EngineRefused,
    Event,
    ExclusiveBegan,
    ExclusiveEnded,
    Finished,
    Gate,
    JobDone,
    JobKind,
    JobSpec,
    JournalCancelled,
    NoEngine,
    Proceed,
    Queued,
    QueuePolicy,
    QueueState,
    RecoveryListed,
    Refused,
    ResetEngine,
    RetireEngine,
    Start,
    Submit,
    TooLate,
    TryAgain,
    WaitEngineLock,
    WorkspaceClosed,
    WorkspaceOpened,
    transition,
)
from app.platform.engine_supervision import Incompatible, RestartPolicy, SupervisionState, Unavailable, counted
from app.platform.errors import (
    EngineStoppedDetails,
    EngineUnavailableDetails,
    ErrorCode,
    JobCancelledDetails,
    WorkspaceNotOpenDetails,
    error,
)
from app.platform.observability import Event as LogEvent
from app.platform.observability import EventSink
from app.platform.observability.events import ENGINE_EXIT, ENGINE_START_FAILED, JOB_INTERNAL_ERROR
from app.platform.tla import tla_action

DISPLAY_DELAY: Final = 2.0
"""§5.4: the display delay after which a job's text becomes "Still working…" and the advice may show (a UI constant;
it only changes the text, it ends nothing)."""

LOST: Final[frozenset[FailureKind]] = frozenset(
    {"crashed", "protocol_violation", "start_failed", "unavailable", "incompatible", "closed"}
)
"""A job's failed call whose engine is gone (or never came): the table hears `EngineDied` for it. `cancelled` is our
own end (the table already decided), `too_large` sent nothing and leaves the engine as it was."""


def _no_log(event: LogEvent, /, **fields: object) -> None:
    pass


class JobEngine(Protocol):
    """What the scheduler needs of the engine client (`app.platform.adapters.engine_client.EngineClient`)."""

    def start(self) -> EngineInfo | EngineFailure: ...

    def call(
        self,
        op: str,
        params: Mapping[str, object],
        *,
        on_event: Callable[[EngineEvent], None] | None = None,
        spawn: bool = True,
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure: ...

    def proceed(self) -> bool: ...

    def end(self, reason: EndReason) -> None: ...

    def reset(self) -> None: ...

    @property
    def state(self) -> SupervisionState: ...


# -- What a job is and what it ends with ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkResult:
    """What a job's work returns: its value, and whether its engine ran user code (§1: then it is not reused, §5.1).
    The default is the safe one: a fresh engine for the next job."""

    value: object = None
    ran_user_code: bool = True


@dataclass(frozen=True, slots=True)
class Done:
    value: object


@dataclass(frozen=True, slots=True)
class Failed:
    """The job ended without its value; `error` is a registry `DomainError`, raised as it is by the job's route."""

    error: DomainError


type Outcome = Done | Failed
type CancelAnswer = Literal["cancelled", "too_late", "nothing"]
type ShownPhase = Phase | Literal["starting"] | None


@dataclass(frozen=True, slots=True)
class Shown:
    """What a job shows (§5.4): its phase (`starting` while its engine starts, None before its first progress) and
    since when it shows it (a phase change is shown at once; the display delay counts from it)."""

    phase: ShownPhase
    since: float


@dataclass(frozen=True, slots=True)
class JobsStatus:
    """The scheduler's published state, read without waiting (`app.platform.engine_jobs.text` words it)."""

    queue: QueueState
    policy: QueuePolicy
    shown: Mapping[int, Shown]


class _Box:
    """A job's outcome, set once by the scheduler thread."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.outcome: Outcome | None = None

    def answer(self, outcome: Outcome) -> None:
        if self.outcome is None:
            self.outcome = outcome
            self.done.set()


class JobTicket:
    """A queued job: its id and, once it has ended, its outcome."""

    def __init__(self, job_id: int, kind: JobKind, box: _Box) -> None:
        self.id = job_id
        self.kind = kind
        self._box = box

    @property
    def outcome(self) -> Outcome | None:
        return self._box.outcome

    def wait(self, timeout: float | None = None) -> Outcome | None:
        """The outcome, once the job has ended (None if `timeout` passed first)."""
        self._box.done.wait(timeout)
        return self._box.outcome


type Work = Callable[[JobContext], WorkResult]


class JobContext:
    """The running job's way to its engine, given to its work on the job's worker thread."""

    def __init__(self, worker: _Worker) -> None:
        self.job_id = worker.job_id
        self.kind = worker.kind
        self._worker = worker

    def call(
        self, op: str, params: Mapping[str, object], *, on_event: Callable[[EngineEvent], None] | None = None
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        """One request to this job's engine; never to another (§5.3). `on_event` sees the job's messages first."""
        return self._worker.call(op, params, on_event)

    def fresh_engine(self) -> EngineFailure | None:
        """§5.1: end this engine (it ran user code) and wait for a fresh one for the job's next part; a failure if
        none came (the job was cancelled, or no engine could be started)."""
        return self._worker.fresh_engine()

    def listed(self) -> None:
        """A recover-all has listed the journals (§8.1): from now on its Cancel ends only its engine (S0062)."""
        self._worker.listed()


class _Worker:
    """The running job's side of the scheduler: its engine calls, its `Start`s and its end."""

    def __init__(
        self, engine: JobEngine, post: Callable[[_Message], None], books: _Books, job_id: int, kind: JobKind
    ) -> None:
        self.job_id = job_id
        self.kind = kind
        self._engine = engine
        self._post = post
        self._books = books
        self._cond = threading.Condition()
        self._grant: Start | None = None
        self._refused: EngineFailure | None = None
        self._over = False
        self._lost: EngineFailure | None = None

    def call(
        self, op: str, params: Mapping[str, object], on_event: Callable[[EngineEvent], None] | None
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        with self._cond:
            if self._over:
                return EngineFailure("cancelled", "the job has ended")
            if self._lost is not None:
                return self._lost
        post, job_id = self._post, self.job_id

        def events(event: EngineEvent) -> None:
            if on_event is not None:
                on_event(event)
            if isinstance(event, Progress):
                post(_Phase(job_id, event.phase))
            elif isinstance(event, GateReached):
                post(_Feed(Gate(job_id)))  # the commit point is the table's step (§4)

        out = self._engine.call(op, params, on_event=events, spawn=False)
        if isinstance(out, EngineFailure):
            self.fail(out)
        return out

    def fresh_engine(self) -> EngineFailure | None:
        self._post(_Feed(RetireEngine(self.job_id)))
        self._post(_Feed(EngineNeeded(self.job_id)))
        with self._cond:
            self._cond.wait_for(lambda: self._grant is not None or self._over or self._refused is not None)
            grant, self._grant = self._grant, None
            refused, self._refused = self._refused, None
        if refused is not None:
            return refused
        if grant is None:
            return EngineFailure("cancelled", "the job has ended")
        with self._cond:
            self._lost = None  # the lost engine was the one before (a recover-all goes on: §8.1, S0062)
        return self.begin(grant)

    def listed(self) -> None:
        self._post(_Feed(RecoveryListed(self.job_id)))

    def refuse(self, failure: EngineFailure) -> None:
        """No engine can be had for the job's next part (`EngineRefused`): its `fresh_engine` returns `failure`."""
        with self._cond:
            self._refused = failure
            self._cond.notify_all()

    def begin(self, start: Start) -> EngineFailure | None:
        """A `Start`: a fresh engine is started first; a reused one is ready. A job ended while its engine was being
        started gets that engine ended here: the scheduler's end can come before the start reaches the client, which
        then has nothing to end and would start the engine anyway (the slot would wait for it for ever)."""
        if start.reuse:
            return None
        try:
            out = self._engine.start()
            if isinstance(out, EngineFailure):
                self.fail(out)
                return out
            with self._cond:
                over = self._over
            if over:
                self._engine.end("cancel")
                return EngineFailure("cancelled", "the job ended while its engine started")
        finally:
            with self._books.lock:
                self._books.starting -= 1
            self._post(_Settle())
        self._post(_Phase(self.job_id, None))
        return None

    def fail(self, failure: EngineFailure) -> None:
        """A failed call whose engine is gone is reported once; the job's later calls get the same failure."""
        if failure.kind not in LOST:
            return
        with self._cond:
            if self._lost is not None:
                return
            self._lost = failure
        self._post(_JobFailed(self.job_id, failure))

    def granted(self, start: Start) -> None:
        with self._cond:
            self._grant = start
            self._cond.notify_all()

    def end(self) -> None:
        with self._cond:
            self._over = True
            self._cond.notify_all()


# -- Messages to the scheduler thread ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Feed:
    event: Event


@dataclass(slots=True)
class _Call:
    """A request run on the scheduler thread; `fallback` is its answer if `fn` raises (a bug, logged)."""

    fn: Callable[[], object]
    fallback: object = None
    done: threading.Event = field(default_factory=threading.Event)
    value: object = None


@dataclass(frozen=True, slots=True)
class _Spawned:
    serial: int


@dataclass(frozen=True, slots=True)
class _Ended:
    end: EngineEnd


@dataclass(frozen=True, slots=True)
class _Settle:
    """A worker's fresh start returned: the slot may have ended."""


@dataclass(frozen=True, slots=True)
class _JobFailed:
    job_id: int
    failure: EngineFailure


@dataclass(frozen=True, slots=True)
class _Phase:
    job_id: int
    phase: Phase | None


@dataclass(frozen=True, slots=True)
class _WorkReturned:
    job_id: int
    result: WorkResult | None  # None: the work raised (already logged)


type _Message = _Feed | _Call | _Spawned | _Ended | _Settle | _JobFailed | _Phase | _WorkReturned


@dataclass(slots=True)
class _Job:
    ticket: JobTicket
    box: _Box
    work: Work
    shown: Shown
    boot: bool = False
    worker: _Worker | None = None
    value: object = None
    raised: bool = False
    failure: EngineFailure | None = None
    past_commit: bool = False  # `Proceed` was sent for it (§4)


class _Books:
    """The engines the client spawned and has not reported ended (at most two), the current one, and the fresh
    starts handed to a worker that have not returned yet (at most one): written by the client's reports and the
    workers on their own threads, read by the scheduler (S0058). While a start is under way the slot cannot have
    ended: the start may yet spawn an engine (one that a Cancel, come first, could not reach)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.live: set[int] = set()
        self.current: int | None = None
        self.current_ended = False
        self.starting = 0


class EngineJobs:
    """The engine job queue and its scheduler thread, owned by the user container (`state_registry`)."""

    def __init__(
        self,
        engine: JobEngine,
        *,
        wait_engine_lock: Callable[[], object],
        policy: QueuePolicy | None = None,
        restart: RestartPolicy | None = None,
        display_delay: float = DISPLAY_DELAY,
        monotonic: Callable[[], float] = time.monotonic,
        log: EventSink | None = None,
        recover_all: Work | None = None,
    ) -> None:
        self._engine = engine
        self._recover_all = recover_all
        self._wait_engine_lock = wait_engine_lock
        self._policy = policy or QueuePolicy()
        self._restart = restart or RestartPolicy()
        self._delay = display_delay
        self._now = monotonic
        self._log: EventSink = log or _no_log
        self._inbox: queue.Queue[_Message] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="engine-jobs", daemon=True)
        self._guard = threading.Lock()  # guards `_started`, `_closed`
        self._started = False
        self._closed = False
        self._books = _Books()
        # Owned by the scheduler thread:
        self._state = QueueState()
        self._jobs: dict[int, _Job] = {}
        self._submitting: tuple[JobSpec, Work, bool] | None = None
        self._number_of: dict[int, int] = {}  # serial -> the queue's engine number
        self._kind_on: dict[int, JobKind] = {}  # engine number -> the kind of the last job started on it
        self._advice_due: tuple[float, int] | None = None
        self._window_due: float | None = None
        self._unavailable_reason: Literal["stopped_often", "incompatible"] = "stopped_often"
        self._follow: list[Event] = []
        self._status = JobsStatus(self._state, self._policy, {})

    # -- requests ---------------------------------------------------------------------------------------------

    def start(self) -> JobTicket | Failed:
        """The boot (§5.1, §9.2): the scheduler thread starts, the engine lock is waited for, and the boot's own
        `recover_all` is queued; its engine is the session's first. The boot's window ends with that job (§3; the
        boot's open is BL-0152/BL-0154). Never waits for the lock or the engine."""
        with self._guard:
            if self._started or self._closed:
                return self._closing()
            self._started = True
        self._thread.start()
        return self._ask(self._boot, self._closing())

    def submit(self, spec: JobSpec, work: Work) -> JobTicket | Failed:
        """Queue a job; refused at once for a workspace that is not open or a full queue (§3)."""
        return self._ask(lambda: self._submit(spec, work, boot=False), self._closing())

    @tla_action("AppBgSave")
    def submit_background_save(self, workspace: str, work: Work) -> JobTicket | Failed:
        """A save the app starts itself (prices; §2): background, unconfirmed, and at most one queued per workspace
        (§3; model `AppBgSave`): a second one is answered with the first one's ticket."""
        spec = JobSpec(kind="save", workspace=workspace, waited_for=False, confirmed=False, owner="app")
        return self.submit(spec, work)

    def cancel(self, job_id: int) -> CancelAnswer:
        """The job's own requester pressed Cancel (§4)."""
        nothing: CancelAnswer = "nothing"
        return self._ask(lambda: self._cancel(job_id), nothing)

    def client_gone(self, job_id: int) -> None:
        """The job's requester went away: a tab closed, a request aborted (§4)."""
        self._ask(lambda: self._step(ClientGone(job_id)), None)

    def try_again(self) -> None:
        """§5.3 "Try again": a step of the table, which asks for the client's reset while the engine is unavailable;
        the reset runs on a thread of its own and jobs start once it is done. Never waits for the engine. The
        client's own state is read first (its `state` never waits), so a table that has not heard of the bound yet
        (an idle engine's death, a cancelled job's failed start) does not drop the Try again (S0058 verifier R2-F1)."""
        self._ask(self._try_again, None)

    def _try_again(self) -> None:
        self._read_availability()
        self._step(TryAgain())

    def tick(self) -> None:
        """Fire the timers that are due now: the scheduler's own wait does it with the real clock; a test whose clock
        moves only when it moves it calls this after moving it (S0058 round 1: wall time decides no test)."""
        self._ask(self._fire_timers, None)

    def workspace_opened(self, key: str) -> None:
        self._ask(lambda: self._step(WorkspaceOpened(key)), None)

    def workspace_closed(self) -> None:
        self._ask(lambda: self._step(WorkspaceClosed()), None)

    def exclusive_began(self) -> None:
        """A switch or Reopen is past its close: only its own jobs start (§3; the switch is BL-0154)."""
        self._ask(lambda: self._step(ExclusiveBegan()), None)

    def exclusive_ended(self) -> None:
        self._ask(lambda: self._step(ExclusiveEnded()), None)

    def status(self) -> JobsStatus:
        """The published state: a read that never waits (one reference, replaced after every step)."""
        return self._status

    def close(self) -> None:
        """Answer every queued job, and a running one before its commit point (`ENGINE_UNAVAILABLE {closing}`), and
        stop the scheduler thread. A job past its commit point is left unanswered: it finishes (§4), and its outcome
        is the next start's to tell (§9.3). It ends nothing on the engine: the composition root's
        `EngineClient.close()` does (§9.3 step 4; the quiesce before it is BL-0154). Never waits for a job."""
        with self._guard:
            started, self._closed = self._started and not self._closed, True
        if started:
            self._inbox.put(_Call(self._close))

    # -- the client's reports (any thread) ------------------------------------------------------------------------

    def engine_spawned(self, serial: int) -> None:
        """The client spawned an engine (before it can end; the client may hold its own lock: never call back)."""
        with self._books.lock:
            self._books.live.add(serial)
            self._books.current = serial
            self._books.current_ended = False
        self._post(_Spawned(serial))

    def engine_ended(self, end: EngineEnd) -> None:
        """An engine's group was drained and the engine reaped (§5.2)."""
        with self._books.lock:
            if end.serial not in self._books.live:
                return  # not an engine this scheduler saw spawned, or reported twice
            self._books.live.discard(end.serial)
            if end.serial == self._books.current:
                self._books.current_ended = True
        self._post(_Ended(end))

    # -- the scheduler thread -------------------------------------------------------------------------------------

    def _post(self, message: _Message) -> None:
        self._inbox.put(message)

    def _ask[T](self, fn: Callable[[], T], closed: T) -> T:
        """Run `fn` on the scheduler thread and return its value (at once when already there); `closed` is the answer
        once the scheduler has stopped, and if `fn` raises (logged)."""
        if threading.current_thread() is self._thread:
            return fn()
        with self._guard:
            if not self._started or self._closed:
                return closed
            call = _Call(fn, closed)
            self._inbox.put(call)
        call.done.wait()
        return call.value  # type: ignore[return-value]

    def _loop(self) -> None:
        while True:
            message = self._next_message()
            try:
                self._fire_timers()
                if message is not None and self._handle(message):
                    return
            except Exception as exc:  # noqa: BLE001 (the scheduler's top-level loop: logged, the loop goes on)
                self._log_internal(None, exc)
            finally:
                # Published before the caller is answered, so a `status()` read after an answer shows its step.
                self._publish()
                if isinstance(message, _Call):
                    message.done.set()

    def _next_message(self) -> _Message | None:
        dues = [d for d in (self._advice_due[0] if self._advice_due else None, self._window_due) if d is not None]
        timeout = max(0.0, min(dues) - self._now()) if dues else None
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def _fire_timers(self) -> None:
        now = self._now()
        if self._advice_due is not None and self._advice_due[0] <= now:
            _, epoch = self._advice_due
            self._advice_due = None
            self._step(AdviceTimer(epoch))
        if self._window_due is not None and self._window_due <= now:
            self._window_due = None
            self._check_window()

    def _handle(self, message: _Message) -> bool:
        """One message; True when the scheduler stops (close)."""
        match message:
            case _Call():
                try:
                    message.value = message.fn()
                except Exception as exc:  # noqa: BLE001 (a bug: logged; the caller gets its fallback, never None)
                    self._log_internal(None, exc)
                    message.value = message.fallback
                return message.fn == self._close
            case _Feed(event=event):
                self._step(event)
            case _Spawned(serial=serial):
                if self._state.engine is not None:
                    self._number_of[serial] = self._state.engine
                self._settle_slot()
            case _Ended(end=end):
                self._log_exit(end)
                self._settle_slot()
            case _Settle():
                self._settle_slot()
            case _JobFailed():
                self._job_failed(message)
            case _Phase(job_id=job_id, phase=phase):
                job = self._jobs.get(job_id)
                if job is not None:
                    job.shown = Shown(phase, self._now())
            case _WorkReturned():
                self._work_returned(message)
        return False

    def _step(self, event: Event) -> tuple[Effect, ...]:
        """One table step and its effects; then the steps they called for, and the slot's books."""
        step = transition(self._policy, self._state, event)
        self._state = step.state
        for effect in step.effects:  # a job's worker learns that its job has ended before its engine is signalled
            ended = isinstance(effect, (Cancelled, EngineLost, NoEngine))
            if ended and (job := self._jobs.get(effect.job_id)) is not None and job.worker is not None:
                job.worker.end()
        for effect in step.effects:
            # Effects must not raise; one that does (a bug) is logged and the step's other effects still happen, so
            # no step is left half carried out after its state was taken (S0058 verifier F1).
            try:
                self._effect(effect)
            except Exception as exc:  # noqa: BLE001 (the rule above)
                self._log_internal(None, exc)
        while self._follow:
            self._step(self._follow.pop(0))
        self._settle_slot()
        return step.effects

    def _settle_slot(self) -> None:
        """The slot against the books: an idle engine whose end was reported died (`EngineDied` by its number, §5.3);
        an ending slot with no engine left undrained and no start under way has ended (`EngineEnded`, §5.2)."""
        state = self._state
        with self._books.lock:
            live = bool(self._books.live) or self._books.starting > 0
            idle_died = state.slot == "idle" and self._books.current_ended
        if idle_died and state.engine is not None:
            self._step(EngineDied(state.engine))
        elif state.slot == "ending" and not live:
            self._step(EngineEnded())

    def _publish(self) -> None:
        keep = {self._state.engine, *self._number_of.values()}
        for number in [n for n in self._kind_on if n not in keep]:
            del self._kind_on[number]  # INV-10: only the engines still in the books
        held = {j.id for j in self._state.queued}
        if self._state.running is not None:
            held.add(self._state.running.job.id)
        for job_id in [i for i, j in self._jobs.items() if i not in held and j.ticket.outcome is not None]:
            del self._jobs[job_id]
        self._status = JobsStatus(self._state, self._policy, {i: j.shown for i, j in self._jobs.items() if i in held})

    # -- requests on the scheduler thread ---------------------------------------------------------------------

    def _boot(self) -> JobTicket | Failed:
        self._step(Boot())
        spec = JobSpec(kind="recover_all", workspace=None, waited_for=True, confirmed=False, owner="switch")
        return self._submit(spec, self._recover_all or _recover_nothing, boot=True)

    def _submit(self, spec: JobSpec, work: Work, *, boot: bool) -> JobTicket | Failed:
        self._submitting = (spec, work, boot)
        try:
            effects = self._step(Submit(spec))
        finally:
            self._submitting = None
        for effect in effects:
            match effect:
                case Queued(job=job):
                    return self._jobs[job.id].ticket
                case AlreadyQueued(job_id=job_id):
                    return self._jobs[job_id].ticket
                case Refused(code="WORKSPACE_NOT_OPEN"):
                    details = WorkspaceNotOpenDetails(open_workspace=self._state.open_workspace, switching=False)
                    return Failed(error(ErrorCode.WORKSPACE_NOT_OPEN, details))
                case Refused():
                    return Failed(error(ErrorCode.ENGINE_QUEUE_FULL))
                case _:
                    pass
        msg = "the queue answered a Submit with neither Queued nor a refusal"  # pragma: no cover (the table's contract)
        raise RuntimeError(msg)

    def _cancel(self, job_id: int) -> CancelAnswer:
        effects = self._step(Cancel(job_id))
        if any(isinstance(e, Cancelled | JournalCancelled) and e.job_id == job_id for e in effects):
            return "cancelled"
        if TooLate(job_id) in effects:
            return "too_late"
        return "nothing"

    def _available(self) -> None:
        self._window_due = None
        self._step(EngineAvailability(available=True))

    def _close(self) -> None:
        closing = self._closing()
        running = self._state.running
        for job_id, job in list(self._jobs.items()):
            if running is not None and running.job.id == job_id and running.past_commit:
                continue  # it finishes (§4); "not done" would be untrue, and the app is quitting (§9.3, BL-0154)
            job.box.answer(closing)
            if job.worker is not None:
                job.worker.end()

    def _closing(self) -> Failed:
        return Failed(error(ErrorCode.ENGINE_UNAVAILABLE, EngineUnavailableDetails(reason="closing")))

    # -- effects ----------------------------------------------------------------------------------------------

    def _effect(self, effect: Effect) -> None:
        match effect:
            case Queued(job=job):
                if self._submitting is not None:
                    spec, work, boot = self._submitting
                    box = _Box()
                    ticket = JobTicket(job.id, spec.kind, box)
                    self._jobs[job.id] = _Job(ticket, box, work, Shown(None, self._now()), boot)
            case Start():
                self._start(effect)
            case Proceed(job_id=job_id):
                if (job := self._jobs.get(job_id)) is not None:
                    job.past_commit = True
                self._engine.proceed()
            case ResetEngine():
                threading.Thread(target=self._reset, name="engine-reset", daemon=True).start()
            case EndEngine(reason=reason):
                if reason != "crashed":  # a dead engine needs no signal; the client never signals after the reap
                    self._engine.end(reason)
            case Cancelled(job_id=job_id, cannot_stop_ahead=cannot):
                details = JobCancelledDetails(cannot_stop_ahead=cannot)
                self._finish(job_id, Failed(error(ErrorCode.JOB_CANCELLED, details)))
            case EngineLost(job_id=job_id):
                self._finish(job_id, self._lost_outcome(job_id))
            case NoEngine(job_id=job_id, why=why):
                reason = "cannot_start" if why == "lock_failed" else self._unavailable_reason
                self._finish(job_id, Failed(error(ErrorCode.ENGINE_UNAVAILABLE, EngineUnavailableDetails(reason))))
            case EngineRefused(job_id=job_id, why=why):
                job = self._jobs.get(job_id)
                if job is not None and job.worker is not None:
                    kind: FailureKind = "start_failed" if why == "lock_failed" else "unavailable"
                    job.worker.refuse(EngineFailure(kind, f"no engine for the job's next part ({why})"))
            case Finished(job_id=job_id):
                job = self._jobs.get(job_id)
                if job is not None:
                    self._finish(job_id, Failed(error(ErrorCode.INTERNAL)) if job.raised else Done(job.value))
            case WaitEngineLock():
                threading.Thread(target=self._wait_lock, name="engine-lock-wait", daemon=True).start()
            case ArmAdviceTimer(epoch=epoch):
                self._advice_due = (self._now() + self._delay, epoch)
            case _:
                pass  # ShowAdvice, HideAdvice (published with the state), TooLate, AlreadyQueued, Refused

    def _start(self, start: Start) -> None:
        job = self._jobs.get(start.job.id)
        if job is None:  # pragma: no cover (the table starts only jobs it queued here)
            return
        self._kind_on[start.engine] = start.job.kind
        job.shown = Shown(None if start.reuse else "starting", self._now())
        if not start.reuse:
            with self._books.lock:
                self._books.starting += 1  # until the worker's start has returned (`_Worker.begin`)
        if job.worker is not None:
            job.worker.granted(start)  # a fresh engine for the job's next part
            return
        job.worker = _Worker(self._engine, self._post, self._books, start.job.id, start.job.kind)
        threading.Thread(target=self._run, args=(job.worker, job.work, start), name="engine-job", daemon=True).start()

    def _finish(self, job_id: int, outcome: Outcome) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.box.answer(outcome)
        if job.worker is not None:
            job.worker.end()
        if job.boot:
            self._follow.append(ExclusiveEnded())  # §3, §9.2: the boot ends with its job (its open: BL-0154)

    def _lost_outcome(self, job_id: int) -> Failed:
        job = self._jobs.get(job_id)
        kind = job.failure.kind if job is not None and job.failure is not None else "crashed"
        if kind in ("unavailable", "incompatible"):
            reason = "incompatible" if kind == "incompatible" else "stopped_often"
            return Failed(error(ErrorCode.ENGINE_UNAVAILABLE, EngineUnavailableDetails(reason)))
        if kind == "closed":
            return self._closing()
        if job is not None and job.past_commit:
            # §5.3 "a save's outcome is resolved from disk (§6.6)": past its commit point a writing job may have
            # changed files, so it is never told "Your ledger files are safe" (S0058 verifier F5).
            # TODO(BL-0151): the outcome check of §6.6 replaces this answer with what the disk says.
            return Failed(error(ErrorCode.CHANGE_OUTCOME_UNKNOWN))
        # TODO(BL-0094): `killed_by_system` once the end report's cause is told apart (failure-modes E4).
        return Failed(error(ErrorCode.ENGINE_STOPPED, EngineStoppedDetails(reason="crashed")))

    # -- the job's worker thread --------------------------------------------------------------------------------

    def _run(self, worker: _Worker, work: Work, start: Start) -> None:
        if worker.begin(start) is not None:
            return  # the failed start was reported by the context
        try:
            result: WorkResult | None = work(JobContext(worker))
        except Exception as exc:  # noqa: BLE001 (the job runner: ARCHITECTURE §6.3; logged, the job ends INTERNAL)
            self._log_internal(worker.job_id, exc)
            result = None
        self._post(_WorkReturned(worker.job_id, result))

    def _work_returned(self, message: _WorkReturned) -> None:
        job = self._jobs.get(message.job_id)
        running = self._state.running
        if job is None or running is None or running.job.id != message.job_id:
            return  # the job already ended (Cancel, its engine lost): its late answer is dropped
        result = message.result
        job.raised = result is None
        job.value = None if result is None else result.value
        self._step(JobDone(message.job_id, ran_user_code=result is None or result.ran_user_code))

    def _job_failed(self, message: _JobFailed) -> None:
        job = self._jobs.get(message.job_id)
        running = self._state.running
        if job is None or running is None or running.job.id != message.job_id:
            return
        job.failure = message.failure
        self._read_availability()
        engine = self._state.engine
        if self._state.slot == "busy" and engine is not None:
            self._step(EngineDied(engine))

    # -- the engine lock and availability ---------------------------------------------------------------------

    def _wait_lock(self) -> None:
        try:
            self._wait_engine_lock()
        except OSError as exc:
            with contextlib.suppress(Exception):
                detail = f"the engine lock could not be waited for: {exc}"
                self._log(ENGINE_START_FAILED, kind="start_failed", detail=detail)
            self._post(_Feed(EngineLockFailed()))
        else:
            self._post(_Feed(EngineLockFree()))

    def _reset(self) -> None:
        """Try again's reset, beside the scheduler thread: the client's reset waits for its call lock, which a call to
        an engine that has not ended yet can hold."""
        try:
            self._engine.reset()
        except Exception as exc:  # noqa: BLE001 (the job runner's rule: logged; the engine is tried again anyway)
            self._log_internal(None, exc)
        self._post(_Call(self._available))

    def _read_availability(self) -> None:
        state = self._engine.state
        if isinstance(state, Incompatible):
            self._unavailable_reason = "incompatible"
            self._step(EngineAvailability(available=False))
        elif isinstance(state, Unavailable):
            self._unavailable_reason = "stopped_often"
            self._step(EngineAvailability(available=False))
            self._arm_window(state)

    def _arm_window(self, state: Unavailable) -> None:
        stops = counted(self._restart, state.stops, self._now())
        if len(stops) < self._restart.max_crashes:
            self._window_due = self._now()
        else:
            self._window_due = stops[0] + self._restart.window

    def _check_window(self) -> None:
        state = self._engine.state
        if isinstance(state, Incompatible):
            return  # until Try again
        if isinstance(state, Unavailable):
            stops = counted(self._restart, state.stops, self._now())
            if len(stops) >= self._restart.max_crashes:
                self._window_due = stops[0] + self._restart.window
                return
        self._step(EngineAvailability(available=True))

    # -- the log ------------------------------------------------------------------------------------------------

    def _log_exit(self, end: EngineEnd) -> None:
        number = self._number_of.pop(end.serial, None)
        kind = self._kind_on.get(number) if number is not None else None
        code = end.returncode
        with contextlib.suppress(Exception):
            self._log(
                ENGINE_EXIT,
                engine=number,
                job=kind,
                reason=end.reason or "none",
                exit_code=code if code >= 0 else None,
                signal=-code if code < 0 else None,
                max_rss_kib=end.max_rss_kib,
            )

    def _log_internal(self, job_id: int | None, exc: Exception) -> None:
        with contextlib.suppress(Exception):
            self._log(JOB_INTERNAL_ERROR, job=job_id, exception_type=type(exc).__name__, exception=exc)


def _recover_nothing(ctx: JobContext) -> WorkResult:
    """The boot's recover-all when the composition root gives none (tests of the queue alone): nothing to list; its
    engine (the session's first) ran no user code and serves the next job (§5.1). The product's is
    `app.platform.engine_jobs.recovery.recover_all_work` (S0062)."""
    return WorkResult(None, ran_user_code=False)
