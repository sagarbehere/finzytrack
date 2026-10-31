"""The save job, the sidecar's side (S0063; `design/specs/engine-jobs.md` §2's `save`, §4, §5.3, §6.1, §6.6, §11;
ADR-0064 decisions 3 and 7).

The work of a `save` job: one `save` request to the job's engine, which runs §6.1 steps 1-11 (`ledger_engine.ledger.
save`); the sidecar never reads a journal or a ledger file itself. Its answer becomes the job's value:

- **`Saved`** once the engine said so (the closed record is durable). `published` is False until the publish exists
  (BL-0113). `after_saved` queues the save's R4 as background work (`submit_save` wires it: §6.1 step 11; S0066):
  it compares and shows a notice, and gates nothing (ADR-0067; S0068).
- **`NotDone(error)`**, a registry error with §11's text (`save_text`): `WRITES_PAUSED {unfinished_save}`,
  `LEGACY_APP_SAVING`, `LEDGER_EXTERNALLY_CHANGED`, `WORKSPACE_NOT_READY {WARMING}`,
  `CHANGE_NOT_SAVED {reason, path, folder, acl_entry, unchanged}`; an engine refusal before the engine's `gate` is
  `INTERNAL` (it wrote nothing; never "saved").

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
from app.platform.engine import EngineEvent, EngineFailure, EngineRefusal, GateReached, OutcomeFacts, SaveAnswer
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.r4 import R4Target, r4_spec, r4_work
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_save, decode_save_outcome
from app.platform.errors import (
    ChangeNotSavedDetails,
    EngineStoppedDetails,
    ErrorCode,
    NotSavedReason,
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
    """The change is saved (its closed record is durable). `published`: the views were updated from it (BL-0113)."""

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
        "mirror": r.mirror,
        "legacy_locks": list(r.legacy_locks),
        "app_local_data": str(r.app_local_data),
    }


def _not_done(code: ErrorCode, details: object | None = None) -> NotDone:
    return NotDone(error(code, details))


def _paused(file: str | None = None) -> NotDone:
    return _not_done(ErrorCode.WRITES_PAUSED, WritesPausedDetails("unfinished_save", file))


def answer_of(a: SaveAnswer) -> SaveValue:
    """The engine's `save` answer as the job's value (§11)."""
    match a.outcome:
        case "saved":
            return Saved(a.published)
        case "refused":
            match a.reason:
                case "unfinished_save":
                    return _paused()
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
                return _paused()  # nothing replaced, the journal left open: Reopen resolves it
            if a.reason in OUTSIDE:
                return _not_done(ErrorCode.LEDGER_EXTERNALLY_CHANGED)
            if a.reason in WRITER_REASONS:
                reason = cast("NotSavedReason", a.reason)
                return _not_done(
                    ErrorCode.CHANGE_NOT_SAVED, ChangeNotSavedDetails(reason, a.path, a.entry, a.detail, a.unchanged)
                )
            return _not_done(ErrorCode.INTERNAL)
        case "unfinished":
            return _paused(a.cause_path if a.cause == "created_path_taken" else None)


def save_outcome(facts: OutcomeFacts, *, gated: bool) -> SaveValue:
    """§6.6, a pure function of what the disk says (model `DiskOutcome`): `.closed` is saved; `.resolved`,
    a `.part` or no record is not saved ("unchanged" only after the re-hash); `.open` is unfinished (writes paused,
    Reopen); a record that could not be read is unknown (`CHANGE_OUTCOME_UNKNOWN` once the engine had sent its gate)."""
    match facts.record:
        case "closed":
            return Saved(published=False)  # TODO(BL-0152): a `rebuild` is queued when the publish did not land (part 2)
        case "none" | "part" | "resolved":
            unchanged = facts.unchanged is True
            details = ChangeNotSavedDetails("engine_stopped", facts.path, None, None, unchanged)
            return _not_done(ErrorCode.CHANGE_NOT_SAVED, details)
        case "open" | "kept":
            return _paused(facts.path)
        case "unknown":
            if gated:
                return _not_done(ErrorCode.CHANGE_OUTCOME_UNKNOWN)
            return _not_done(ErrorCode.ENGINE_STOPPED, EngineStoppedDetails(reason="crashed"))


type AfterSaved = Callable[[R4Target], object]


def save_work(request: SaveRequest, *, after_saved: AfterSaved | None = None) -> Work:
    """The work of a save job; `after_saved` is told the record of a save that landed (its R4 is queued)."""

    def work(ctx: JobContext) -> WorkResult:
        return _Run(ctx, request, after_saved).run()

    return work


def submit_save(
    jobs: EngineJobs, workspace: str, request: SaveRequest, *, background: bool = False
) -> JobTicket | Failed:
    """Queue a save (the user's, or the app's own in the background) whose landing queues its R4 as background work
    (§6.1 step 11). At most one R4 waits per workspace (§3); a newer save's takes the queued one's place, since only
    the newest save can still be compared with the disk (`EngineJobs.submit`'s `replace_queued`; S0068)."""

    def queue_r4(target: R4Target) -> None:
        jobs.submit(r4_spec(workspace), r4_work(request.app_local_data, target), replace_queued=True)

    work = save_work(request, after_saved=queue_r4)
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

    @tla_action("S_Release")
    def _answered(self, value: SaveValue, *, ran_user_code: bool = True) -> WorkResult:
        if isinstance(value, Saved) and self._after_saved is not None:
            self._after_saved(R4Target(self._r.workspace_key, f"{self._r.session}-{self._job}"))
        return WorkResult(value, ran_user_code=ran_user_code)

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
