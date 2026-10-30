"""Model-driven conformance: the TLA+ model's traces replayed through the queue table and the scheduler shell (S0059;
BL-0150; `design/specs/engine-jobs.md` §3-§5, §13, §14; ADR-0064).

`formal/tla/engine-jobs/traces/corpus.json` holds TLC-simulated traces of small worlds of `EngineJobs.tla`
(`gen_traces.py` there writes it; each trace is a behaviour of `Spec`, with the app's own dispatch taken first when
it is enabled, as the table takes it inside the step that allows it). Each step is turned into what the code sees
(`ops`): a request (submit, Cancel, a closed tab, Try again), an engine report (a crash, an end drained), a time
event (the display delay, the restart window), the engine lock coming free, or the running job's own part (its end,
its commit point, its ask for a fresh engine). The same ops drive two replays:

- **the table** (`queue.transition`, fed the events the shell would feed it, with the shell's two follow-ups: the
  reset done after `ResetEngine`, and the boot's window ended with its job);
- **the shell** (`EngineJobs` over the fake engine port, with a clock that moves only when the replay moves it, a
  lock wait the replay opens, and a scripted work per job).

After every step that is not followed by one of the model's dispatch steps (the table is one step ahead until those
have run), the queue's state is projected (`View`) and must equal the model's projection of the same state, or a
state the projection says is equivalent (`same`); every job that has ended must have been answered as the model ended
it (`End`), and as many fresh engines must have started (§5.1: an engine that ran only parse-only work is reused):

- the quit advice: the model's `cancel` advice (the Cancel's "Your request is cancelled..." message, an effect in the
  code) says nothing about the timer's advice; and the shell may show the timer's advice before the model's
  `AdviceTimer` step when that step is enabled (the model's timer is a delay of any length; the shell's clock moves
  for every timer at once);
- a Try again's reset under way counts as unavailable (the model's Try again is one step);
- the engine's number, the epoch, the engine lock's own state and the switch's window are not compared: what they
  decide (which job starts, on which engine, when) is.

`inventory()` classifies every action of the model's `Next` (checked against the model) as mapped (BL-0150's own), a
later piece's engine step projected through the job-step rule, a later piece's app step played through the shell's
seam, projected away (no effect on the queue; checked per step), or left out of the traces (`gen_traces.OUT`).
A few branches stop their trace (`STOPS`): behaviour of a later piece that the code does not have yet; the walk
leaves four of them out (`gen_traces.GUARDS`), the fifth is only absent (see `STOPS`), and the coverage test checks
that none is reached.

What this covers today: the action names of BL-0150 over 62 traces of small worlds, not every branch of them (S0059
verifier F1, F3, F4): a Cancel meeting a background save past its commit point, a Cancel while an R4 runs,
`SpawnStep`'s unavailable branch and `OrphanEndStuck` (mapped, never replayed) are not in the corpus, and each world
has one workspace, so a job of a workspace that is not open is never seen (the switch is BL-0154). BL-0159 widens the
corpus by branch with BL-0151. The hand-written suites (`test_engine_queue.py`, `test_engine_scheduler.py`) remain
the main guard for these rules; this replay checks the code against the model's behaviour as a whole.
"""

from __future__ import annotations

import faulthandler
import hashlib
import json
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Literal, TypedDict, cast

import pytest

from app.domain.errors import DomainError
from app.platform.engine import EngineFailure
from app.platform.engine_jobs import queue, scheduler
from app.platform.engine_jobs.queue import (
    AdviceTimer,
    AlreadyQueued,
    Boot,
    Cancel,
    Cancelled,
    ClientGone,
    EngineAvailability,
    EngineDied,
    EngineEnded,
    EngineLockFree,
    EngineLost,
    EngineNeeded,
    Event,
    ExclusiveEnded,
    Finished,
    Gate,
    Job,
    JobDone,
    JobKind,
    JobSpec,
    NoEngine,
    Owner,
    Queued,
    QueuePolicy,
    QueueState,
    ResetEngine,
    RetireEngine,
    Start,
    Submit,
    TryAgain,
    WaitEngineLock,
    WorkspaceOpened,
    eligible,
    transition,
)
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobContext, JobTicket, WorkResult
from app.platform.engine_supervision import RestartPolicy
from app.platform.errors import ErrorCode, JobCancelledDetails
from tests.architecture.test_tla_mapping import next_groups, step_actions
from tests.platform.fake_job_engine import FakeEngine

REPO = Path(__file__).resolve().parents[3]
DIR = REPO / "formal" / "tla" / "engine-jobs"
MODEL = DIR / "EngineJobs.tla"
GENERATOR = DIR / "gen_traces.py"
CORPUS = DIR / "traces" / "corpus.json"
LOCK = REPO / "formal" / "tla" / "tools.lock"
REGENERATE = "python3 formal/tla/engine-jobs/gen_traces.py"

DEADLINE = 10.0  # a failure guard on each settle, never a pass condition
DELAY = 2.0
WINDOW = 300.0
SPAWN_PCS = frozenset({"s_spawn", "r4_spawn", "rc_spawn"})
EAGER = frozenset({"Dispatch", "DispatchUnavail", "SpawnStep"})

SAVE, R4_READS, CREATE_COPY, SWITCH = "BL-0151", "BL-0152", "BL-0153", "BL-0154"

# -- the inventory: every action of the model's Next ---------------------------------------------------------------

type Class = Literal["mapped", "job_step", "seam", "away", "out"]


def names(text: str) -> tuple[str, ...]:
    return tuple(text.split())


MAPPED = (
    "Dispatch",  # the dispatch inside the step that allows it (§3); `Start`, the engine lock waited for first
    "DispatchUnavail",  # the queued jobs end `NoEngine` while the engine is unavailable (§5.3)
    "SpawnStep",  # the running job's fresh engine, or its end while unavailable (§5.1)
    "FreshEnd",  # the job's `fresh_engine()`: `RetireEngine`, `EngineNeeded`
    "AdviceTimer",  # the display delay passes (§5.4): the clock moves, `AdviceTimer`
    "Drain",  # the ended engine's group is drained: its end report, `EngineEnded` (§5.2)
    "OrphanEnd",  # the earlier session's engine ends: the lock wait returns, `EngineLockFree` (§5.1)
    "OrphanEndStuck",  # mapped, never replayed: its stuck orphan needs a quit (left out); BL-0159
    "WindowExpire",  # the restart window lets the bound go: `EngineAvailability(True)` (§5.3)
    "UserTryAgain",  # `try_again()`: `TryAgain`, `ResetEngine`, `EngineAvailability(True)`
    "UserCancelQueued",  # `cancel()` of a queued job (§4)
    "UserCancelRunning",  # `cancel()` of the running job (§4)
    "UserCloseTab",  # `client_gone()` (§4)
    "UserSave",  # `submit()`
    "UserRead",
    "AppBgSave",  # `submit_background_save()`
    "EngineCrash",  # the running job's engine dies: its failed call, `EngineDied`, the drain; the bound read first
    "Hang",  # the job takes no step (nothing to do: the scripted work waits)
    "Stuck",  # a member never ends: the drain's report never comes (nothing to do: ends are held until a Drain)
)
JOB_STEPS: Mapping[str, str] = {
    **{
        a: SAVE
        for a in names(
            "S_R4Check S_WaitR4 S_Locks S_Gate S_Load S_Recheck S_Commit S_Backup S_BackupSync S_JPart S_JPartSync "
            "S_JOpen S_JDirSync S_TempStart S_TempDone S_TempSync S_Swap S_Compare S_DirSync S_ReadBack "
            "S_RemoveTemps S_Close S_CloseSync S_Saved S_Publish S_Release RB_Check RS_Write RS_Swap RS_Compare "
            "RS_Temps RS_Sync RS_Resolve RC_Init RC_Next RC_Lock RC_Check IOFail"
        )
    },
    **{
        a: R4_READS
        for a in names("R4_Pick R4_Start R4_Load R4_Check R4_Confirm R4_Sync RD_Load RD_Check OP_Load OP_Check")
    },
}
SEAMS: Mapping[str, str] = {
    "Assess": SWITCH,  # the boot's open: `workspace_opened()`; an open job stops the trace (BL-0152)
    "AppFocusCheck": R4_READS,  # the rebuild the focus check queues: `submit()`
}
AWAY: Mapping[str, str] = {
    "Editor": "env",
    "EditorDrop": "env",
    "EditorOverwrite": "env",
    "PathChange": "env",
    "Unreach": "env",
    "UserResume": SAVE,  # a record's rename (§6.5)
    "UserForgetReadd": SAVE,  # the registry key (§6.5)
}
OUT: Mapping[str, str] = {
    **{
        a: CREATE_COPY
        for a in names(
            "UserCreate UserCopy CP_Check CP_Load CP_Commit CP_Manifest CR_Target CR_WriteA CR_SyncA CR_ReadBack "
            "CR_Load CR_Commit CR_ManifestTemp CR_ManifestRename CR_ManifestSync CR_Register"
        )
    },
    **{
        a: SWITCH
        for a in names(
            "UserSwitch UserStay UserRetry UserBack UserOpenAnother UserKeep UserQuit UserQuitAnyway Relaunch "
            "CloseA QuitClose AppCrash PowerLoss"
        )
    },
}


def inventory() -> dict[str, Class]:
    classes: dict[str, Class] = {}
    groups: tuple[tuple[Iterable[str], Class], ...] = (
        (MAPPED, "mapped"), (JOB_STEPS, "job_step"), (SEAMS, "seam"), (AWAY, "away"), (OUT, "out"),
    )  # fmt: skip
    for group, cls in groups:
        for name in group:
            assert name not in classes, f"{name} is classified twice"
            classes[name] = cls
    return classes


# -- the corpus ------------------------------------------------------------------------------------------------------


class JobJ(TypedDict):
    id: int
    k: str
    w: int
    wf: bool
    conf: bool
    req: str
    own: str


class EngJ(TypedDict):
    st: str


class CtlJ(TypedDict):
    pc: str
    past: bool
    rinit: bool


class SesJ(TypedDict):
    app: str
    stops: int
    advice: str
    opened: list[int]


class PstJ(TypedDict):
    orphan: bool


class ActJ(TypedDict):
    a: str
    p: int | str


class StateJ(TypedDict):
    act: ActJ
    q: list[JobJ]
    run: JobJ | None
    eng: EngJ
    ctl: CtlJ
    ses: SesJ
    pst: PstJ


class TraceJ(TypedDict):
    world: str
    trace: int
    states: list[StateJ]


class Pin(TypedDict):
    sha256: str


class Header(TypedDict):
    model: Pin
    generator: Pin
    tools: Pin
    cfg: dict[str, Pin]
    seed: int
    worlds: dict[str, dict[str, object]]
    eager: list[str]
    out: list[str]
    command: str


class Meta(TypedDict):
    header: Header
    constants: dict[str, dict[str, str]]


@cache
def corpus() -> tuple[Meta, tuple[TraceJ, ...]]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    return cast(Meta, json.loads(lines[0])), tuple(cast(TraceJ, json.loads(line)) for line in lines[1:])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def staleness(header: Header) -> list[str]:
    """What the corpus was made from, against the model, the worlds, the generator and the tools now."""
    tools = next(line.split()[2] for line in LOCK.read_text().splitlines() if line.startswith("tla2tools.jar"))
    now = {
        "model": {"sha256": sha256(MODEL)},
        "generator": {"sha256": sha256(GENERATOR)},
        "tools": {"sha256": tools},
        "cfg": {w: {"sha256": sha256(DIR / "cfg" / f"{w}.cfg")} for w in header["cfg"]},
    }
    return [k for k, v in now.items() if header.get(k) != v]


# -- the projection --------------------------------------------------------------------------------------------------

KIND: Mapping[str, JobKind] = {
    "read": "preview",
    "rebuild": "rebuild",
    "save": "save",
    "r4": "r4",
    "recover": "recover_all",
    "open": "open",
    "create": "create",
    "copy": "copy",
}
SLOT: Mapping[str, str] = {"none": "none", "idle": "idle", "busy": "busy", "sig": "ending"}


@dataclass(frozen=True, slots=True)
class JobView:
    id: int
    kind: str
    workspace: str | None
    waited_for: bool
    confirmed: bool
    owner: str
    present: bool


@dataclass(frozen=True, slots=True)
class RunView:
    job: JobView
    past_commit: bool
    wants_engine: bool


@dataclass(frozen=True, slots=True)
class View:
    queued: tuple[JobView, ...]
    running: RunView | None
    slot: str
    unavailable: bool
    advice: bool | None  # None: the model says nothing about it (its `cancel` advice)
    open_workspace: str | None


def spec_of(j: JobJ) -> JobSpec:
    owner = cast(Owner, j["own"])
    return JobSpec(KIND[j["k"]], None if j["w"] == 0 else str(j["w"]), j["wf"], j["conf"], owner)


def model_view(s: StateJ, ids: Mapping[int, int], bound: int) -> View:
    def job(j: JobJ) -> JobView:
        sp = spec_of(j)
        return JobView(
            ids[j["id"]], sp.kind, sp.workspace, sp.waited_for, sp.confirmed, sp.owner, j["req"] == "present"
        )

    run = s["run"]
    running = None if run is None else RunView(job(run), s["ctl"]["past"], s["ctl"]["pc"] in SPAWN_PCS)
    advice = {"timer": True, "none": False}.get(s["ses"]["advice"])
    opened = s["ses"]["opened"]
    return View(
        tuple(job(j) for j in s["q"]),
        running,
        SLOT[s["eng"]["st"]],
        s["ses"]["stops"] >= bound,
        advice,
        str(opened[0]) if opened else None,
    )


def code_view(state: QueueState) -> View:
    def job(j: Job) -> JobView:
        return JobView(j.id, j.kind, j.workspace, j.waited_for, j.confirmed, j.owner, j.requester_present)

    r = state.running
    running = None if r is None else RunView(job(r.job), r.past_commit, r.wants_engine)
    unavailable = state.unavailable or state.resetting  # Try again's reset under way: not available yet
    return View(
        tuple(job(j) for j in state.queued), running, state.slot, unavailable, state.advice, state.open_workspace
    )


def advice_timer_enabled(s: StateJ) -> bool:
    """The model's `AdviceTimer` guard (MUTANT = "none")."""
    return (
        s["ses"]["app"] != "dead" and s["ses"]["advice"] == "none" and (s["eng"]["st"] == "sig" or s["pst"]["orphan"])
    )


def same(model: View, code: View, s: StateJ, *, early_advice: bool) -> bool:
    """The code's view equals the model's, but for the advice's two equivalences (the module's docstring)."""
    early = early_advice and code.advice and advice_timer_enabled(s)
    if model.advice is not None and model.advice != code.advice and not early:
        return False
    return replace(model, advice=None) == replace(code, advice=None)


# -- how a job ended -------------------------------------------------------------------------------------------------

type EndKind = Literal["finished", "cancelled", "lost", "no_engine", "other"]


@dataclass(frozen=True, slots=True)
class End:
    """How a job ended, as its requester is told: its value (`finished`), `JOB_CANCELLED` (with or without "the earlier
    ledger work cannot be stopped from here"), its engine's death (`ENGINE_STOPPED`, or `CHANGE_OUTCOME_UNKNOWN` past
    its commit point), or `ENGINE_UNAVAILABLE` (no engine). `cannot_stop_ahead` None: the model does not say.
    `past_commit` (a lost job only): its engine died past its commit point, so the user is told the outcome is being
    checked (`CHANGE_OUTCOME_UNKNOWN`), never "your files are safe" (`ENGINE_STOPPED`; §5.3, §6.6; S0059 verifier F2);
    None where the code's side does not say (the table's `EngineLost`: the shell picks the code)."""

    kind: EndKind
    cannot_stop_ahead: bool | None = None
    past_commit: bool | None = None


def matches(want: End, got: End | None) -> bool:
    if got is None or got.kind != want.kind:
        return False
    if want.past_commit is not None and got.past_commit is not None and want.past_commit != got.past_commit:
        return False
    return want.cannot_stop_ahead is None or want.cannot_stop_ahead == got.cannot_stop_ahead


def ends_of(s: StateJ, t: StateJ, classes: Mapping[str, Class]) -> dict[int, End]:
    """The jobs the model's step ended (by model id), and how. A Cancel's "cannot be stopped from here" is the
    model's `cancel` advice (§4), read only where the advice was not already showing."""
    a = t["act"]["a"]
    left = used(s) - used(t)
    run = s["run"]
    out: dict[int, End] = {}
    for mid in left:
        if a == "EngineCrash" and run is not None and mid == run["id"]:
            out[mid] = End("lost", past_commit=s["ctl"]["past"])  # only a writing job has a commit point
        elif classes[a] == "job_step":
            out[mid] = End("finished")
        elif a in ("DispatchUnavail", "SpawnStep"):
            out[mid] = End("no_engine")
        elif a == "UserCancelQueued" and mid == s["q"][int(t["act"]["p"]) - 1]["id"]:
            told = None if s["ses"]["advice"] == "cancel" else t["ses"]["advice"] == "cancel"
            out[mid] = End("cancelled", told)
        elif a in ("UserCancelQueued", "UserCancelRunning", "UserCloseTab"):
            out[mid] = End("cancelled", False)
        else:
            msg = f"{a} ended job {mid}, and the replay does not know how"
            raise AssertionError(msg)
    return out


def fresh_start(s: StateJ, t: StateJ) -> bool:
    """§5.1: the step started a job on a fresh engine (an idle engine that ran only parse-only work is reused)."""
    a = t["act"]["a"]
    if a == "Dispatch":
        return s["eng"]["st"] != "idle"
    return a == "SpawnStep" and t["run"] is not None and t["ctl"]["pc"] not in SPAWN_PCS


# -- the ops: what the code sees of one model step -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubmitOp:
    job: JobJ
    background_save: bool = False


@dataclass(frozen=True, slots=True)
class CancelOp:
    model_id: int


@dataclass(frozen=True, slots=True)
class GoneOp:
    model_id: int


@dataclass(frozen=True, slots=True)
class WorkOp:
    """The running job's own part: its end (`done`, with whether it ran user code), its commit point (`gate`), its
    ask for a fresh engine (`fresh`), or its engine's death mid-request (`crash`, with the bound reached or not)."""

    what: Literal["done", "gate", "fresh", "crash"]
    flag: bool = False


@dataclass(frozen=True, slots=True)
class SimpleOp:
    what: Literal["drain", "lock_free", "timer", "lift", "try_again"]


@dataclass(frozen=True, slots=True)
class OpenedOp:
    workspace: str


type Op = SubmitOp | CancelOp | GoneOp | WorkOp | SimpleOp | OpenedOp


STOPS: Mapping[str, str] = {
    # Built in S0062 (the queue's `RecoveryListed`, `JournalCancelled`, `EngineRefused`), but not walked by this
    # corpus yet: `gen_traces.GUARDS` still leaves them out; widening it is the save path's replay (BL-0151 slice 5).
    "recover_spawn": f"recover-all goes on with the next journal in a fresh engine ({SAVE}, slice 5's replay)",
    "recover_cancel": f"a Cancel of recover-all after its listing ends only its engine ({SAVE}, slice 5's replay)",
    "recover_crash": f"recover-all goes on with the next journal after its engine's death ({SAVE}, slice 5's replay)",
    "open_crash": f"the open's engine death ends the switch on its failed screen ({R4_READS})",
    "open_job": f"the boot's open of a workspace whose mirror is out of date runs an open job ({R4_READS})",
}
"""Steps that stop their trace: behaviour of a later piece that the code does not have yet, or (the three `recover_`
ones, since S0062) that the corpus does not walk yet. `gen_traces.GUARDS`
leaves four of them out of the walk (`recover_cancel`, `recover_crash`, `open_crash`, `open_job`). `recover_spawn` is
not guarded: guarding `SpawnStep` would deadlock the walk (the trace module takes the dispatch whenever it is enabled),
so it is only absent from this corpus, its seed and its worlds (it follows only from `recover_crash` or
`recover_cancel`). The coverage test fails if a trace reaches any of them (S0059 verifier F5)."""


class Stop(Exception):
    """The trace ends here (`STOPS`)."""

    def __init__(self, code: str) -> None:
        super().__init__(STOPS[code])
        self.reason = STOPS[code]


def used(s: StateJ) -> set[int]:
    return {j["id"] for j in s["q"]} | ({s["run"]["id"]} if s["run"] is not None else set())


def new_jobs(s: StateJ, t: StateJ) -> list[JobJ]:
    """Jobs the step queued (the model reuses a free id; a new job's id is not in use before the step)."""
    old = used(s)
    return [j for j in t["q"] if j["id"] not in old]


def projected(s: StateJ) -> tuple[object, ...]:
    """What of a model state the projection reads (the queue, the running job, the engine, the bound, the advice,
    the open workspace, the engine lock)."""
    ses = s["ses"]
    return (
        s["q"], s["run"], s["eng"]["st"], s["ctl"]["past"], s["ctl"]["pc"] in SPAWN_PCS,
        ses["stops"], ses["advice"], ses["opened"], s["pst"]["orphan"],
    )  # fmt: skip


def ops_of(s: StateJ, t: StateJ, bound: int, classes: Mapping[str, Class]) -> list[Op]:
    """What the code sees of the model's step from `s` to `t` (labelled `t["act"]`)."""
    a, p = t["act"]["a"], t["act"]["p"]
    run = s["run"]
    cls = classes[a]
    if a in EAGER:
        if a == "SpawnStep" and s["ctl"]["pc"] == "rc_spawn":
            raise Stop("recover_spawn")
        return []  # the table took it inside the step before (§3)
    submits: list[Op] = [SubmitOp(j, background_save=a == "AppBgSave") for j in new_jobs(s, t)]
    match a:
        case "UserSave" | "UserRead" | "AppBgSave" | "AppFocusCheck":
            return submits
        case "UserCancelQueued":
            return [CancelOp(s["q"][int(p) - 1]["id"])]
        case "UserCancelRunning":
            assert run is not None
            if run["k"] == "recover" and s["ctl"]["rinit"]:
                raise Stop("recover_cancel")
            return [CancelOp(run["id"])]
        case "UserCloseTab":
            return [GoneOp(int(p))]
        case "UserTryAgain":
            return [SimpleOp("try_again")]
        case "EngineCrash":
            assert run is not None
            if run["k"] == "recover":
                raise Stop("recover_crash")
            if run["k"] == "open":
                raise Stop("open_crash")
            reached = s["ses"]["stops"] < bound <= t["ses"]["stops"]
            return [WorkOp("crash", reached), *submits]
        case "Drain":
            return [SimpleOp("drain")]
        case "OrphanEnd" | "OrphanEndStuck":
            return [SimpleOp("lock_free")]
        case "WindowExpire":
            return [SimpleOp("lift")] if s["ses"]["stops"] >= bound > t["ses"]["stops"] else []
        case "AdviceTimer":
            return [SimpleOp("timer")]
        case "FreshEnd":
            return [WorkOp("fresh")]
        case "Hang" | "Stuck":
            return []
        case "Assess":
            if any(j["k"] == "open" for j in new_jobs(s, t)):
                raise Stop("open_job")
            opened = t["ses"]["opened"]
            moved: list[Op] = [OpenedOp(str(opened[0]))] if opened and opened != s["ses"]["opened"] else []
            return [*moved, *submits]
        case _ if cls == "away":
            assert projected(s) == projected(t), f"{a} is classified as projected away but changed the queue's state"
            return []
        case _ if cls == "job_step":
            assert run is not None, f"{a} ran with no running job"
            if t["run"] is None:
                end = t["eng"]["st"]
                assert end in ("sig", "idle", "none"), f"{a} ended its job with the engine {end}"
                return [WorkOp("done", end == "sig"), *submits]
            if t["ctl"]["past"] and not s["ctl"]["past"]:
                return [WorkOp("gate"), *submits]
            assert (t["ctl"]["pc"] in SPAWN_PCS) == (s["ctl"]["pc"] in SPAWN_PCS), f"{a} moved between engines"
            assert t["eng"]["st"] == s["eng"]["st"], f"{a} changed the engine ({s['eng']['st']} -> {t['eng']['st']})"
            return submits
        case _:
            msg = f"{a} ({cls}) has no mapping"
            raise AssertionError(msg)


# -- the table replay ------------------------------------------------------------------------------------------------


class TableReplay:
    """`queue.transition` fed the events the shell feeds it, with the shell's follow-ups."""

    early_advice = False

    def __init__(self, policy: QueuePolicy, orphan: bool) -> None:
        self.policy = policy
        self.state = QueueState()
        self.ids: dict[int, int] = {}
        self.orphan = orphan
        self.lock_asked = False
        self.boot_id = 0
        self.ended: dict[int, End] = {}
        self.fresh = 0

    def boot(self, model_id: int) -> None:
        self.feed(Boot())
        self.boot_id = self.submit(JobSpec("recover_all", None, True, False, "switch"))
        self.ids[model_id] = self.boot_id

    def submit(self, spec: JobSpec) -> int:
        effects = self.feed(Submit(spec))
        for e in effects:
            if isinstance(e, Queued):
                return e.job.id
            if isinstance(e, AlreadyQueued):
                return e.job_id
        msg = f"the table did not queue {spec}: {effects}"
        raise AssertionError(msg)

    def feed(self, event: Event) -> list[object]:
        step = transition(self.policy, self.state, event)
        self.state = step.state
        effects: list[object] = list(step.effects)
        follow: list[Event] = []
        for e in step.effects:
            match e:
                case Cancelled(job_id=i, cannot_stop_ahead=cannot):
                    self.ended[i] = End("cancelled", cannot)
                case Finished(job_id=i):
                    self.ended[i] = End("finished")
                case EngineLost(job_id=i):
                    self.ended[i] = End("lost")
                case NoEngine(job_id=i):
                    self.ended[i] = End("no_engine")
                case Start(reuse=False):
                    self.fresh += 1
                case _:
                    pass
            if isinstance(e, WaitEngineLock):
                self.lock_asked = True
            elif isinstance(e, ResetEngine):
                follow.append(EngineAvailability(True))  # the reset, done
            elif isinstance(e, Cancelled | EngineLost | NoEngine | Finished) and e.job_id == self.boot_id:
                follow.append(ExclusiveEnded())  # the shell ends the boot's window with its job
        if self.lock_asked and not self.orphan:
            self.lock_asked = False
            follow.append(EngineLockFree())
        for event2 in follow:
            effects += self.feed(event2)
        return effects

    def running_id(self) -> int:
        r = self.state.running
        assert r is not None, "the table has no running job"
        return r.job.id

    def apply(self, op: Op) -> None:
        match op:
            case SubmitOp(job=j):
                self.ids[j["id"]] = self.submit(spec_of(j))
            case CancelOp(model_id=m):
                self.feed(Cancel(self.ids[m]))
            case GoneOp(model_id=m):
                self.feed(ClientGone(self.ids[m]))
            case WorkOp(what="done", flag=ran):
                self.feed(JobDone(self.running_id(), ran))
            case WorkOp(what="gate"):
                self.feed(Gate(self.running_id()))
            case WorkOp(what="fresh"):
                job_id = self.running_id()
                self.feed(RetireEngine(job_id))
                self.feed(EngineNeeded(job_id))
            case WorkOp(what="crash", flag=reached):
                if reached:
                    self.feed(EngineAvailability(False))
                assert self.state.engine is not None
                self.feed(EngineDied(self.state.engine))
                self.feed(EngineEnded())
            case SimpleOp(what="drain"):
                self.feed(EngineEnded())
            case SimpleOp(what="lock_free"):
                self.orphan = False
                if self.lock_asked:
                    self.lock_asked = False
                    self.feed(EngineLockFree())
            case SimpleOp(what="timer"):
                self.feed(AdviceTimer(self.state.epoch))
            case SimpleOp(what="lift"):
                self.feed(EngineAvailability(True))
            case SimpleOp(what="try_again"):
                self.feed(TryAgain())
            case OpenedOp(workspace=w):
                self.feed(WorkspaceOpened(w))
            case _:
                msg = f"the table replay has no {op}"
                raise AssertionError(msg)

    def view(self) -> View:
        return code_view(self.state)

    def ends(self) -> dict[int, End]:
        return dict(self.ended)

    def fresh_starts(self) -> int:
        return self.fresh

    def settle(self, done: Callable[[], bool]) -> None:
        pass

    def close(self) -> None:
        pass


# -- the shell replay ------------------------------------------------------------------------------------------------


class Script:
    """A job's work, told what to do by the replay: it waits in a `block` request between commands, so our end of
    its engine ends it wherever it is."""

    def __init__(self) -> None:
        self.commands: deque[tuple[str, bool]] = deque()

    def __call__(self, ctx: JobContext) -> WorkResult:
        while True:
            if isinstance(ctx.call("block", {}), EngineFailure):
                return WorkResult(None, ran_user_code=True)
            what, flag = self.commands.popleft()
            if what == "done":
                return WorkResult(None, ran_user_code=flag)
            if what == "gate":
                ctx.call("gate", {})
            elif what == "fresh":
                if ctx.fresh_engine() is not None:
                    return WorkResult(None, ran_user_code=True)
            else:
                ctx.call("crash", {})
                return WorkResult(None, ran_user_code=True)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ShellReplay:
    """`EngineJobs` over the fake engine port: the replay is the client's environment and the jobs' work."""

    early_advice = True

    def __init__(self, policy: QueuePolicy, orphan: bool, bound: int, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = FakeEngine(hold_ends=True)
        self.clock = Clock()
        self.lock_open = threading.Event()
        if not orphan:
            self.lock_open.set()
        self.bound = bound
        self.jobs = EngineJobs(
            self.engine,
            wait_engine_lock=self._wait_lock,
            policy=policy,
            restart=RestartPolicy(max_crashes=bound, window=WINDOW),
            display_delay=DELAY,
            monotonic=self.clock,
        )
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        self.ids: dict[int, int] = {}
        self.scripts: dict[int, Script] = {}
        self.tickets: dict[int, JobTicket] = {}
        self.boot_script = Script()
        monkeypatch.setattr(scheduler, "_recover_nothing", self.boot_script)  # recover-all's work is BL-0151's

    def _wait_lock(self) -> None:
        assert self.lock_open.wait(60.0), "the replay never opened the engine lock"

    def boot(self, model_id: int) -> None:
        ticket = self.jobs.start()
        assert isinstance(ticket, JobTicket)
        self.ids[model_id] = ticket.id
        self.scripts[ticket.id] = self.boot_script
        self.tickets[ticket.id] = ticket

    def running_script(self) -> Script:
        r = self.jobs.status().queue.running
        assert r is not None, "the shell has no running job"
        return self.scripts[r.job.id]

    def command(self, what: str, flag: bool = False) -> None:
        self.running_script().commands.append((what, flag))
        self.engine.release("block")

    def apply(self, op: Op) -> None:
        match op:
            case SubmitOp(job=j, background_save=bg):
                script = Script()
                spec = spec_of(j)
                if bg:
                    assert spec.workspace is not None
                    ticket = self.jobs.submit_background_save(spec.workspace, script)
                else:
                    ticket = self.jobs.submit(spec, script)
                assert isinstance(ticket, JobTicket), f"the shell refused {spec}: {ticket}"
                self.ids[j["id"]] = ticket.id
                self.scripts.setdefault(ticket.id, script)
                self.tickets[ticket.id] = ticket
            case CancelOp(model_id=m):
                self.jobs.cancel(self.ids[m])
            case GoneOp(model_id=m):
                self.jobs.client_gone(self.ids[m])
            case WorkOp(what="crash", flag=reached):
                if reached:
                    self.engine.unavailable((self.clock.now,) * self.bound)
                held = self.engine.held
                self.command("crash")
                until(lambda: self.engine.held > held, "the crashed engine's end report")
                self.engine.release_ends()  # the model drains a crashed engine at once
            case WorkOp(what="gate"):
                self.command("gate")
                self.engine.release("gate")
            case WorkOp(what=what, flag=flag):
                self.command(what, flag)
            case SimpleOp(what="drain"):
                self.engine.release_ends()
            case SimpleOp(what="lock_free"):
                self.lock_open.set()
            case SimpleOp(what="timer"):
                self.clock.now += DELAY
                self.jobs.tick()
            case SimpleOp(what="lift"):
                self.engine.available()
                self.clock.now += WINDOW
                self.jobs.tick()
            case SimpleOp(what="try_again"):
                self.jobs.try_again()
            case OpenedOp(workspace=w):
                self.jobs.workspace_opened(w)
            case _:
                msg = f"the shell replay has no {op}"
                raise AssertionError(msg)

    def view(self) -> View:
        return code_view(self.jobs.status().queue)

    def ends(self) -> dict[int, End]:
        """How each job that ended was answered (its ticket's outcome)."""
        out: dict[int, End] = {}
        for job_id, ticket in self.tickets.items():
            outcome = ticket.outcome
            if isinstance(outcome, Done):
                out[job_id] = End("finished")
            elif isinstance(outcome, Failed):
                out[job_id] = end_of(outcome.error)
        return out

    def fresh_starts(self) -> int:
        return self.engine.starts

    def settle(self, done: Callable[[], bool]) -> None:
        """Until the shell shows a state the model allows; the deadline only guards against a hang."""
        end = time.monotonic() + DEADLINE
        pause = threading.Event()
        while time.monotonic() < end:
            self.jobs.tick()  # a barrier through the scheduler's inbox
            if done():
                return
            pause.wait(0.001)

    def close(self) -> None:
        """No thread is left waiting: the lock wait returns, the live engine ends (its job's `block` returns)."""
        self.jobs.close()
        self.lock_open.set()
        self.engine.end("quit_anyway")
        self.engine.release_ends()


def end_of(err: DomainError) -> End:
    match err.code:
        case ErrorCode.JOB_CANCELLED:
            details = err.details
            assert isinstance(details, JobCancelledDetails)
            return End("cancelled", details.cannot_stop_ahead)
        case ErrorCode.ENGINE_STOPPED:
            return End("lost", past_commit=False)
        case ErrorCode.CHANGE_OUTCOME_UNKNOWN:
            return End("lost", past_commit=True)
        case ErrorCode.ENGINE_UNAVAILABLE:
            return End("no_engine")
        case _:
            return End("other")


def until(cond: Callable[[], bool], what: str) -> None:
    end = time.monotonic() + DEADLINE
    pause = threading.Event()
    while time.monotonic() < end:
        if cond():
            return
        pause.wait(0.001)
    assert cond(), f"never came: {what}"


# -- the replay ------------------------------------------------------------------------------------------------------


@dataclass
class Tally:
    steps: Counter[str] = field(default_factory=lambda: Counter[str]())
    compared: int = 0
    stops: Counter[str] = field(default_factory=lambda: Counter[str]())


type Replay = TableReplay | ShellReplay


def replay(trace: TraceJ, make: Callable[[QueuePolicy, bool, int], Replay], tally: Tally) -> None:
    meta, _ = corpus()
    consts = meta["constants"][trace["world"]]
    bound = int(consts["StopBound"])
    depth = cast(int, meta["header"]["worlds"][trace["world"]]["depth"])
    states = trace["states"]
    first = states[0]
    assert first["act"]["a"] == "Init", "a trace starts at the model's Init"
    assert [j["k"] for j in first["q"]] == ["recover"], "a trace starts at the boot, its recover-all queued"
    code = make(QueuePolicy(max_queued=64), first["pst"]["orphan"], bound)
    classes = inventory()
    where = f"{trace['world']} trace {trace['trace']}"
    want_ends: dict[int, End] = {}  # by the code's job id
    fresh = 0  # fresh engines the model has started
    try:
        code.boot(first["q"][0]["id"])
        for i in range(1, len(states)):
            s, t = states[i - 1], states[i]
            label = t["act"]["a"]
            try:
                ops = ops_of(s, t, bound, classes)
            except Stop as stop:
                tally.stops[stop.reason] += 1
                return
            tally.steps[label] += 1
            for mid, end in ends_of(s, t, classes).items():
                want_ends[code.ids[mid]] = end
            fresh += fresh_start(s, t)
            for op in ops:
                code.apply(op)
            if i + 1 < len(states) and states[i + 1]["act"]["a"] in EAGER:
                continue  # the table is a step ahead of the model until its dispatch has run
            if i + 1 == depth:
                continue  # the walk was cut here: a dispatch may be due, which the table has already taken
            want = model_view(t, code.ids, bound)

            def ok(want: View = want, t: StateJ = t, fresh: int = fresh) -> bool:
                got = code.ends()
                told = set(got) == set(want_ends) and all(matches(e, got.get(i)) for i, e in want_ends.items())
                view = same(want, code.view(), t, early_advice=code.early_advice)
                return view and told and code.fresh_starts() == fresh

            code.settle(ok)
            tally.compared += 1
            assert ok(), (
                f"{where}, step {i} ({label} {t['act']['p']}): the code reached\n  {code.view()}\n"
                f"where the model is at\n  {want}\nops: {ops}\n"
                f"jobs ended: {code.ends()}, the model's: {want_ends}\n"
                f"fresh engines: {code.fresh_starts()}, the model's: {fresh}"
            )
    finally:
        code.close()


def traces() -> tuple[TraceJ, ...]:
    return corpus()[1]


@pytest.fixture(autouse=True)
def hang_guard() -> Iterator[None]:
    faulthandler.dump_traceback_later(300, exit=True)  # the test's own hang detector; a file runs in about 1 s
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


# -- the tests -------------------------------------------------------------------------------------------------------


def test_the_corpus_is_current_with_the_model() -> None:
    """The corpus records what it was made from; a change to the model, a world, the generator or the tools makes it
    stale, and the replay would check the code against a model that no longer exists."""
    header = corpus()[0]["header"]
    stale = staleness(header)
    assert not stale, f"the trace corpus is stale ({', '.join(stale)}); regenerate it: {REGENERATE}"


def test_every_action_of_the_model_is_classified() -> None:
    """The inventory is complete by construction: every action of the model's Next has exactly one class, and the
    actions left out of the traces are exactly the generator's."""
    model = MODEL.read_text(encoding="utf-8")
    names = {a for g in next_groups(model) for a in step_actions(model, g)}
    classes = inventory()
    assert set(classes) == names, (
        f"not classified: {sorted(names - set(classes))}; not in the model: {sorted(set(classes) - names)}"
    )
    assert sorted(OUT) == corpus()[0]["header"]["out"]
    assert sorted(EAGER) == sorted(corpus()[0]["header"]["eager"])


def test_the_corpus_covers_every_mapped_action() -> None:
    """Each of BL-0150's actions occurs in a replayed step at least once, so the replay checks every one of them."""
    tally = Tally()
    for trace in traces():
        replay(trace, lambda p, o, b: TableReplay(p, o), tally)
    missing = [a for a in MAPPED if tally.steps[a] == 0 and a != "OrphanEndStuck"]  # its stuck orphan needs a quit
    assert not missing, f"the corpus never reaches {missing}: widen its worlds in gen_traces.py"
    assert not tally.stops, f"traces reach steps gen_traces.GUARDS should leave out: {dict(tally.stops)}"


def test_the_table_follows_the_model() -> None:
    tally = Tally()
    for trace in traces():
        replay(trace, lambda p, o, b: TableReplay(p, o), tally)
    assert tally.compared > 0


@pytest.mark.parametrize("world", sorted({t["world"] for t in traces()}))
def test_the_shell_follows_the_model(world: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tally = Tally()
    for trace in traces():
        if trace["world"] == world:
            replay(trace, lambda p, o, b: ShellReplay(p, o, b, monkeypatch), tally)
    assert tally.compared > 0


def test_a_divergence_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The replay is not vacuous: a table that dispatches the newest job first fails it (model mutant
    `newest_first`)."""

    def newest(state: QueueState) -> Job | None:
        pool = [j for j in state.queued if eligible(state, j)]
        return pool[-1] if pool else None

    monkeypatch.setattr(queue, "_pick", newest)

    def every_trace() -> None:
        for trace in traces():
            replay(trace, lambda p, o, b: TableReplay(p, o), Tally())

    with pytest.raises(AssertionError, match="the code reached"):
        every_trace()


def test_the_ops_reader_refuses_an_unknown_action() -> None:
    meta, all_traces = corpus()
    del meta
    s = all_traces[0]["states"][0]
    t = cast(StateJ, {**s, "act": {"a": "NoSuchAction", "p": 0}})
    with pytest.raises(KeyError):
        ops_of(s, t, 3, inventory())
