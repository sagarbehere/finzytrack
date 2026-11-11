"""The save job, the sidecar's side (S0063; `design/specs/engine-jobs.md` §2's `save`, §4, §5.3, §6.1, §6.6, §11;
ADR-0064 decisions 3 and 7).

The work of a `save` job: one `save` request to the job's engine, which runs §6.1 steps 1-11 (`ledger_engine.ledger.
save`); the sidecar never reads a journal or a ledger file itself. Its answer becomes the job's value:

- **`Saved`** once the engine said so (the closed record is durable), or once the disk said so after the engine
  died (§6.6). `published` is False: the save publishes nothing itself. `after_saved` queues the background rebuild
  with Beancount's own loader (`submit_save` wires it: §6.1 step 10; ADR-0068 D1, S0070); it gates nothing.
- **`NotDone(error)`**, a registry error with §11's text (`save_text`): `WRITES_PAUSED {unfinished_save}` (this
  save's journal left open) or `{earlier_unfinished}` (refused at step 1 or 4 by an earlier save's: S0100, BL-0183),
  `LEDGER_EXTERNALLY_CHANGED`, `WORKSPACE_NOT_READY {WARMING}`,
  `CHANGE_NOT_SAVED {reason, path, folder, acl_entry, unchanged, changed}`; an engine refusal before the engine's
  `gate` is `INTERNAL` (it wrote nothing; never "saved"), and so is the save's `encrypted_file` refusal until the
  preview's own refusal exists (BL-0106).

**After the engine died without its final reply** (§5.3, §6.6), the queue keeps the job (the table's `outcome_owed`);
then, as after a refusal past its `gate` (an exception once it may have written) or a final reply of the wrong
shape, the work asks for a fresh engine and reads the outcome from disk (`save_outcome`: the job's journal folder synced
first, then its record, and with no record the targets hashed again); `save_outcome` below decides. A death of that
engine asks for another (the client's bounded restart ends it); when none can be had, the answer is
`CHANGE_OUTCOME_UNKNOWN` if the engine had sent its `gate` (it may have written), else `ENGINE_STOPPED` (it had not
reached its commit point: nothing was written).

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
from app.platform.clock import Clock
from app.platform.engine import EngineEvent, EngineFailure, EngineRefusal, GateReached, OutcomeFacts, SaveAnswer
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.rebuild import RebuildTarget, rebuild_spec, rebuild_work
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_save, decode_save_outcome
from app.platform.errors import (
    ChangeNotSavedDetails,
    EngineStoppedDetails,
    EngineUnavailableDetails,
    ErrorCode,
    NotSavedReason,
    WorkspaceNotReadyDetails,
    WritesPausedDetails,
    error,
)

GONE: Final[frozenset[str]] = frozenset({"crashed", "protocol_violation", "start_failed", "closed"})
"""A failed `save` call after which the outcome is read from disk: the engine is gone (it may have written). Our own
Cancel (`cancelled`) has ended the job already; a request the client never sent (`too_large`) wrote nothing."""

WRITER_REASONS: Final[frozenset[str]] = frozenset(
    {
        "hard_linked",
        "locked",
        "not_a_file",
        "permission",
        "io_error",
        "read_back",
        "name_not_utf8",
        "linked_folder",
        "no_drive_id",
        "no_folder_id",
        "case_twins",
        "folder_appeared",
        "app_folder_missing",
    }
)
OUTSIDE: Final[frozenset[str]] = frozenset({"changed", "path_moved"})


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
    """A confirmed plan (the preview's, BL-0106): who saves, the root, the changes, the plan's
    fingerprint (its canonical encoding, opaque here) and the app's machine-local folder (no legacy lock: a save tries
    none, ADR-0068 D4). The engine's gate reads the published mirror in the app cache folder the engine was started
    with (publish.md §7; S0087): no request names it.

    It names no job: the record's name `<session>-<job>` must be this save's alone (§6.5 "One save, one name"), since
    §6.6 tells a dead save's outcome by that name; `save_work` makes a new job part for every save it runs, so a
    request sent twice is two saves with two names (round 1, verifier F2)."""

    session: str
    workspace_key: str
    lineage: str
    root: str
    changes: tuple[ChangeText, ...]
    fingerprint: str
    app_local_data: Path


@dataclass(frozen=True, slots=True)
class Saved:
    """The change is saved (its closed record is durable). `published`: the save itself updated the views (never: the
    rebuild after it publishes, §6.7; S0087)."""

    published: bool


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
        "app_local_data": str(r.app_local_data),
    }


def _not_done(code: ErrorCode, details: object | None = None) -> NotDone:
    return NotDone(error(code, details))


def _paused(file: str | None = None) -> NotDone:
    return _not_done(ErrorCode.WRITES_PAUSED, WritesPausedDetails("unfinished_save", file))


def _earlier_unfinished() -> NotDone:
    """§6.1 steps 1 and 4: an earlier save's `.open` journal refused this one before anything was written (BL-0183)."""
    return _not_done(ErrorCode.WRITES_PAUSED, WritesPausedDetails("earlier_unfinished", None))


def answer_of(a: SaveAnswer) -> SaveValue:
    """The engine's `save` answer as the job's value (§11)."""
    match a.outcome:
        case "saved":
            return Saved(a.published)
        case "refused":
            match a.reason:
                case "unfinished_save":
                    return _earlier_unfinished()
                case "ledger_changed":
                    return _not_done(ErrorCode.LEDGER_EXTERNALLY_CHANGED)
                case "mirror_stale":
                    return _not_done(ErrorCode.WORKSPACE_NOT_READY, WorkspaceNotReadyDetails(phase="WARMING"))
                case _:  # `encrypted_file`: the preview refuses it first (BL-0106); here a bug, never "saved"
                    return _not_done(ErrorCode.INTERNAL)  # TODO(BL-0106): the contract's own code
        case "not_saved":
            if a.open_journal:
                return _paused()  # nothing replaced, the journal left open: Reopen resolves it
            if a.reason in OUTSIDE:
                return _not_done(ErrorCode.LEDGER_EXTERNALLY_CHANGED)
            if a.reason in WRITER_REASONS:
                reason = cast("NotSavedReason", a.reason)
                details = ChangeNotSavedDetails(reason, a.path, a.entry, a.detail, a.unchanged, a.changed)
                return _not_done(ErrorCode.CHANGE_NOT_SAVED, details)
            return _not_done(ErrorCode.INTERNAL)
        case "unfinished":
            return _paused(a.cause_path if a.cause == "created_path_taken" else None)


def save_outcome(facts: OutcomeFacts, *, gated: bool) -> SaveValue:
    """§6.6, a pure function of what the disk says (the durability model's `DiskOutcome`): `.closed` is saved;
    `.resolved`, a `.part` or no record is not saved ("unchanged" only after the re-hash); `.open` is unfinished (writes
    paused, Reopen); a record that could not be read is unknown (`CHANGE_OUTCOME_UNKNOWN` once the engine had sent its
    gate)."""
    match facts.record:
        case "closed":
            return Saved(published=False)  # its rebuild is queued by `_answered` (§6.6)
        case "none" | "part" | "resolved":
            unchanged = facts.unchanged is True
            details = ChangeNotSavedDetails("engine_stopped", None, None, None, unchanged, facts.path)
            return _not_done(ErrorCode.CHANGE_NOT_SAVED, details)
        case "open" | "kept":
            return _paused(facts.path)
        case "unknown":
            if gated:
                return _not_done(ErrorCode.CHANGE_OUTCOME_UNKNOWN)
            return _not_done(ErrorCode.ENGINE_STOPPED, EngineStoppedDetails(reason="crashed"))


type AfterSaved = Callable[[RebuildTarget], object]


def save_work(request: SaveRequest, *, after_saved: AfterSaved | None = None) -> Work:
    """The work of a save job; `after_saved` is told what to rebuild after a save that landed."""

    def work(ctx: JobContext) -> WorkResult:
        return _Run(ctx, request, after_saved).run()

    return work


def submit_save(
    jobs: EngineJobs, workspace: str, request: SaveRequest, *, clock: Clock, background: bool = False
) -> JobTicket | Failed:
    """Queue a save (the user's, or the app's own in the background) whose landing queues the background rebuild
    (§6.1 step 10, ADR-0068 D1). A background save of a fixed request cannot join a queued one (ADR-0068 D8: only a
    work that plans when it runs can), so a second one while one is queued is refused with its own
    `ENGINE_QUEUE_FULL`. At most one rebuild waits per workspace (§3): a newer save's finds the queued one, which loads
    the disk when it runs and so shows both saves (its fingerprint is a hint: a set that misses a file the newer save
    added makes it try once more with the wider set, `ledger_engine.ledger.rebuild`)."""

    def queue_rebuild(target: RebuildTarget) -> None:
        jobs.submit(rebuild_spec(workspace), rebuild_work(target, clock))

    work = save_work(request, after_saved=queue_rebuild)
    return jobs.submit_background_save(workspace, work) if background else jobs.submit(save_spec(workspace), work)


class _Run:
    def __init__(self, ctx: JobContext, request: SaveRequest, after_saved: AfterSaved | None = None) -> None:
        self._ctx = ctx
        self._r = request
        self._after_saved = after_saved
        self._job = new_job_name()  # this save's name: the save and its outcome read use it (§6.5, §6.6)
        self.gated = False

    def _seen(self, event: EngineEvent) -> None:
        if isinstance(event, GateReached):
            self.gated = True

    def run(self) -> WorkResult:
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
        return self._answered(answer_of(answer))

    def _answered(self, value: SaveValue, *, ran_user_code: bool = True) -> WorkResult:
        if isinstance(value, Saved) and self._after_saved is not None:
            r = self._r
            self._after_saved(RebuildTarget(r.workspace_key, r.root, r.fingerprint, r.lineage, r.app_local_data))
        return WorkResult(value, ran_user_code=ran_user_code)

    def _no_engine(self, *, app_folder: bool = False) -> WorkResult:
        """No engine read the outcome. Past the gate the change may have landed: `CHANGE_OUTCOME_UNKNOWN` (check the
        ledger before making it again), also when the app folder holding its record was removed or replaced (S0102
        round 1, verifier F1: never "not saved"). Before the gate nothing was written; a folder that is not the one of
        the start then says so (quit and reopen)."""
        if self.gated:
            return WorkResult(_not_done(ErrorCode.CHANGE_OUTCOME_UNKNOWN), ran_user_code=False)
        if app_folder:
            details = EngineUnavailableDetails(reason="app_folder_missing")
            return WorkResult(_not_done(ErrorCode.ENGINE_UNAVAILABLE, details), ran_user_code=False)
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
            refused = self._ctx.fresh_engine()
            if refused is not None:
                return self._no_engine(app_folder=refused.kind == "app_folder_missing")
            out = self._ctx.call("save_outcome", params)
            if isinstance(out, EngineFailure):
                if out.kind in GONE:
                    continue  # that engine died too: another, until the client's bound says no
                return self._no_engine()
            if isinstance(out, EngineRefusal):
                return self._no_engine(app_folder=out.key == "engine.app_folder_missing")
            facts = decode_save_outcome(out)
            if isinstance(facts, Violation):
                return self._no_engine()
            return self._answered(save_outcome(facts, gated=self.gated), ran_user_code=False)
