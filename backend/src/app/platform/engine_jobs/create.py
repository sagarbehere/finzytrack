"""The create job, the sidecar's side (S0080; `design/specs/engine-jobs.md` §1 "Commit point", §2's `create`, §4, §5.3,
§8.2, §11; ADR-0064 P2, ADR-0063 decision 1).

The work of a `create` job: one `create` request to the job's engine, which writes the new folder's files, checks that
its ledger loads with no error, sends `gate`, and writes `.finzytrack/` last (`ledger_engine.ledger.create`); the
sidecar never reads the folder itself. Its answer becomes the job's value:

- **`Created(folder)`** once the engine said so, or once the disk said so after the engine died. Registering the new
  workspace (§8.2 step 8, after `assess_workspace` says `READY`, INV-29) is the caller's: the registry is BL-0102's.
- **`NotCreated`** with its reason and what the job left in the folder (`create_text` words it): no `.finzytrack/` of
  ours stands in the folder.
- **`NotDone(error)`**: `CHANGE_OUTCOME_UNKNOWN` when no engine could read the disk past the commit point (the folder
  may be a workspace: no Retry), `INTERNAL` for a request the client could not send (nothing written).

**After the engine died without its final reply**, before or past its commit point (§1: "After the commit point the job
finishes or is resolved from disk"; §8.2: "an engine death leaves the job to register the workspace"), and after an
engine refusal or a reply of the wrong shape, the queue keeps the job (`outcome_owed`) and the work asks a fresh engine
for `create_outcome`: the folder synced, then `.finzytrack` read (`create_outcome` below decides), with the entries at
the job's names that may be its own (nothing is removed after a death). A death of that engine asks for another (the
client's bounded restart ends it)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast

from app.platform.engine import (
    CreateAnswer,
    CreateFacts,
    EngineEvent,
    EngineFailure,
    EngineRefusal,
    GateReached,
)
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
    """No `.finzytrack/` of ours stands in `folder` (the target as requested). `left`: what the job left there
    because it could not be sure each is only its own, by path relative to the folder (it removed the rest of what
    it made; after a death it removes nothing and names what may be its own); None when the folder was not cleaned
    up or listed (a Cancel, no engine, an error): the user is asked to check it (owner, 2026-11-07)."""

    reason: NotCreatedReason
    folder: str
    left: tuple[str, ...] | None
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
    return NotCreated(cast("NotCreatedReason", reason), a.folder, a.left, a.path, a.detail, a.errno)


def create_outcome(facts: CreateFacts, *, gated: bool, folder: str) -> CreateValue:
    """§1 "resolved from disk", a pure function of what the disk says: `.finzytrack` holding our bytes is created
    (only the job's step 7 puts it there, after the commit point); anything else is not created, naming what stands at
    the job's names. Past the commit point a folder that could not be read is unknown (it may be a workspace); before
    it, nothing of ours was put there, so it is not created."""
    if facts.manifest == "ours":
        return Created(folder)
    if gated and facts.manifest == "unknown":
        return NotDone(error(ErrorCode.CHANGE_OUTCOME_UNKNOWN))
    # A `.finzytrack` that is not ours after a death may be another program's or a part of ours a death cut in the
    # cleanup (S0080 r3): "stopped", naming what stands at the job's names, never blaming another program.
    return NotCreated("stopped", folder, facts.left, errno=facts.errno)


def create_work(request: CreateRequest) -> Work:
    """The work of a create job."""

    def work(ctx: JobContext) -> WorkResult:
        params = params_of(request, new_job_name())  # the create and its outcome read name the same job
        return FolderJob(ctx, "create", "create_outcome", params, request.target, answer_of, _from_facts).run()

    return work


def _from_facts(facts: CreateFacts, gated: bool, folder: str) -> CreateValue:
    return create_outcome(facts, gated=gated, folder=folder)


def submit_create(jobs: EngineJobs, request: CreateRequest) -> JobTicket | Failed:
    """Queue a create. TODO(BL-0102): register the folder once it is `Created` (§8.2 step 8, INV-29)."""
    return jobs.submit(create_spec(), create_work(request))


class FolderJob[V]:
    """The work of a job that writes a new folder and its manifest last (a create, a copy: S0081): one request to the
    job's engine; after an engine death, an engine refusal or a reply of the wrong shape, the outcome read from disk
    in a fresh engine (`outcome_op`), decided by `from_facts`; with no engine for that read, `CHANGE_OUTCOME_UNKNOWN`
    past the commit point, else "stopped" with the folder unlisted (`from_facts` of no facts is not asked)."""

    def __init__(
        self,
        ctx: JobContext,
        op: str,
        outcome_op: str,
        params: Mapping[str, object],
        target: str,
        answer: Callable[[CreateAnswer], V],
        from_facts: Callable[[CreateFacts, bool, str], V],
    ) -> None:
        self._ctx = ctx
        self._op = op
        self._outcome_op = outcome_op
        self._params = params
        self._target = target
        self._answer = answer
        self._from_facts = from_facts
        self.gated = False

    def _seen(self, event: EngineEvent) -> None:
        if isinstance(event, GateReached):
            self.gated = True

    def run(self) -> WorkResult:
        out = self._ctx.call(self._op, self._params, on_event=self._seen)
        if isinstance(out, EngineFailure):
            if out.kind == "cancelled":
                return WorkResult(None)  # our own end: the job was answered already (§4)
            if out.kind not in GONE:
                return WorkResult(NotDone(error(ErrorCode.INTERNAL)))  # never sent (`too_large`): nothing written
            return self._from_disk()
        if isinstance(out, EngineRefusal):
            if out.key != "engine.internal" and not self.gated:
                # A request the engine would not take (malformed params, a malformed plan: `BadPlanError`) is refused
                # before anything is read or written: Finzytrack's own fault, never "stopped" (S0080 r1 question 2).
                return WorkResult(NotDone(error(ErrorCode.INTERNAL)))
            return self._from_disk()  # an exception in the engine: it may have written, before or past its gate
        answer = decode_create(out)
        if isinstance(answer, Violation):
            return self._from_disk()  # a final reply we cannot read: what happened is on disk
        return WorkResult(self._answer(answer))

    def _no_engine(self) -> WorkResult:
        if self.gated:
            return WorkResult(NotDone(error(ErrorCode.CHANGE_OUTCOME_UNKNOWN)), ran_user_code=False)
        # no `.finzytrack` before the gate; not listed
        value = self._from_facts(CreateFacts("unknown", None, None), False, self._target)
        return WorkResult(value, ran_user_code=False)

    def _from_disk(self) -> WorkResult:
        while True:
            if self._ctx.fresh_engine() is not None:
                return self._no_engine()
            out = self._ctx.call(self._outcome_op, self._params)
            if isinstance(out, EngineFailure):
                if out.kind in GONE:
                    continue  # that engine died too: another, until the client's bound says no
                return self._no_engine()
            if isinstance(out, EngineRefusal):
                return self._no_engine()
            facts = decode_create_outcome(out)
            if isinstance(facts, Violation):
                return self._no_engine()
            return WorkResult(self._from_facts(facts, self.gated, self._target), ran_user_code=False)
