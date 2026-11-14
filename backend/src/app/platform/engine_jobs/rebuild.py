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
workspace's mirror folder (`<app cache>/workspaces/<key>/mirror/`, under the cache folder the engine was started with,
`EngineCommand.app_cache`) and answers its generation's number and
`ledger_version`; a `fresh` answer means the published generation already describes the disk; `paused` means an
unfinished save's `.open` journal was seen at the re-check (no try again: only recovery ends it). The request carries
the civil date and the instant from the sidecar's clock (the engine reads none, INV-07) and the floor for the
generation number. TODO(BL-0126): the floor comes from the open workspace's holder of the highest number published or
served this session; until reads are served it is 0, so after the OS purges the cache under a running app the number
may start again at 1 (no screen reads it yet). TODO(BL-0211): the screens' "Checking…" text until the rebuild ends.

**Who queues it** (`rebuild_submitter`, one helper for every caller): a landed save, a save read from disk as saved,
and since S0118 (BL-0255) a save the gate refused `mirror_stale` and a preview answered `stale` (`unpublished`,
`not_fresh`): a rebuild that ended without publishing (a crashed plugin, a ledger that kept changing, an I/O
refusal, a full queue, the OS removing the cache folder) is then queued again by the next write, so
`WORKSPACE_NOT_READY {WARMING}`'s "wait a moment and try again" comes true. A queued one answers `AlreadyQueued`
(the queued one loads the disk when it runs). A refused submit (`ENGINE_QUEUE_FULL`; `WORKSPACE_NOT_OPEN` after a
switch, INV-31; `ENGINE_UNAVAILABLE` while the app closes) is logged `rebuild.not_queued`, content-free (INV-23); it is
never raised and never changes the asking job's answer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from app.domain.dates import civil_date
from app.platform.clock import Clock
from app.platform.engine import EngineFailure, EngineRefusal, Publish, RebuildAnswer
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_rebuild
from app.platform.observability.events import REBUILD_LOADED, REBUILD_NOT_LOADED, REBUILD_NOT_QUEUED

MAX_TRIES: Final = 3
"""§7: a moved fingerprint runs the load again, at most 3 times in a row."""


@dataclass(frozen=True, slots=True)
class RebuildTarget:
    """What a landed save leaves to rebuild: its workspace's registry key, the ledger's root, the plan's fingerprint
    (its canonical encoding, opaque here), the workspace's lineage id and the app's machine-local folder (the engine
    looks for an `.open` journal under it; it publishes under the app cache folder it was started with), and the
    floor for the generation number (publish.md §3)."""

    workspace_key: str
    root: str
    fingerprint: str
    lineage: str
    app_local_data: Path
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


type NotLoadedWhy = Literal[
    "changing", "engine_failed", "refused", "violation", "paused", "number_failed", "beancount_failed"
]


@dataclass(frozen=True, slots=True)
class NotLoaded:
    """The rebuild did not reach a result: logged, nothing shown, nothing gated, nothing owed. `number_failed` and
    `beancount_failed`: Beancount's own load raised on the ledger (S0098); the file and line it names are never logged
    (INV-23), and no screen shows a rebuild's result yet."""

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
        "workspace_key": target.workspace_key,
        "lineage": target.lineage,
        "floor": target.floor,
        "today": civil_date(now).isoformat(),
        "built_at": now.isoformat(),
    }


def names_its_generation(publish: Publish | None, number: int | None, ledger_version: str | None) -> bool:
    """A published or fresh generation is named by its number and its `ledger_version`, whose `N` is that number;
    anything else names none (S0088 round 1, verifier F5, red team F3: an answer that does not hold together is a
    violation, never logged or shown as a generation)."""
    if publish in ("published", "fresh"):
        return number is not None and ledger_version is not None and ledger_version.startswith(f"{number}/")
    return number is None and ledger_version is None


def _loaded(answer: RebuildAnswer, tries: int) -> Loaded | NotLoaded:
    publish = answer.publish
    if publish is None or (publish == "fresh") != (answer.outcome == "fresh"):
        return NotLoaded("violation")  # a loaded or fresh answer always says what became of it
    if not names_its_generation(publish, answer.number, answer.ledger_version):
        return NotLoaded("violation")
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


def _refusal_holds_together(answer: RebuildAnswer) -> bool:
    """A refusal, and only a refusal, says why and names its file (S0098); only a refusal names a line."""
    refused = answer.outcome == "refused"
    if refused != (answer.refusal is not None) or refused != (answer.refused is not None):
        return False
    return refused or answer.line is None


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
        if isinstance(answer, Violation) or not _refusal_holds_together(answer):
            return NotLoaded("violation")
        if answer.outcome in ("loaded", "fresh"):
            return _loaded(answer, tries)
        if answer.outcome == "paused":
            return NotLoaded("paused")  # an unfinished save: no try again, only recovery ends it (§7)
        if answer.refusal is not None:  # Beancount's own load raised on the ledger: the same in every try
            return NotLoaded(answer.refusal)
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


type QueueRebuild = Callable[[JobContext, RebuildTarget], None]
"""Queue the background rebuild of what a job leaves stale (the module's docstring), from inside that job's work."""


def rebuild_submitter(jobs: EngineJobs, workspace: str, clock: Clock) -> QueueRebuild:
    """The one way a job queues the rebuild of its own workspace (`workspace`, the queue's key; INV-31): queued, or
    the queued one's ticket (`AlreadyQueued`), or a refusal logged and dropped (the module's docstring)."""

    def queue(ctx: JobContext, target: RebuildTarget) -> None:
        out = jobs.submit(rebuild_spec(workspace), rebuild_work(target, clock))
        if isinstance(out, Failed):
            ctx.log(REBUILD_NOT_QUEUED, job=ctx.job_id, workspace_key=target.workspace_key, code=str(out.error.code))

    return queue
