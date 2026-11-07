"""The create job, the sidecar's side (S0080; `design/specs/engine-jobs.md` §1 "Commit point", §2's `create`, §4,
§5.3, §8.2, §11; ADR-0064 P2, ADR-0063 decision 1).

The work of a `create` job: one `create` request to the job's engine, which writes the new folder's files, checks
that its ledger loads with no error, sends `gate`, and writes `.finzytrack/` last (`ledger_engine.ledger.create`);
the sidecar never reads the folder itself. Its answer becomes the job's value:

- **`Created(folder)`** once the engine said so, or once the disk said so after the engine died. Registering the
  new workspace (§8.2 step 8, after `assess_workspace` says `READY`, INV-29) is the caller's: the registry is
  BL-0102's.
- **`NotCreated`** with its reason and the delete advice (`create_text` words it): no `.finzytrack/` of ours stands
  in the folder.
- **`NotDone(error)`**: `CHANGE_OUTCOME_UNKNOWN` when no engine could read the disk past the commit point (the
  folder may be a workspace: no Retry), `INTERNAL` for a request the client could not send (nothing written).

**After the engine died without its final reply**, before or past its commit point (§1: "After the commit point the
job finishes or is resolved from disk"; §8.2: "an engine death leaves the job to register the workspace"), and after
an engine refusal or a reply of the wrong shape, the queue keeps the job (`outcome_owed`) and the work asks a fresh
engine for `create_outcome`: the folder synced, then `.finzytrack` read (`create_outcome` below decides), with the
listing for the delete advice. A death of that engine asks for another (the client's bounded restart ends it).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast

from app.platform.engine import CreateAnswer, CreateFacts, EngineEvent, EngineFailure, EngineRefusal, GateReached
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.save import GONE, NotDone
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_create, decode_create_outcome
from app.platform.errors import ErrorCode, error

type NotCreatedReason = Literal[
    "not_empty",
    "not_a_folder",
    "no_parent",
    "permission",
    "taken",
    "read_back",
    "load_failed",
    "load_errors",
    "load_outside",
    "manifest_taken",
    "path_moved",
    "io_error",
    "stopped",  # the engine stopped before its answer (read from disk, or no engine to read it)
]
REASONS: Final[frozenset[str]] = frozenset(
    {
        "not_empty",
        "not_a_folder",
        "no_parent",
        "permission",
        "taken",
        "read_back",
        "load_failed",
        "load_errors",
        "load_outside",
        "manifest_taken",
        "path_moved",
        "io_error",
    }
)
"""The engine's own reasons (`ledger_engine.ledger.create.Reason`); `stopped` is the sidecar's."""


@dataclass(frozen=True, slots=True)
class NewFile:
    """One file of the new workspace: its path relative to the folder it is written in, and its text."""

    path: str
    text: str


@dataclass(frozen=True, slots=True)
class CreateRequest:
    """A confirmed create (§8.2): the target folder (absolute), the workspace's files relative to it, which of them is
    the ledger's root, and the files of `.finzytrack/` relative to it (`app.workspaces.creation.manifest_files` makes
    the manifest). All of it is Finzytrack's own text (the wizard's starter ledger, M3); a ledger kept outside the
    folder is BL-0197."""

    target: str
    files: tuple[NewFile, ...]
    root: str
    manifest: tuple[NewFile, ...]


@dataclass(frozen=True, slots=True)
class Created:
    """The new workspace is complete on disk (`.finzytrack/` durable over files read back). Not registered yet."""

    folder: str


@dataclass(frozen=True, slots=True)
class NotCreated:
    """No `.finzytrack/` of ours stands in `folder`. `wrote`: the job made something there (None: not known);
    `only_ours`: listed when the job stopped, the folder holds nothing but the job's own bytes (None: not listed)."""

    reason: NotCreatedReason
    folder: str
    wrote: bool | None
    only_ours: bool | None
    path: str | None = None
    detail: str | None = None
    errno: int | None = None


type CreateValue = Created | NotCreated | NotDone


def create_spec() -> JobSpec:
    """A create the user confirmed: app-scoped, waited for (§2)."""
    return JobSpec(kind="create", workspace=None, waited_for=True, confirmed=True, owner="user")


def new_job_name() -> str:
    """The job part of the temp folder's name `.finzytrack.ft-<job>` (`[a-z0-9_]`), new for every create."""
    return uuid.uuid4().hex


def params_of(r: CreateRequest, job: str) -> dict[str, object]:
    return {
        "job": job,
        "target": r.target,
        "files": [{"path": f.path, "text": f.text} for f in r.files],
        "root": r.root,
        "manifest": [{"path": f.path, "text": f.text} for f in r.manifest],
    }


def answer_of(a: CreateAnswer) -> CreateValue:
    """The engine's `create` answer as the job's value (§11)."""
    if a.outcome == "created":
        return Created(a.folder)
    reason = a.reason if a.reason in REASONS else "io_error"
    return NotCreated(cast("NotCreatedReason", reason), a.folder, a.wrote, a.only_ours, a.path, a.detail, a.errno)


def create_outcome(facts: CreateFacts, *, gated: bool, folder: str) -> CreateValue:
    """§1 "resolved from disk", a pure function of what the disk says: `.finzytrack` holding our bytes is created
    (only the job's step 7 puts it there, after the commit point); anything else is not. Past the commit point a
    foreign `.finzytrack` is step 7's collision, and a folder that could not be read is unknown (it may be a
    workspace); before it, nothing of ours was put there, so it is not created."""
    if facts.manifest == "ours":
        return Created(folder)
    if gated and facts.manifest == "unknown":
        return NotDone(error(ErrorCode.CHANGE_OUTCOME_UNKNOWN))
    reason: NotCreatedReason = "manifest_taken" if gated and facts.manifest == "foreign" else "stopped"
    return NotCreated(reason, folder, facts.wrote, facts.only_ours, errno=facts.errno)


def create_work(request: CreateRequest) -> Work:
    """The work of a create job."""

    def work(ctx: JobContext) -> WorkResult:
        return _Run(ctx, request).run()

    return work


def submit_create(jobs: EngineJobs, request: CreateRequest) -> JobTicket | Failed:
    """Queue a create. TODO(BL-0102): register the folder once it is `Created` (§8.2 step 8, INV-29)."""
    return jobs.submit(create_spec(), create_work(request))


class _Run:
    def __init__(self, ctx: JobContext, request: CreateRequest) -> None:
        self._ctx = ctx
        self._r = request
        self._params = params_of(request, new_job_name())  # the create and its outcome read name the same job
        self.gated = False

    def _seen(self, event: EngineEvent) -> None:
        if isinstance(event, GateReached):
            self.gated = True

    def run(self) -> WorkResult:
        out = self._ctx.call("create", self._params, on_event=self._seen)
        if isinstance(out, EngineFailure):
            if out.kind == "cancelled":
                return WorkResult(None)  # our own end: the job was answered already (§4)
            if out.kind not in GONE:
                return WorkResult(NotDone(error(ErrorCode.INTERNAL)))  # never sent (`too_large`): nothing written
            return self._from_disk()
        if isinstance(out, EngineRefusal):
            return self._from_disk()  # an exception in the engine: it may have written, before or past its gate
        answer = decode_create(out)
        if isinstance(answer, Violation):
            return self._from_disk()  # a final reply we cannot read: what happened is on disk
        return WorkResult(answer_of(answer))

    def _no_engine(self) -> WorkResult:
        if self.gated:
            return WorkResult(NotDone(error(ErrorCode.CHANGE_OUTCOME_UNKNOWN)), ran_user_code=False)
        value = NotCreated("stopped", self._r.target, wrote=None, only_ours=None)  # no `.finzytrack` before the gate
        return WorkResult(value, ran_user_code=False)

    def _from_disk(self) -> WorkResult:
        params: Mapping[str, object] = self._params
        while True:
            if self._ctx.fresh_engine() is not None:
                return self._no_engine()
            out = self._ctx.call("create_outcome", params)
            if isinstance(out, EngineFailure):
                if out.kind in GONE:
                    continue  # that engine died too: another, until the client's bound says no
                return self._no_engine()
            if isinstance(out, EngineRefusal):
                return self._no_engine()
            facts = decode_create_outcome(out)
            if isinstance(facts, Violation):
                return self._no_engine()
            value = create_outcome(facts, gated=self.gated, folder=self._r.target)
            return WorkResult(value, ran_user_code=False)
