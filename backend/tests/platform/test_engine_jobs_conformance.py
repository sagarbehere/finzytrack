"""Model-driven conformance: the TLA+ model's traces replayed through the queue table and the scheduler shell (S0059;
BL-0150; `design/specs/engine-jobs.md` §3-§5, §13, §14; ADR-0064).

`formal/tla/engine-jobs/traces/corpus.json` holds TLC-simulated traces of small worlds of `EngineJobs.tla`
(`gen_traces.py` there writes it; each trace is a behaviour of `Spec`, with the app's own dispatch taken first when
it is enabled, as the table takes it inside the step that allows it). Each step is turned into what the code sees
(`ops`): a request (submit, Cancel, a closed tab, Try again), an engine report (a crash, an end drained), a time
event (the display delay, the restart window), the engine lock coming free, or the running job's own part (its end,
its commit point, recover-all's listing). The same ops drive two replays:

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

What this covers (S0064, BL-0159): every branch of `BRANCHES` over the whole corpus
(`test_the_corpus_covers_every_branch`), recover-all's listing, its Cancel after it, its engine's death and fresh
engine included; `UNREACHED` names what the corpus cannot reach and why. Each world has one workspace, so a job of a
workspace that is not open is never seen (the switch is BL-0154). A save's own steps are replayed through the engine in
`engine/tests/ledger/test_save_conformance.py`; here the outcome the sidecar decides after a save's death is checked
against the model's `DiskOutcome` (`test_the_outcome_after_a_save_death_is_the_models_disk_outcome`).
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
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import pytest

from app.domain.errors import DomainError
from app.platform.engine import EngineFailure, OutcomeFacts
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
    EngineRefused,
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
    RecoveryListed,
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
from app.platform.engine_jobs.save import NotDone, Saved, save_outcome
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobContext, JobTicket, WorkResult
from app.platform.engine_supervision import RestartPolicy
from app.platform.errors import ChangeNotSavedDetails, ErrorCode, JobCancelledDetails
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
SPAWN_PCS = frozenset({"rc_spawn"})
EAGER = frozenset({"Dispatch", "DispatchUnavail", "SpawnStep"})

SAVE, R4_READS, CREATE_COPY, SWITCH = "BL-0151", "BL-0152", "BL-0153", "BL-0154"
R4_JOB = "S0068"  # R4 has no disk effect (ADR-0067); its work is `engine_jobs.r4` (`test_r4_job.py`)

# -- the inventory: every action of the model's Next ---------------------------------------------------------------

type Class = Literal["mapped", "job_step", "seam", "away", "out"]


def names(text: str) -> tuple[str, ...]:
    return tuple(text.split())


MAPPED = (
    "Dispatch",  # the dispatch inside the step that allows it (§3); `Start`, the engine lock waited for first
    "DispatchUnavail",  # the queued jobs end `NoEngine` while the engine is unavailable (§5.3)
    "SpawnStep",  # the running job's fresh engine, or its end while unavailable (§5.1)
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
            "S_Locks S_Gate S_Load S_Recheck S_Commit S_Backup S_BackupSync S_JPart S_JPartSync "
            "S_JOpen S_JDirSync S_TempStart S_TempDone S_TempSync S_Swap S_Compare S_DirSync S_ReadBack "
            "S_RemoveTemps S_Close S_CloseSync S_Saved S_Publish S_Release RB_Check RS_Write RS_Swap RS_Compare "
            "RS_Temps RS_Sync RS_Resolve RC_Init RC_Next RC_Lock RC_Check IOFail"
        )
    },
    **{a: R4_JOB for a in names("R4_Start R4_Load R4_Check")},
    **{a: R4_READS for a in names("RD_Load RD_Check OP_Load OP_Check")},
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


def undelta(steps: list[dict[str, object]]) -> list[dict[str, object]]:
    """The states of a trace, from the corpus's steps (each the variables that changed, and under "-" the ones gone;
    a copy of `gen_traces.undelta`, which a test may not import)."""
    states: list[dict[str, object]] = []
    for step in steps:
        state = {**(states[-1] if states else {}), **step}
        for k in cast(list[str], state.pop("-", [])):
            del state[k]
        states.append(state)
    return states


@cache
def corpus() -> tuple[Meta, tuple[TraceJ, ...]]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    rows = [cast(dict[str, object], json.loads(line)) for line in lines[1:]]
    traces = tuple(
        cast(
            TraceJ,
            {"world": r["world"], "trace": r["trace"], "states": undelta(cast(list[dict[str, object]], r["steps"]))},
        )
        for r in rows
    )
    return cast(Meta, json.loads(lines[0])), traces


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
        if a == "EngineCrash" and run is not None and mid == run["id"] and run["k"] == "save":
            # §6.6: the save is told its outcome from disk (S0063), never "lost"; an R4 whose engine died is lost like
            # a read (§6.7, S0068)
            out[mid] = End("finished")
        elif a == "EngineCrash" and run is not None and mid == run["id"]:
            out[mid] = End("lost", past_commit=s["ctl"]["past"])  # only a writing job has a commit point
        elif classes[a] == "job_step":
            out[mid] = End("finished")
        elif run is not None and mid == run["id"] and run["k"] == "recover" and a in ("SpawnStep", "UserCancelRunning"):
            # §8.1 (S0062): recover-all ends with its report: no engine for its next journal (every journal not yet
            # tried `not_tried`), or a Cancel after its listing with nothing left to try
            out[mid] = End("finished") if a == "SpawnStep" or s["ctl"]["rinit"] else End("cancelled", False)
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
    """A Cancel; `then`: what the job's work does once its call fails (recover-all after its listing, S0062: it asks a
    fresh engine for its next journal, or ends with its report when nothing is left)."""

    model_id: int
    then: Literal["none", "fresh", "done"] = "none"


@dataclass(frozen=True, slots=True)
class GoneOp:
    model_id: int


@dataclass(frozen=True, slots=True)
class WorkOp:
    """The running job's own part: its end (`done`, with whether it ran user code), its commit point (`gate`), its
    engine's death mid-request (`crash`, with the bound reached or not; a
    recover-all then asks a fresh engine for its next journal, §8.1), or recover-all's listing (`listed`, S0062)."""

    what: Literal["done", "gate", "crash", "listed"]
    flag: bool = False


@dataclass(frozen=True, slots=True)
class SimpleOp:
    what: Literal["drain", "lock_free", "timer", "lift", "try_again"]


@dataclass(frozen=True, slots=True)
class OpenedOp:
    workspace: str


type Op = SubmitOp | CancelOp | GoneOp | WorkOp | SimpleOp | OpenedOp


STOPS: Mapping[str, str] = {
    "open_crash": f"the open's engine death ends the switch on its failed screen ({R4_READS})",
    "open_job": f"the boot's open of a workspace whose mirror is out of date runs an open job ({R4_READS})",
}
"""Steps that stop their trace: behaviour of a later piece that the code does not have yet. `gen_traces.GUARDS` leaves
both out of the walk; the coverage test fails if a trace reaches either (S0059 verifier F5). Recover-all's listing, a
Cancel after it, its engine's death and its fresh engine (S0062) are walked since S0064 (BL-0151 slice 5)."""


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
                return [CancelOp(run["id"], "done" if t["run"] is None else "fresh")]  # §8.1: it goes on
            return [CancelOp(run["id"])]
        case "UserCloseTab":
            return [GoneOp(int(p))]
        case "UserTryAgain":
            return [SimpleOp("try_again")]
        case "EngineCrash":
            assert run is not None
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
            if run["k"] == "recover" and t["ctl"]["rinit"] and not s["ctl"]["rinit"]:
                return [WorkOp("listed"), *submits]  # §8.1: the listing is reported (`RecoveryListed`, S0062)
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
            elif isinstance(e, EngineRefused):
                follow.append(JobDone(e.job_id, ran_user_code=False))  # recover-all's work answers with its report
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
            case CancelOp(model_id=m, then=then):
                self.feed(Cancel(self.ids[m]))
                if then == "fresh":
                    self.feed(RetireEngine(self.ids[m]))  # the work's `fresh_engine()` (§8.1)
                    self.feed(EngineNeeded(self.ids[m]))
                elif then == "done":
                    self.feed(JobDone(self.ids[m], ran_user_code=False))
            case GoneOp(model_id=m):
                self.feed(ClientGone(self.ids[m]))
            case WorkOp(what="done", flag=ran):
                self.feed(JobDone(self.running_id(), ran))
            case WorkOp(what="gate"):
                self.feed(Gate(self.running_id()))
            case WorkOp(what="listed"):
                self.feed(RecoveryListed(self.running_id()))
            case WorkOp(what="crash", flag=reached):
                if reached:
                    self.feed(EngineAvailability(False))
                assert self.state.engine is not None
                running = self.state.running
                self.feed(EngineDied(self.state.engine))
                if running is not None and running.job.kind == "save":
                    # S0063: the table keeps a save whose engine died; its work reads the outcome from disk and
                    # answers (the model tells `DiskOutcome` at the crash; the disk read itself is the work's,
                    # `test_save_job.py`; the engine's reading against the model's states is
                    # `engine/tests/ledger/test_save_conformance.py`), so here it answers at once, as the model's job
                    # ends at once
                    self.feed(JobDone(running.job.id, ran_user_code=True))
                elif running is not None and running.job.kind == "recover_all":
                    self.feed(RetireEngine(running.job.id))  # §8.1: the work asks a fresh engine for its next part
                    self.feed(EngineNeeded(running.job.id))
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
    its engine ends it wherever it is. A recover-all goes on after its engine ends (§8.1, S0062): the replay queues
    what its work does then (`after`: ask a fresh engine, or end with its report) before it ends the engine."""

    def __init__(self) -> None:
        self.commands: deque[tuple[str, bool]] = deque()
        self.after: deque[str] = deque()

    def _goes_on(self, ctx: JobContext) -> bool:
        """After its engine ended: recover-all asks a fresh engine for its next part, unless told to end."""
        if not self.after or self.after.popleft() == "done":
            return False
        return ctx.fresh_engine() is None

    def __call__(self, ctx: JobContext) -> WorkResult:
        while True:
            if isinstance(ctx.call("block", {}), EngineFailure):
                if self._goes_on(ctx):
                    continue
                return WorkResult(None, ran_user_code=True)
            what, flag = self.commands.popleft()
            if what == "done":
                return WorkResult(None, ran_user_code=flag)
            if what == "gate":
                ctx.call("gate", {})
            elif what == "listed":
                ctx.listed()
            else:
                ctx.call("crash", {})
                if self._goes_on(ctx):
                    continue
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
        script = self.running_script()
        script.commands.append((what, flag))
        self.engine.release("block")
        # the release is a flag, not a count: wait until this command was taken, so the next one's release is not
        # merged with it (S0064: a command that changes nothing the view shows, recover-all's `listed`)
        until(lambda: not script.commands, f"the job's work took {what}")

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
            case CancelOp(model_id=m, then=then):
                if then != "none":
                    self.scripts[self.ids[m]].after.append(then)
                self.jobs.cancel(self.ids[m])
            case GoneOp(model_id=m):
                self.jobs.client_gone(self.ids[m])
            case WorkOp(what="crash", flag=reached):
                if reached:
                    self.engine.unavailable((self.clock.now,) * self.bound)
                held = self.engine.held
                r = self.jobs.status().queue.running
                if r is not None and r.job.kind == "recover_all":
                    self.running_script().after.append("fresh")  # §8.1: it goes on with a fresh engine
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


# -- the branches ----------------------------------------------------------------------------------------------------

type Pred = Callable[[StateJ, StateJ, int], bool]


def _kind(s: StateJ) -> str:
    r = s["run"]
    return "" if r is None else r["k"]


def _ahead_of_cancel(s: StateJ, t: StateJ) -> JobJ | None:
    """The job running ahead of a queued job's Cancel."""
    return s["run"] if t["act"]["a"] == "UserCancelQueued" else None


def _lifts(s: StateJ, t: StateJ, bound: int) -> bool:
    return s["ses"]["stops"] >= bound > t["ses"]["stops"]


def _ends_running(s: StateJ, t: StateJ) -> bool:
    return s["run"] is not None and (t["run"] is None or t["run"]["id"] != s["run"]["id"])


BRANCHES: Mapping[str, Pred] = {
    # the dispatch (§3, §5.1, §5.3)
    "dispatch, fresh engine": lambda s, t, b: t["act"]["a"] == "Dispatch" and s["eng"]["st"] != "idle",
    "dispatch, idle engine reused": lambda s, t, b: t["act"]["a"] == "Dispatch" and s["eng"]["st"] == "idle",
    "dispatch while unavailable": lambda s, t, b: t["act"]["a"] == "DispatchUnavail",
    "recover-all's fresh engine for its next journal": lambda s, t, b: (
        t["act"]["a"] == "SpawnStep" and s["ctl"]["pc"] == "rc_spawn" and t["run"] is not None
    ),
    "recover-all with no engine for its next journal (unavailable)": lambda s, t, b: (
        t["act"]["a"] == "SpawnStep" and s["ctl"]["pc"] == "rc_spawn" and t["run"] is None
    ),
    # Cancel (§4): the one rule for the job running ahead
    "Cancel of a queued job ends a running R4": lambda s, t, b: (
        (r := _ahead_of_cancel(s, t)) is not None and r["k"] == "r4" and t["run"] is None
    ),
    "Cancel of a queued job ends a background save before its commit point": lambda s, t, b: (
        (r := _ahead_of_cancel(s, t)) is not None and r["k"] == "save" and not r["wf"] and not s["ctl"]["past"]
        and t["run"] is None
    ),
    "Cancel of a queued job meets a background save past its commit point": lambda s, t, b: (
        (r := _ahead_of_cancel(s, t)) is not None and r["k"] == "save" and not r["wf"] and s["ctl"]["past"]
        and t["run"] is not None
    ),
    "Cancel of a queued job meets a confirmed save": lambda s, t, b: (
        (r := _ahead_of_cancel(s, t)) is not None and r["k"] == "save" and r["conf"]
    ),
    "Cancel of a queued job with nothing running": lambda s, t, b: (
        t["act"]["a"] == "UserCancelQueued" and s["run"] is None
    ),
    "Cancel of a running read": lambda s, t, b: t["act"]["a"] == "UserCancelRunning" and _kind(s) == "read",
    "Cancel of a running save before its commit point": lambda s, t, b: (
        t["act"]["a"] == "UserCancelRunning" and _kind(s) == "save"
    ),
    "Cancel of recover-all before its listing": lambda s, t, b: (
        t["act"]["a"] == "UserCancelRunning" and _kind(s) == "recover" and not s["ctl"]["rinit"]
    ),
    "Cancel of recover-all after its listing: it goes on": lambda s, t, b: (
        t["act"]["a"] == "UserCancelRunning" and _kind(s) == "recover" and s["ctl"]["rinit"] and t["run"] is not None
    ),
    "Cancel of recover-all after its listing, nothing left: it ends": lambda s, t, b: (
        t["act"]["a"] == "UserCancelRunning" and _kind(s) == "recover" and s["ctl"]["rinit"] and t["run"] is None
    ),
    "a closed tab cancels a queued read": lambda s, t, b: (
        t["act"]["a"] == "UserCloseTab" and len(t["q"]) < len(s["q"])
    ),
    "a closed tab: a running job loses its requester": lambda s, t, b: (
        t["act"]["a"] == "UserCloseTab" and s["run"] is not None and t["run"] is not None
        and s["run"]["req"] == "present" and t["run"]["req"] == "gone"
    ),
    # engine deaths (§5.3, §6.6, §8.1)
    "a save's engine dies before its commit point": lambda s, t, b: (
        t["act"]["a"] == "EngineCrash" and _kind(s) == "save" and not s["ctl"]["past"]
    ),
    "a save's engine dies past its commit point": lambda s, t, b: (
        t["act"]["a"] == "EngineCrash" and _kind(s) == "save" and s["ctl"]["past"]
    ),
    "an R4's engine dies": lambda s, t, b: t["act"]["a"] == "EngineCrash" and _kind(s) == "r4",
    "a read's engine dies": lambda s, t, b: t["act"]["a"] == "EngineCrash" and _kind(s) == "read",
    "recover-all's engine dies before its listing": lambda s, t, b: (
        t["act"]["a"] == "EngineCrash" and _kind(s) == "recover" and not s["ctl"]["rinit"]
    ),
    "recover-all's engine dies after its listing": lambda s, t, b: (
        t["act"]["a"] == "EngineCrash" and _kind(s) == "recover" and s["ctl"]["rinit"]
    ),
    "an engine death reaches the stop bound": lambda s, t, b: (
        t["act"]["a"] == "EngineCrash" and s["ses"]["stops"] < b <= t["ses"]["stops"]
    ),
    # the slot, the lock, the window, Try again, the advice
    "an ended engine is drained": lambda s, t, b: t["act"]["a"] == "Drain",
    "the earlier session's engine ends": lambda s, t, b: t["act"]["a"] == "OrphanEnd",
    "the window lifts the bound": lambda s, t, b: t["act"]["a"] == "WindowExpire" and _lifts(s, t, b),
    "the window lets one stop go": lambda s, t, b: t["act"]["a"] == "WindowExpire" and not _lifts(s, t, b),
    "Try again": lambda s, t, b: t["act"]["a"] == "UserTryAgain",
    "the advice on the timer": lambda s, t, b: t["act"]["a"] == "AdviceTimer",
    "a job hangs": lambda s, t, b: t["act"]["a"] == "Hang",
    "an engine member is stuck": lambda s, t, b: t["act"]["a"] == "Stuck",
    # requests, the boot's open, the job steps' ends
    "a user's save": lambda s, t, b: t["act"]["a"] == "UserSave",
    "a read": lambda s, t, b: t["act"]["a"] == "UserRead",
    "a background save": lambda s, t, b: t["act"]["a"] == "AppBgSave",
    "a save reaches its commit point": lambda s, t, b: _kind(s) == "save" and t["ctl"]["past"] and not s["ctl"]["past"],
    "a save ends with its answer": lambda s, t, b: (
        _kind(s) == "save" and _ends_running(s, t)
        and t["act"]["a"] not in ("EngineCrash", "UserCancelRunning", "UserCancelQueued")
    ),
    "a save queues its R4": lambda s, t, b: (
        t["act"]["a"] == "S_Release" and any(j["k"] == "r4" for j in new_jobs(s, t))
    ),
    "recover-all lists the journals": lambda s, t, b: (
        _kind(s) == "recover" and t["ctl"]["rinit"] and not s["ctl"]["rinit"]
    ),
    "the boot's open": lambda s, t, b: t["act"]["a"] == "Assess" and bool(t["ses"]["opened"]),
}  # fmt: skip
"""The branches the replay must walk (S0064, BL-0159; S0059 verifier F1, F3: the action names overstated the
coverage). Each is a predicate on a model step (the state before, the state after, the stop bound)."""

UNREACHED: Mapping[str, str] = {
    "DispatchUnavail, recover-all": (
        f"recover-all is queued only at the boot here, before any stop; a later one comes with a switch, Retry, Back "
        f"or the next start ({SWITCH})"
    ),
    "UserCancelQueued, recover-all running": (
        f"while the boot's recover-all runs only its own jobs may be queued, and its open job is left out "
        f"({R4_READS}, {SWITCH})"
    ),
    "OrphanEndStuck": f"its stuck orphan comes only from a quit or the app's crash, both left out ({SWITCH})",
    "SpawnStep, a save or an R4": (
        "unreachable: only recover-all asks a fresh engine mid-job (`rc_spawn`); a save and an R4 run in one engine "
        "(S0068, ADR-0067)"
    ),
}
"""Branches of mapped actions the corpus cannot reach, each with why (the hand-written suites guard them)."""


# -- the replay ------------------------------------------------------------------------------------------------------


@dataclass
class Tally:
    steps: Counter[str] = field(default_factory=lambda: Counter[str]())
    branches: Counter[str] = field(default_factory=lambda: Counter[str]())
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
            tally.branches.update(name for name, walked in BRANCHES.items() if walked(s, t, bound))
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


def test_the_corpus_covers_every_branch() -> None:
    """Each branch of `BRANCHES` occurs in a replayed step at least once, and so does each mapped action but those
    `UNREACHED` names, so the replay checks every one of them (S0064, BL-0159: branches, not action names)."""
    tally = Tally()
    for trace in traces():
        replay(trace, lambda p, o, b: TableReplay(p, o), tally)
    missing = [b for b in BRANCHES if tally.branches[b] == 0]
    assert not missing, f"the corpus never walks {missing}: widen its worlds in gen_traces.py"
    unmapped = [a for a in MAPPED if tally.steps[a] == 0 and a not in UNREACHED]
    assert not unmapped, f"the corpus never reaches {unmapped}: widen its worlds in gen_traces.py"
    assert tally.steps["OrphanEndStuck"] == 0, "OrphanEndStuck is reached now: replay it and drop it from UNREACHED"
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


def test_the_outcome_after_a_save_death_is_the_models_disk_outcome() -> None:
    """§6.6: after a save's engine died, what the sidecar decides from the engine's facts (`save.save_outcome`) is what
    the model told at that crash (`DiskOutcome`), for each record state and re-hash the corpus reaches (S0064, BL-0151
    slice 5). The engine's reading of the disk into those facts is replayed against the model's states in
    `engine/tests/ledger/test_save_conformance.py`."""
    seen: Counter[str] = Counter()
    for trace in traces():
        states = cast(list[dict[str, Any]], trace["states"])
        for s, t in pairwise(states):
            run = s["run"]
            if t["act"]["a"] != "EngineCrash" or run is None or run["k"] != "save":
                continue
            sv = s["sv"]["s"]
            record = s["jr"][sv]
            unchanged = all(s["fs"]["cur"][f] == s["sv"]["plan"][f] for f in ("R", "N"))
            facts = OutcomeFacts(record, unchanged if record in ("none", "part", "resolved") else None, None, None)
            got = save_outcome(facts, gated=s["ctl"]["past"])
            told = t["told"][sv]
            seen[f"{record}: {told}"] += 1
            if told == "saved":
                assert isinstance(got, Saved), (record, got)
                continue
            assert isinstance(got, NotDone), (record, told, got)
            if told == "unfinished":
                assert got.error.code is ErrorCode.WRITES_PAUSED, (record, got)
                continue
            assert got.error.code is ErrorCode.CHANGE_NOT_SAVED, (record, told, got)
            details = got.error.details
            assert isinstance(details, ChangeNotSavedDetails)
            if told in ("unchanged", "changed"):  # "notsaved" (a `.resolved` record) says nothing more
                assert details.unchanged is (told == "unchanged"), (record, told, details)
    assert {"none: unchanged", "open: unfinished", "closed: saved"} <= set(seen), seen
