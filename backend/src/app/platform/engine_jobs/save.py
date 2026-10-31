"""The save job, the sidecar's side (S0063; `design/specs/engine-jobs.md` §2's `save`, §4, §5.3, §6.1, §6.6, §11;
ADR-0064 decisions 3 and 7).

The work of a `save` job: one `save` request to the job's engine, which runs §6.1 steps 1-11 (`ledger_engine.ledger.
save`); the sidecar never reads a journal or a ledger file itself. Its answer becomes the job's value:

- **`Saved`** once the engine said so (the closed record is durable). `published` is False until the publish exists
  (BL-0113). The R4 the save owes is on disk (the closed record); `after_saved` queues it as background work
  (`submit_save` wires it: §6.1 step 11, S0066).
- **`NotDone(error)`**, a registry error with §11's text (`save_text`): `WRITES_PAUSED {unfinished_save |
  check_pending | check_failed}`, `LEGACY_APP_SAVING`, `LEDGER_EXTERNALLY_CHANGED`, `WORKSPACE_NOT_READY {WARMING}`,
  `CHANGE_NOT_SAVED {reason, path, folder, acl_entry, unchanged}`; an engine refusal before the engine's `gate` is
  `INTERNAL` (it wrote nothing; never "saved").

**After the engine died without its final reply** (§5.3, §6.6), the queue keeps the job (the table's `outcome_owed`);
then, as after a refusal past its `gate` (an exception once it may have written) or a final reply of the wrong
shape, the work asks for a fresh engine and reads the outcome from disk (`save_outcome`: the job's journal folder synced
first, then its record, and with no record the targets hashed again); `save_outcome` below decides. A death of that
engine asks for another (the client's bounded restart ends it); when none can be had, the answer is
`CHANGE_OUTCOME_UNKNOWN` if the engine had sent its `gate` (it may have written), else `ENGINE_STOPPED` (it had not
reached its commit point: nothing was written).

**R4 first** (§6.1 step 1; S0066): when the engine refuses `check_pending` naming a closed record (its step 1, or
step 4's re-read under the locks; it wrote nothing and ran no plugin), the work runs that record's R4 in this job
(`r4.R4Runner`, "Checking your last change first…"), then, if R4 let the save go on (`pass`, `superseded`,
`superseded_build`, `nondeterministic`, or the record gone), asks the engine again, in a fresh engine when R4 ran
user code; the engine's own step 1 re-reads the records, so nothing is taken on trust. A `mismatch` refuses
`WRITES_PAUSED {check_failed}` with the record and the backup; R4's third failure to run refuses `check_failed` with
`could_not_check`; any other failure to run refuses `check_pending`; a Cancel cancels the save. A record already met in
this save, or more than `MAX_R4_ROUNDS` records, refuses `check_pending` (no loop).

Session ids are lower case only (`new_session_id`), so two sessions never differ only in case: names derived from
`<session>-<job>` are compared case-folded on a case-insensitive volume (S0061 red team r3 overflow 4).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from app.domain.errors import DomainError
from app.platform.engine import EngineEvent, EngineFailure, EngineRefusal, GateReached, OutcomeFacts, SaveAnswer
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.r4 import Cancelled, Checked, NotChecked, R4Runner, R4Target, r4_spec, r4_work, target_of
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_save, decode_save_outcome
from app.platform.errors import (
    ChangeNotSavedDetails,
    EngineStoppedDetails,
    ErrorCode,
    NotSavedReason,
    PausedReason,
    WorkspaceNotReadyDetails,
    WritesPausedDetails,
    error,
)
from app.platform.tla import tla_action

GONE: Final[frozenset[str]] = frozenset({"crashed", "protocol_violation", "start_failed", "closed"})
"""A failed `save` call after which the outcome is read from disk: the engine is gone (it may have written). Our own
Cancel (`cancelled`) has ended the job already; a request the client never sent (`too_large`) wrote nothing."""

WRITER_REASONS: Final[frozenset[str]] = frozenset(
    {"hard_linked", "locked", "not_a_file", "permission", "io_error", "read_back", "name_not_utf8"}
)
OUTSIDE: Final[frozenset[str]] = frozenset({"changed", "path_moved"})
MAX_R4_ROUNDS: Final = 16
"""At most this many owed records are checked inside one save (a workspace has one, or a few after a forget and
re-add; §6.5): beyond it the save refuses `check_pending` rather than loop."""


def new_session_id() -> str:
    """A session id the journal's names may carry (`[a-z0-9_]`, lower case only, unique per start)."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class ChangeText:
    """One file the save writes: its path as the ledger names it, its pre SHA-256 (None: created), its new text."""

    path: str
    pre: str | None
    post: str


@dataclass(frozen=True, slots=True)
class SaveRequest:
    """A confirmed plan (the preview's, BL-0106): who saves, the root, the changes, the plan's fingerprint (its
    canonical encoding, opaque here), the published mirror's fingerprint digest (None: none published), the legacy
    locks to try (ADR-0062; none until M3 knows the legacy installs, BL-0026) and the app's machine-local folder.

    It names no job: the record's name `<session>-<job>` must be this save's alone (§6.5 "One save, one name"), since
    §6.6 tells a dead save's outcome by that name; `save_work` makes a new job part for every save it runs, so a
    request sent twice is two saves with two names (round 1, verifier F2)."""

    session: str
    workspace_key: str
    lineage: str
    root: str
    changes: tuple[ChangeText, ...]
    fingerprint: str
    mirror: str | None
    legacy_locks: tuple[str, ...]
    app_local_data: Path


@dataclass(frozen=True, slots=True)
class Saved:
    """The change is saved (its closed record is durable). `published`: the views were updated from it (BL-0113).
    `nondeterministic`: the R4 run first inside this save found the ledger's plugins give a different result each time
    (§11's notice; the screen is BL-0092's)."""

    published: bool
    nondeterministic: bool = False


@dataclass(frozen=True, slots=True)
class NotDone:
    """The change was not saved, or Finzytrack cannot tell: `error` says which, with §11's text (`save_text`)."""

    error: DomainError


type SaveValue = Saved | NotDone


def save_spec(workspace: str, *, background: bool = False) -> JobSpec:
    """A save the user confirmed (waited for), or one the app started itself (prices: background, unconfirmed)."""
    if background:
        return JobSpec(kind="save", workspace=workspace, waited_for=False, confirmed=False, owner="app")
    return JobSpec(kind="save", workspace=workspace, waited_for=True, confirmed=True, owner="user")


def new_job_name() -> str:
    """A save's job part of its record name (`[a-z0-9_]`), new for every save: random, so it is unique across runs of
    one session as well as across sessions."""
    return uuid.uuid4().hex


def params_of(r: SaveRequest, job: str) -> dict[str, object]:
    return {
        "session": r.session,
        "job": job,
        "workspace_key": r.workspace_key,
        "lineage": r.lineage,
        "root": r.root,
        "changes": [{"path": c.path, "pre": c.pre, "post": c.post} for c in r.changes],
        "fingerprint": r.fingerprint,
        "mirror": r.mirror,
        "legacy_locks": list(r.legacy_locks),
        "app_local_data": str(r.app_local_data),
    }


def _not_done(code: ErrorCode, details: object | None = None) -> NotDone:
    return NotDone(error(code, details))


def _paused(
    reason: PausedReason,
    file: str | None = None,
    *,
    record: str | None = None,
    backup: str | None = None,
    could_not_check: bool = False,
) -> NotDone:
    details = WritesPausedDetails(reason, file, record, backup, could_not_check)
    return _not_done(ErrorCode.WRITES_PAUSED, details)


def answer_of(a: SaveAnswer) -> SaveValue:
    """The engine's `save` answer as the job's value (§11)."""
    match a.outcome:
        case "saved":
            return Saved(a.published)
        case "refused":
            match a.reason:
                case "unfinished_save":
                    return _paused(a.reason)
                case "check_pending":
                    return _paused(a.reason, record=a.path)
                case "check_failed":
                    return _paused(a.reason, record=a.path, backup=a.backup, could_not_check=a.could_not_check)
                case "legacy_app_saving":
                    return _not_done(ErrorCode.LEGACY_APP_SAVING)
                case "ledger_changed":
                    return _not_done(ErrorCode.LEDGER_EXTERNALLY_CHANGED)
                case "mirror_stale":
                    return _not_done(ErrorCode.WORKSPACE_NOT_READY, WorkspaceNotReadyDetails(phase="WARMING"))
                case _:  # `load_refused`: the contract's refusal is BL-0106's; until then a bug, never "saved"
                    return _not_done(ErrorCode.INTERNAL)  # TODO(BL-0106): the contract's own code
        case "not_saved":
            if a.open_journal:
                return _paused("unfinished_save")  # nothing replaced, the journal left open: Reopen resolves it
            if a.reason in OUTSIDE:
                return _not_done(ErrorCode.LEDGER_EXTERNALLY_CHANGED)
            if a.reason in WRITER_REASONS:
                reason = cast("NotSavedReason", a.reason)
                return _not_done(
                    ErrorCode.CHANGE_NOT_SAVED, ChangeNotSavedDetails(reason, a.path, a.entry, a.detail, a.unchanged)
                )
            return _not_done(ErrorCode.INTERNAL)
        case "unfinished":
            return _paused("unfinished_save", a.cause_path if a.cause == "created_path_taken" else None)


def save_outcome(facts: OutcomeFacts, *, gated: bool) -> SaveValue:
    """§6.6, a pure function of what the disk says (model `DiskOutcome`): `.closed` or later is saved; `.resolved`,
    a `.part` or no record is not saved ("unchanged" only after the re-hash); `.open` is unfinished (writes paused,
    Reopen); a record that could not be read is unknown (`CHANGE_OUTCOME_UNKNOWN` once the engine had sent its gate)."""
    match facts.record:
        case "closed" | "done" | "mismatch":
            return Saved(published=False)  # TODO(BL-0152): a `rebuild` is queued when the publish did not land (part 2)
        case "none" | "part" | "resolved":
            unchanged = facts.unchanged is True
            details = ChangeNotSavedDetails("engine_stopped", facts.path, None, None, unchanged)
            return _not_done(ErrorCode.CHANGE_NOT_SAVED, details)
        case "open" | "kept":
            return _paused("unfinished_save", facts.path)
        case "unknown":
            if gated:
                return _not_done(ErrorCode.CHANGE_OUTCOME_UNKNOWN)
            return _not_done(ErrorCode.ENGINE_STOPPED, EngineStoppedDetails(reason="crashed"))


type AfterSaved = Callable[[R4Target], object]


def save_work(request: SaveRequest, *, after_saved: AfterSaved | None = None) -> Work:
    """The work of a save job; `after_saved` is told the record of a save that landed (its R4 is owed)."""

    def work(ctx: JobContext) -> WorkResult:
        return _Run(ctx, request, after_saved).run()

    return work


def submit_save(
    jobs: EngineJobs, workspace: str, request: SaveRequest, *, background: bool = False
) -> JobTicket | Failed:
    """Queue a save (the user's, or the app's own in the background) whose landing queues its R4 as background work
    (§6.1 step 11; at most one queued R4 per workspace, §3: a second one is the first's, and the record it would have
    checked waits for the next save's step 1)."""

    def queue_r4(target: R4Target) -> None:
        jobs.submit(r4_spec(workspace), r4_work(request.app_local_data, target))

    work = save_work(request, after_saved=queue_r4)
    return jobs.submit_background_save(workspace, work) if background else jobs.submit(save_spec(workspace), work)


class _Run:
    def __init__(self, ctx: JobContext, request: SaveRequest, after_saved: AfterSaved | None = None) -> None:
        self._ctx = ctx
        self._r = request
        self._after_saved = after_saved
        self._job = new_job_name()  # this save's name: the save and its outcome read use it (§6.5, §6.6)
        self.gated = False
        self._checked: set[R4Target] = set()
        self._nondeterministic = False

    def _seen(self, event: EngineEvent) -> None:
        if isinstance(event, GateReached):
            self.gated = True

    def run(self) -> WorkResult:
        while True:
            out = self._ctx.call("save", params_of(self._r, self._job), on_event=self._seen)
            if isinstance(out, EngineFailure):
                if out.kind == "cancelled":
                    return WorkResult(None)  # our own end: the job was answered already (§4)
                if out.kind not in GONE:
                    return WorkResult(_not_done(ErrorCode.INTERNAL))  # never sent (`too_large`): nothing written
                return self._from_disk()
            if isinstance(out, EngineRefusal):
                if self.gated:  # an exception past `proceed` (round 1, verifier F3): the save may have written
                    return self._from_disk()
                return WorkResult(_not_done(ErrorCode.INTERNAL))  # refused before its gate: it wrote nothing
            answer = decode_save(out)
            if isinstance(answer, Violation):
                return self._from_disk()  # a final reply we cannot read: what happened is on disk
            if answer.outcome == "refused" and answer.reason == "check_pending":
                stop = self._r4_first(answer)
                if stop is None:
                    continue  # R4 let the save go on: ask the engine again (its step 1 re-reads the records)
                return stop
            return self._answered(answer_of(answer))

    @tla_action("S_Release")
    def _answered(self, value: SaveValue, *, ran_user_code: bool = True) -> WorkResult:
        if isinstance(value, Saved):
            if self._nondeterministic:
                value = Saved(value.published, nondeterministic=True)
            if self._after_saved is not None:
                self._after_saved(R4Target(self._r.workspace_key, f"{self._r.session}-{self._job}"))
        return WorkResult(value, ran_user_code=ran_user_code)

    def _r4_first(self, answer: SaveAnswer) -> WorkResult | None:
        """§6.1 step 1 (the module's docstring): None when the save may be asked again."""
        target = None if answer.path is None else target_of(answer.path)
        if target is None or target in self._checked or len(self._checked) >= MAX_R4_ROUNDS:
            return WorkResult(_paused("check_pending", record=answer.path))
        self._checked.add(target)
        runner = R4Runner(self._ctx, str(self._r.app_local_data))  # the save's engine ran no plugin: it refused
        result = runner.run(target)
        match result:
            case Cancelled():
                return WorkResult(None)  # the save's Cancel (§6.1 step 1: "A Cancel during this step cancels the save")
            case Checked(outcome="mismatch", record=record, backup=backup):
                return WorkResult(_paused("check_failed", record=record, backup=backup))
            case NotChecked(counted="could_not_check", record=record, backup=backup):
                return WorkResult(_paused("check_failed", record=record, backup=backup, could_not_check=True))
            case NotChecked():
                return WorkResult(_paused("check_pending", record=answer.path))
            case Checked(outcome=outcome):
                self._nondeterministic = self._nondeterministic or outcome == "nondeterministic"
        failure = runner.fresh()  # steps 2-11 run in one fresh engine (§6.1)
        if failure is not None:
            return WorkResult(None) if failure.kind == "cancelled" else WorkResult(_paused("check_pending"))
        return None

    def _no_engine(self) -> WorkResult:
        if self.gated:
            return WorkResult(_not_done(ErrorCode.CHANGE_OUTCOME_UNKNOWN), ran_user_code=False)
        return WorkResult(
            _not_done(ErrorCode.ENGINE_STOPPED, EngineStoppedDetails(reason="crashed")), ran_user_code=False
        )

    def _from_disk(self) -> WorkResult:
        params: Mapping[str, object] = {
            "app_local_data": str(self._r.app_local_data),
            "workspace_key": self._r.workspace_key,
            "session": self._r.session,
            "job": self._job,
            "targets": [{"path": c.path, "pre": c.pre} for c in self._r.changes],
        }
        while True:
            if self._ctx.fresh_engine() is not None:
                return self._no_engine()
            out = self._ctx.call("save_outcome", params)
            if isinstance(out, EngineFailure):
                if out.kind in GONE:
                    continue  # that engine died too: another, until the client's bound says no
                return self._no_engine()
            if isinstance(out, EngineRefusal):
                return self._no_engine()
            facts = decode_save_outcome(out)
            if isinstance(facts, Violation):
                return self._no_engine()
            return self._answered(save_outcome(facts, gated=self.gated), ran_user_code=False)
