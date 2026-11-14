"""The write contract's preview and confirm, the sidecar's use cases (S0117; ADR-0008 steps 4-7, ADR-0014, ADR-0059,
ADR-0063, ADR-0067, ADR-0068 D1; ARCHITECTURE.md §9.2 steps 3-4; `design/specs/engine-jobs.md` §2, §6.1, §11;
BL-0106). No route calls them yet: each mutator's slice adds its `…:preview` route, and the shared
`plans/{id}:confirm` comes with the first of them (roadmap M1b: no user-facing mutator).

**The owner's ownership rule** (2026-11-14, the S0117 brief), and where each part is held:
1. A confirm writes exactly the files and bytes of one plan this app's preview prepared, held in the `PlanStore`,
   never sent back by the client (`submit_preview` keeps the engine's plan; `submit_confirm` names it by id and saves
   its own texts).
2. Bound to its workspace and ledger state: found only in its workspace and for its root (`PLAN_EXPIRED` otherwise);
   expired, used or dropped by a switch: `PLAN_EXPIRED`; made on another mirror generation than the published one:
   `PREVIEW_STALE`, read inside the save's job (one job at a time, so no publish comes between that read and the
   save's gate); its fingerprint: the save's gate (engine-jobs §6.1 step 4). The plan is taken from the store when the
   save's job starts, so it is used once, whatever the save answers.
3. The confirm carries the preview's hash (`PREVIEW_STALE` otherwise); the acknowledgements must be exactly the new
   error keys the preview listed, as a set (`ACK_REQUIRED {error_keys}` otherwise; the plan stays for the user's Save
   anyway). An accounting error never blocks (ADR-0067).
4. Every file as the preview found it: the save's gate (`LEDGER_EXTERNALLY_CHANGED`).
5. Never a file outside the plan or outside the ledger set, never an encrypted file: the save writes only the plan's
   files, and refuses a change that is not in the plan's fingerprint at its `pre` bytes (`ledger_engine.ledger.save`,
   before anything is written); `plan_problems`, at the preview (an engine answer that breaks it is never kept) and
   again at the confirm, refuses an encrypted file by name and a text that is not the one the preview checked.
6. Until a mutator is promoted, its plans are confirmed only on test copies: `promotion_refusal`
   (`UNPROMOTED_MUTATOR_ON_REAL_LEDGER`), refusing by default.
7. "Saved" only once the save is durable, and "unchanged" only after a re-hash: the save's own (S0063).
The order at the confirm: the plan, its hash, its files, the guard, then the acknowledgements, so the user is never
asked to Save anyway a change that would be refused anyway. Every refusal before the save's job writes nothing; an
exception in a step is not caught here (the edge answers `INTERNAL`, ARCHITECTURE §6.3) and queues nothing; one inside
the job before the save ends it `INTERNAL` (the scheduler's rule): each step fails closed (INV-22).

**The promotion guard** (ADR-0014). `PROMOTED` names the mutators the owner has cleared for real ledgers; it is code,
changed only with the owner's signed clearance page (ADR-0045), never a setting or a request field: empty today. An
unpromoted mutator's plan is confirmed only when the workspace carries a test-copy mark (`WriteTarget.copy`) and every
file the plan writes, resolved through links as the save resolves them (ADR-0008 step 5: a linked file is written at
its real path), lies inside the copy's folder, resolved too. **No workspace carries the mark yet:** the manifest's
format 1 has no such field (an unknown field written by hand is ignored, EP-14), and the mark comes with the test copy
(CAP-WS-TEST-COPY, M3), so no product code builds a `WriteTarget` with one, and every confirm on a real workspace is
refused. The target is built by the sidecar from its own state (the open workspace's container, M2), never from a
request. ADR-0014's second half, "and not in the file set of any non-copy workspace", needs the registry and the mark:
TODO(BL-0267). Limit: the guard resolves the paths when the confirm is asked; a link made inside a test copy after
that, pointing out of it, is met only by the save's gate when it changes the plan's fingerprint.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.domain.errors import DomainError
from app.mirror.files import generation_number, pin
from app.platform.clock import Clock
from app.platform.engine import PreviewError
from app.platform.engine_jobs.preview import NotPrepared, Prepared, PreviewOrder, PreviewTarget
from app.platform.engine_jobs.preview import submit_preview as submit_preview_job
from app.platform.engine_jobs.rebuild import RebuildTarget, rebuild_spec, rebuild_work
from app.platform.engine_jobs.save import ChangeText, NotDone, SaveRequest, save_spec, save_work
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.errors import (
    AckRequiredDetails,
    ContractStepDetails,
    EncryptedFileDetails,
    ErrorCode,
    UnpromotedDetails,
    error,
)
from app.platform.ids import Ids
from app.platform.plans import PlanId, PlannedFile, PlanStore, PreviewHash, WritePlan, new_plan

PROMOTED: Final[frozenset[str]] = frozenset()
"""The mutators promoted for real ledgers (ADR-0014), by intent-table id: none yet."""

ENCRYPTED_SUFFIXES: Final = (".gpg", ".asc")
"""ADR-0059 (the engine's `overlay.ENCRYPTED_SUFFIXES`, any case): never written."""


@dataclass(frozen=True, slots=True)
class CopyMark:
    """The workspace is a test copy (CAP-WS-TEST-COPY): the folder it was copied into."""

    folder: Path


@dataclass(frozen=True, slots=True)
class WriteTarget:
    """The open workspace a change is for, as the sidecar knows it (never from a request): its registry key, the
    ledger's root, its lineage id, the app's machine-local and cache folders, and its test-copy mark (None: a real
    workspace; no workspace has one before M3)."""

    workspace: str
    root: str
    lineage: str
    app_local_data: Path
    app_cache: Path
    copy: CopyMark | None = None


def mirror_generation(target: WriteTarget) -> int | None:
    """The number of the workspace's published mirror generation now (`current.db`, publish.md §7), or None."""
    pinned = pin(str(target.app_cache / "workspaces" / target.workspace / "mirror"))
    return generation_number(pinned) if isinstance(pinned, str) else None


@dataclass(frozen=True, slots=True)
class WriteServices:
    """What the use cases need from the user container: the engine jobs, the plan store, the clock, the ids, this
    session's id (the save's record names, engine-jobs §6.5) and how to read the published generation."""

    jobs: EngineJobs
    plans: PlanStore
    clock: Clock
    ids: Ids
    session: str
    published_generation: Callable[[WriteTarget], int | None] = mirror_generation


@dataclass(frozen=True, slots=True)
class Previewed:
    """A kept plan, as the preview answers it (ARCHITECTURE §4.2's `WritePlan`): its id and hash, the files with their
    new texts, the new errors (listed, never a refusal: ADR-0067) and Beancount's error count of the candidate."""

    plan_id: PlanId
    preview_hash: PreviewHash
    files: tuple[PlannedFile, ...]
    new_errors: tuple[PreviewError, ...]
    errors: int


# -- rule 5: the plan's own files -------------------------------------------------------------------------------------


def _check_failed() -> DomainError:
    return error(ErrorCode.WRITE_REFUSED_CHECK_FAILED, ContractStepDetails("files"))


def plan_problems(plan: WritePlan) -> DomainError | None:
    """Rule 5, the part only the sidecar can check: None when the plan writes at least one file, none of them
    encrypted by its name (`ENCRYPTED_FILE_NOT_EDITABLE`), and each text the one the preview checked (its SHA-256 is
    the plan's `post`: the save receives only the text); else `WRITE_REFUSED_CHECK_FAILED {files}`. The rest of rule 5
    is the engine save's, before anything is written, and is not repeated here: every change must be in the plan's
    fingerprint at its `pre` bytes, by an absolute normal path, each once (`ledger.save` refuses the plan otherwise:
    `INTERNAL`, a bug, since only our own preview makes plans), and a name that resolves to an encrypted file is
    refused at its step 0. A file the save would create is not possible here: the engine's preview edits only files of
    the generation it read (S0116), so every planned file has its `pre`."""
    if not plan.files:
        return _check_failed()
    for f in plan.files:
        if f.path.lower().endswith(ENCRYPTED_SUFFIXES):
            return error(ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE, EncryptedFileDetails(f.path))
    for f in plan.files:
        if _sha256(f.text) != f.post:
            return _check_failed()
    return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- rule 6: the promotion guard --------------------------------------------------------------------------------------


def _inside(folder: str, path: str) -> bool:
    return posixpath.commonpath([folder, path]) == folder


def promotion_refusal(plan: WritePlan, target: WriteTarget) -> DomainError | None:
    """Rule 6 (the module's docstring): None when the mutator is promoted, or the workspace is a test copy and every
    file of the plan, resolved through links (where the save writes it), lies inside the copy's folder, resolved; else
    `UNPROMOTED_MUTATOR_ON_REAL_LEDGER`. A resolution that fails (a missing file, a loop) refuses (INV-22)."""
    if plan.mutator in PROMOTED:
        return None
    refused = error(ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER, UnpromotedDetails(plan.mutator))
    if target.copy is None:
        return refused
    try:
        folder = os.path.realpath(target.copy.folder, strict=True)
        for f in plan.files:
            if not _inside(folder, os.path.realpath(f.path, strict=True)):
                return refused
    except (OSError, ValueError):
        return refused
    return None


# -- the preview ------------------------------------------------------------------------------------------------------


def submit_preview(
    services: WriteServices, target: WriteTarget, generation: int, mutator: str, request: Mapping[str, object]
) -> JobTicket | Failed:
    """Queue the preview of `mutator`'s `request`, resolved on the published generation `generation`. Its value is
    `Previewed` (the plan kept in the store) or `NotPrepared(error)` (`app.platform.engine_jobs.preview`; an engine
    plan that breaks rule 5 is `NotPrepared` too, and never kept)."""

    def keep(prepared: Prepared) -> Previewed | NotPrepared:
        files = tuple(PlannedFile(f.path, f.pre, f.post, f.text) for f in prepared.files)
        plan = new_plan(
            plan_id=PlanId(services.ids.new_id()),
            workspace=target.workspace,
            root=target.root,
            mutator=mutator,
            generation=generation,
            fingerprint=prepared.fingerprint,
            files=files,
            new_error_keys=tuple(e.key for e in prepared.new_errors),
            made_at=services.clock.now(),
        )
        problem = plan_problems(plan)
        if problem is not None:
            return NotPrepared(problem)
        services.plans.add(plan)
        return Previewed(plan.plan_id, plan.preview_hash, files, prepared.new_errors, prepared.errors)

    where = PreviewTarget(target.workspace, target.root, target.lineage, target.app_local_data)
    return submit_preview_job(services.jobs, where, PreviewOrder(generation, mutator, request), keep)


# -- the confirm ------------------------------------------------------------------------------------------------------


def _refused(code: ErrorCode, details: object | None = None) -> NotDone:
    return NotDone(error(code, details))


def submit_confirm(
    services: WriteServices,
    target: WriteTarget,
    plan_id: str,
    preview_hash: str,
    *,
    acknowledged: frozenset[str],
) -> JobTicket | Failed | NotDone:
    """Confirm a kept plan (the module's docstring): `NotDone(error)` for a refusal before the queue, `Failed` when
    the queue refuses the job, else the save job's ticket, whose value is the save's (`Saved` or `NotDone`)."""
    plan = services.plans.find(target.workspace, plan_id)
    if plan is None or plan.root != target.root:
        return _refused(ErrorCode.PLAN_EXPIRED)
    if plan.preview_hash != preview_hash:
        return _refused(ErrorCode.PREVIEW_STALE)
    problem = plan_problems(plan)
    if problem is None:
        problem = promotion_refusal(plan, target)
    if problem is not None:
        return NotDone(problem)
    listed = tuple(dict.fromkeys(plan.new_error_keys))
    if acknowledged != frozenset(listed):
        return _refused(ErrorCode.ACK_REQUIRED, AckRequiredDetails(error_keys=listed))
    return services.jobs.submit(save_spec(target.workspace), _confirm_work(services, target, plan))


def _confirm_work(services: WriteServices, target: WriteTarget, plan: WritePlan) -> Work:
    request = SaveRequest(
        session=services.session,
        workspace_key=target.workspace,
        lineage=target.lineage,
        root=plan.root,
        changes=tuple(ChangeText(f.path, f.pre, f.text) for f in plan.files),
        fingerprint=plan.fingerprint,
        app_local_data=target.app_local_data,
    )

    def queue_rebuild(rebuild: RebuildTarget) -> None:
        """§6.1 step 10 (ADR-0068 D1), as `save.submit_save` queues it; its refusal is BL-0255's."""
        services.jobs.submit(rebuild_spec(target.workspace), rebuild_work(rebuild, services.clock))

    save = save_work(request, after_saved=queue_rebuild)

    def work(ctx: JobContext) -> WorkResult:
        if services.plans.take(target.workspace, plan.plan_id) is not plan:
            return WorkResult(_refused(ErrorCode.PLAN_EXPIRED), ran_user_code=False)  # used, expired or dropped
        if services.published_generation(target) != plan.generation:
            return WorkResult(_refused(ErrorCode.PREVIEW_STALE), ran_user_code=False)
        return save(ctx)

    return work
