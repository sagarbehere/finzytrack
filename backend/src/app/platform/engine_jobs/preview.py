"""The preview job, the sidecar's side (S0117; `design/specs/engine-jobs.md` §2's `preview`, §5.1, §7, §11; ADR-0008
steps 1-3 as amended by ADR-0067 and ADR-0068 D1; BL-0106).

The work of a `preview` job: the engine's `preview` op (S0116, `ledger_engine.ledger.preview`), which plans the change
a mutator's request asks for on the published mirror generation the request was resolved on, checks it with the
write-safety contract's steps 1-3 and writes nothing. Its answer becomes the job's value:

- **`Prepared`**: the write plan (its files with their texts and hashes, its fingerprint, the new errors, Beancount's
  error count of the candidate) and the tries it took. The use case (`app.workspaces.writes`) checks it and keeps it
  in the `PlanStore`.
- **`NotPrepared(error)`**, a registry error:
  - the contract refused (ARCHITECTURE §6.1's `ContractRefused`, all at 422): an encrypted file (`apply`'s
    `encrypted_file`, or an encrypted file the load met: ADR-0059) is `ENCRYPTED_FILE_NOT_EDITABLE {path}`; a text
    that is not the intended change (steps `apply`, `parse`, `diff`, `spans`) is
    `WRITE_REFUSED_UNINTENDED_CHANGE {step}`; a check that could not run (the producer's plan, the load, a fault or an
    undecidable result in any step: INV-22) is `WRITE_REFUSED_CHECK_FAILED {step}`;
  - `stale` (the published generation is not the plan's baseline: none, another one, or not fresh) is
    `WORKSPACE_NOT_READY {WARMING}`: the caller plans again once a fresh generation is published (BL-0106). A
    `not_fresh` answer whose generation has a stamp of this ledger carries it as a hint, and the job queues the
    workspace's background rebuild with it (`rebuild.rebuild_submitter`; S0118, BL-0255), so "wait a moment and try
    again" comes true once that rebuild has published, even after one that ended without publishing; a refused
    submit is logged and changes nothing. Every other `stale` answer queues none (S0118 round 1, red team RT-F1):
    `unpublished` and a stamp that is not this ledger's or cannot be read leave no set that names the ledger's include
    patterns, so a reopen builds the mirror (engine-jobs §15); `other_generation` may be fresh, and the caller plans
    again on the current generation;
  - `paused` (an unfinished save's `.open` journal) is `WRITES_PAUSED {earlier_unfinished}`;
  - `moved` runs the job again in a fresh engine (the try ran the ledger's plugins, §5.1), at most `MAX_TRIES` tries
    in a row (§7), then `LEDGER_CHANGING`;
  - an engine refusal, an answer that cannot be decoded or does not hold together: `INTERNAL`, never a plan.
An engine death ends the job as any waited-for read's (§5.3: `ENGINE_STOPPED`, from the scheduler); a Cancel ends it
with nothing to undo (§4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.domain.errors import DomainError
from app.platform.clock import Clock
from app.platform.engine import EngineFailure, EngineRefusal, PreviewAnswer, PreviewError, PreviewFile
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.rebuild import QueueRebuild, RebuildTarget, rebuild_submitter
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_preview
from app.platform.errors import (
    ContractStepDetails,
    EncryptedFileDetails,
    ErrorCode,
    WorkspaceNotReadyDetails,
    WritesPausedDetails,
    error,
)

MAX_TRIES: Final = 3
"""§7: a moved ledger runs the job again, at most 3 times in a row."""
TEXT_STEPS: Final[frozenset[str]] = frozenset({"apply", "parse", "diff", "spans"})
"""The steps that compare the text with the intended change (ADR-0008 steps 1, 2, 2b and the splice)."""
NOT_RUN: Final[frozenset[str]] = frozenset({"fault", "undecidable"})
"""Reasons that say a step did not run to a decision (INV-22): a check failed, whatever the step."""
ENCRYPTED: Final[frozenset[tuple[str, str]]] = frozenset({("apply", "encrypted_file"), ("load", "encrypted")})
HINTED: Final[frozenset[str]] = frozenset({"not_fresh"})
"""The `stale` reasons that may carry a hint and queue the rebuild (BL-0255, S0118 round 1): a generation of this
ledger that no longer describes the disk; it carries one only when its stamp is this ledger's and can be read."""


@dataclass(frozen=True, slots=True)
class PreviewTarget:
    """The workspace: its registry key, the ledger's root, its lineage id and the app's machine-local folder (the
    engine looks for an `.open` journal under it; it reads the mirror from the cache folder it was started with)."""

    workspace_key: str
    root: str
    lineage: str
    app_local_data: Path


@dataclass(frozen=True, slots=True)
class PreviewOrder:
    """What to preview: the generation the request was resolved on, the mutator (an intent-table id) and its own
    request (JSON)."""

    generation: int
    mutator: str
    request: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Prepared:
    files: tuple[PreviewFile, ...]
    fingerprint: str
    new_errors: tuple[PreviewError, ...]
    errors: int
    tries: int


@dataclass(frozen=True, slots=True)
class NotPrepared:
    error: DomainError


type PreviewValue = Prepared | NotPrepared


def preview_spec(workspace: str) -> JobSpec:
    """A preview the user waits for; it writes nothing (§2)."""
    return JobSpec(kind="preview", workspace=workspace, waited_for=True, confirmed=False, owner="user")


def params_of(target: PreviewTarget, order: PreviewOrder) -> dict[str, object]:
    return {
        "root": target.root,
        "app_local_data": str(target.app_local_data),
        "workspace_key": target.workspace_key,
        "lineage": target.lineage,
        "generation": order.generation,
        "mutator": order.mutator,
        "request": dict(order.request),
    }


def _holds_together(a: PreviewAnswer) -> bool:
    """Only a prepared plan has files, and it has a fingerprint; besides it, only a `stale` answer with a reason in
    `HINTED` may carry one (its hint, S0118 round 1), with or without; only a refusal names its step and reason, and
    every refusal names both; only a stale answer says why it is stale; a refusal lists no new error."""
    prepared, refused, stale = a.outcome == "prepared", a.outcome == "refused", a.outcome == "stale"
    may_hint = stale and a.stale in HINTED
    if prepared != bool(a.files) or (prepared and a.fingerprint is None):
        return False
    if not (prepared or may_hint) and a.fingerprint is not None:
        return False
    if refused != (a.step is not None) or refused != (a.reason is not None):
        return False
    if stale != (a.stale is not None):
        return False
    return prepared or not a.new_errors


def _not_prepared(code: ErrorCode, details: object | None = None) -> NotPrepared:
    return NotPrepared(error(code, details))


def refusal_of(a: PreviewAnswer) -> NotPrepared:
    """A refused answer (`_holds_together`: its step and reason are set) as the contract's code (the docstring)."""
    step, reason = a.step, a.reason
    assert step is not None  # noqa: S101 (`_holds_together`)
    assert reason is not None  # noqa: S101 (`_holds_together`)
    if (step, reason) in ENCRYPTED:
        return _not_prepared(ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE, EncryptedFileDetails(a.path))
    if step in TEXT_STEPS and reason not in NOT_RUN:
        return _not_prepared(ErrorCode.WRITE_REFUSED_UNINTENDED_CHANGE, ContractStepDetails(step))
    return _not_prepared(ErrorCode.WRITE_REFUSED_CHECK_FAILED, ContractStepDetails(step))


def run_preview(
    ctx: JobContext, target: PreviewTarget, order: PreviewOrder, queue_rebuild: QueueRebuild | None = None
) -> PreviewValue | None:
    """The module's docstring. None when an engine failed or the job was cancelled (the scheduler answers then)."""
    for tries in range(1, MAX_TRIES + 1):
        if tries > 1 and ctx.fresh_engine() is not None:
            return None  # the last try ran user code (§5.1); no engine came, or the job was cancelled
        out = ctx.call("preview", params_of(target, order))
        if isinstance(out, EngineFailure):
            return None
        if isinstance(out, EngineRefusal):
            return _not_prepared(ErrorCode.INTERNAL)
        a = decode_preview(out)
        if isinstance(a, Violation) or not _holds_together(a):
            return _not_prepared(ErrorCode.INTERNAL)
        match a.outcome:
            case "prepared":
                assert a.fingerprint is not None  # noqa: S101 (`_holds_together`)
                return Prepared(a.files, a.fingerprint, a.new_errors, a.errors, tries)
            case "refused":
                return refusal_of(a)
            case "stale":
                if a.fingerprint is not None and queue_rebuild is not None:  # `HINTED` (`_holds_together`)
                    t = target
                    queue_rebuild(
                        ctx, RebuildTarget(t.workspace_key, t.root, a.fingerprint, t.lineage, t.app_local_data)
                    )
                return _not_prepared(ErrorCode.WORKSPACE_NOT_READY, WorkspaceNotReadyDetails(phase="WARMING"))
            case "paused":
                return _not_prepared(ErrorCode.WRITES_PAUSED, WritesPausedDetails("earlier_unfinished", None))
            case "moved":
                continue
    return _not_prepared(ErrorCode.LEDGER_CHANGING)


type AfterPrepared = Callable[[Prepared], object]


def preview_work(
    target: PreviewTarget,
    order: PreviewOrder,
    then: AfterPrepared | None = None,
    *,
    queue_rebuild: QueueRebuild | None = None,
) -> Work:
    """The work of a preview job. `then` turns a `Prepared` value into the job's value (the use case keeps the plan);
    if it raises, the job ends `INTERNAL` (the scheduler's rule) and nothing is kept. `queue_rebuild` is told what to
    rebuild after a `stale` answer with a hint (BL-0255)."""

    def work(ctx: JobContext) -> WorkResult:
        value = run_preview(ctx, target, order, queue_rebuild)
        if value is None:
            return WorkResult(None)
        if isinstance(value, Prepared) and then is not None:
            return WorkResult(then(value), ran_user_code=True)
        return WorkResult(value, ran_user_code=True)

    return work


def submit_preview(
    jobs: EngineJobs, target: PreviewTarget, order: PreviewOrder, then: AfterPrepared | None = None, *, clock: Clock
) -> JobTicket | Failed:
    """Queue a preview whose `stale` answer with a hint queues the workspace's rebuild (BL-0255)."""
    queue = rebuild_submitter(jobs, target.workspace_key, clock)
    return jobs.submit(preview_spec(target.workspace_key), preview_work(target, order, then, queue_rebuild=queue))
