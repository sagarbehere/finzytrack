"""The rebuild after a save, the sidecar's side (S0070; `design/specs/engine-jobs.md` §2's `rebuild`, §5.3, §6.1
step 10, §6.7, §7; ADR-0068 D1).

After every landed save, and after a save whose outcome was read from disk as saved (`save.submit_save` wires both),
the sidecar queues a background `rebuild`: Beancount's own load of the disk (the engine's `rebuild` op,
`ledger_engine.ledger.rebuild`), so the screens show Beancount's result and its errors right after "saved". Nobody
waits for it, it gates nothing, and it tells no one "saved" or "not saved": the save has already answered.

`run_rebuild` sends `rebuild` with the saved plan's fingerprint; a `moved` answer (the disk changed during the load, or
the load used files outside the set) tries again with the answer's fingerprint in a fresh engine (it ran user code,
§5.1), at most `MAX_TRIES` tries in a row; then `NotLoaded("changing")` (§7's `LEDGER_CHANGING`: the next save or
the focus check's rebuild shows the ledger once it is still). Each call is sent uncounted: an engine death in a rebuild
is no stop toward the bound that makes the engine unavailable, so a plugin that crashes in every load never switches
saving off (§5.3; step-back area review F2). The result is logged, counts only (INV-23); publishing it to the screens
is the mirror's (BL-0113).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from app.platform.engine import EngineFailure, EngineRefusal
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.scheduler import JobContext, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_rebuild
from app.platform.observability.events import REBUILD_LOADED, REBUILD_NOT_LOADED

MAX_TRIES: Final = 3
"""§7: a moved fingerprint runs the load again, at most 3 times in a row."""


@dataclass(frozen=True, slots=True)
class RebuildTarget:
    """What a landed save leaves to rebuild: its workspace's registry key (for the log), the ledger's root and the
    plan's fingerprint (its canonical encoding, opaque here)."""

    workspace_key: str
    root: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Loaded:
    """Beancount's own result for the disk: its counts, and the tries it took."""

    entries: int
    errors: int
    tries: int


type NotLoadedWhy = Literal["changing", "engine_failed", "refused", "violation"]


@dataclass(frozen=True, slots=True)
class NotLoaded:
    """The rebuild did not reach a result: logged, nothing shown, nothing gated, nothing owed."""

    why: NotLoadedWhy


@dataclass(frozen=True, slots=True)
class Cancelled:
    """The job was cancelled (§4): nothing is owed."""


type RebuildResult = Loaded | NotLoaded | Cancelled


def rebuild_spec(workspace: str) -> JobSpec:
    """The background rebuild a landed save queues (§2, §6.1 step 10): nobody waits, unconfirmed; at most one queued
    per workspace (§3); another job's Cancel may end it (§4)."""
    return JobSpec(kind="rebuild", workspace=workspace, waited_for=False, confirmed=False, owner="app")


def _failed(failure: EngineFailure) -> RebuildResult:
    return Cancelled() if failure.kind == "cancelled" else NotLoaded("engine_failed")


def run_rebuild(ctx: JobContext, target: RebuildTarget) -> RebuildResult:
    """The module's docstring: at most `MAX_TRIES` loads, each in a fresh engine, each uncounted."""
    hint = target.fingerprint
    for tries in range(1, MAX_TRIES + 1):
        if tries > 1:
            fresh = ctx.fresh_engine()  # the last try ran user code (§5.1)
            if fresh is not None:
                return _failed(fresh)
        out = ctx.call("rebuild", {"root": target.root, "fingerprint": hint}, counted=False)
        if isinstance(out, EngineFailure):
            return _failed(out)
        if isinstance(out, EngineRefusal):
            return NotLoaded("refused")  # an encrypted file met, or an I/O error: logged
        answer = decode_rebuild(out)
        if isinstance(answer, Violation):
            return NotLoaded("violation")
        if answer.outcome == "loaded":
            # the publish is at the engine's seam (`ledger_engine.ledger.rebuild.run`, BL-0113); logged until then
            return Loaded(answer.entries, answer.errors, tries)
        hint = answer.fingerprint
    return NotLoaded("changing")


def log_result(ctx: JobContext, target: RebuildTarget, result: RebuildResult) -> None:
    """Content-free (INV-23): the job, the workspace key and counts, never a path, an account or an amount."""
    where = {"job": ctx.job_id, "workspace_key": target.workspace_key}
    if isinstance(result, Loaded):
        ctx.log(REBUILD_LOADED, entries=result.entries, errors=result.errors, tries=result.tries, **where)
    elif isinstance(result, NotLoaded):
        ctx.log(REBUILD_NOT_LOADED, why=result.why, **where)


def rebuild_work(target: RebuildTarget) -> Work:
    def work(ctx: JobContext) -> WorkResult:
        result = run_rebuild(ctx, target)
        log_result(ctx, target, result)
        return WorkResult(None if isinstance(result, Cancelled) else result, ran_user_code=True)

    return work
