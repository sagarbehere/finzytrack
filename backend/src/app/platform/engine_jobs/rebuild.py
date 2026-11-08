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
saving off (§5.3; step-back area review F2). The result is logged, counts only (INV-23).

**The publish** (S0087, BL-0113; `design/specs/mirror/publish.md` §4): the engine publishes a loaded result into the
workspace's mirror folder (`<app_cache>/workspaces/<key>/mirror/`) and answers its generation's number and
`ledger_version`; a `fresh` answer means the published generation already describes the disk; `paused` means an
unfinished save's `.open` journal was seen at the re-check (no try again: only recovery ends it). The request carries
the civil date and the instant from the sidecar's clock (the engine reads none, INV-07) and the floor for the
generation number. TODO(BL-0126): the floor comes from the open workspace's holder of the highest number published or
served this session; until reads are served it is 0, so after the OS purges the cache under a running app the number
may start again at 1 (no screen reads it yet). TODO(BL-0211): the screens' "Checking…" text until the rebuild ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from app.platform.clock import Clock
from app.platform.engine import EngineFailure, EngineRefusal, Publish, RebuildAnswer
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.scheduler import JobContext, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_rebuild
from app.platform.observability.events import REBUILD_LOADED, REBUILD_NOT_LOADED

MAX_TRIES: Final = 3
"""§7: a moved fingerprint runs the load again, at most 3 times in a row."""


@dataclass(frozen=True, slots=True)
class RebuildTarget:
    """What a landed save leaves to rebuild: its workspace's registry key, the ledger's root, the plan's fingerprint
    (its canonical encoding, opaque here), the workspace's lineage id and the app's folders (the engine looks for an
    `.open` journal under `app_local_data`, and publishes under `app_cache`), and the floor for the generation number
    (publish.md §3)."""

    workspace_key: str
    root: str
    fingerprint: str
    lineage: str
    app_local_data: Path
    app_cache: Path
    floor: int = 0


@dataclass(frozen=True, slots=True)
class Loaded:
    """Beancount's own result for the disk: its counts, the tries it took, and what became of it (`published`, or why
    not); `number` and `ledger_version` name the current generation when it was published or found fresh (counts are
    0 when fresh: nothing was loaded). `removed`, `remove_failed`: the last try's cleanup."""

    entries: int
    errors: int
    tries: int
    publish: Publish
    number: int | None = None
    ledger_version: str | None = None
    removed: int = 0
    remove_failed: int = 0


type NotLoadedWhy = Literal["changing", "engine_failed", "refused", "violation", "paused"]


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


def params_of(target: RebuildTarget, fingerprint: str, clock: Clock) -> dict[str, object]:
    """The `rebuild` request: the civil date where the app runs (INV-07) and the instant, from the sidecar's clock."""
    now = clock.now()
    return {
        "root": target.root,
        "fingerprint": fingerprint,
        "app_local_data": str(target.app_local_data),
        "app_cache": str(target.app_cache),
        "workspace_key": target.workspace_key,
        "lineage": target.lineage,
        "floor": target.floor,
        "today": now.astimezone().date().isoformat(),
        "built_at": now.isoformat(),
    }


def _loaded(answer: RebuildAnswer, tries: int) -> Loaded | NotLoaded:
    publish = answer.publish
    if publish is None or (publish == "fresh") != (answer.outcome == "fresh"):
        return NotLoaded("violation")  # a loaded or fresh answer always says what became of it
    return Loaded(
        answer.entries,
        answer.errors,
        tries,
        publish,
        answer.number,
        answer.ledger_version,
        answer.removed,
        answer.remove_failed,
    )


def run_rebuild(ctx: JobContext, target: RebuildTarget, clock: Clock) -> RebuildResult:
    """The module's docstring: at most `MAX_TRIES` loads, each in a fresh engine, each uncounted."""
    hint = target.fingerprint
    for tries in range(1, MAX_TRIES + 1):
        if tries > 1:
            fresh = ctx.fresh_engine()  # the last try ran user code (§5.1)
            if fresh is not None:
                return _failed(fresh)
        out = ctx.call("rebuild", params_of(target, hint, clock), counted=False)
        if isinstance(out, EngineFailure):
            return _failed(out)
        if isinstance(out, EngineRefusal):
            return NotLoaded("refused")  # an encrypted file met, or an I/O error: logged
        answer = decode_rebuild(out)
        if isinstance(answer, Violation):
            return NotLoaded("violation")
        if answer.outcome in ("loaded", "fresh"):
            return _loaded(answer, tries)
        if answer.outcome == "paused":
            return NotLoaded("paused")  # an unfinished save: no try again, only recovery ends it (§7)
        hint = answer.fingerprint
    return NotLoaded("changing")


def log_result(ctx: JobContext, target: RebuildTarget, result: RebuildResult) -> None:
    """Content-free (INV-23): the job, the workspace key and counts, never a path, an account or an amount."""
    where = {"job": ctx.job_id, "workspace_key": target.workspace_key}
    if isinstance(result, Loaded):
        ctx.log(
            REBUILD_LOADED,
            entries=result.entries,
            errors=result.errors,
            tries=result.tries,
            published=result.publish,
            number=result.number,
            removed=result.removed,
            remove_failed=result.remove_failed,
            **where,
        )
    elif isinstance(result, NotLoaded):
        ctx.log(REBUILD_NOT_LOADED, why=result.why, **where)


def rebuild_work(target: RebuildTarget, clock: Clock) -> Work:
    def work(ctx: JobContext) -> WorkResult:
        result = run_rebuild(ctx, target, clock)
        log_result(ctx, target, result)
        return WorkResult(None if isinstance(result, Cancelled) else result, ran_user_code=True)

    return work
