"""The open job, the sidecar's side (S0088; `design/specs/engine-jobs.md` §2's `open`, §4, §5.1, §5.3, §7, §11;
ADR-0059; BL-0152).

When a workspace opens (the switch, the boot and Reopen: BL-0154, M2), the open job publishes its mirror before any
save is offered: the save's gate refuses `mirror_stale` while no fresh generation is published
(`design/specs/mirror/publish.md` §7), which is the case on a fresh install, after the OS purged the cache, and after
an outside edit. `submit_open` queues it as the switch's own job (waited-for); its value is:

- **`Opened`**: the engine loaded the ledger and published its generation (`published`), or found the published one
  fresh (`fresh`); `mirror_ready` says whether saves can pass the gate. A load whose build was not published
  (`inexact`, `incomplete`, `replaced`) is `Opened` too, with `mirror_ready` False: the caller opens the workspace
  `WARMING` (ADR-0015) and saves stay refused until a generation is published.
- **`NotOpened(error)`**: `LEDGER_CHANGING` after `MAX_TRIES` tries in a row each found the ledger moved (§7, Try
  again); `WRITES_PAUSED {unfinished_save}` when this workspace has an `.open` journal (no try again: only recovery
  ends it, and recovery runs before any open, INV-32, so meeting one here is a race with another program or a bug);
  `INTERNAL` for an engine refusal or an answer that does not hold together.
- **`EncryptedFile(path)`**: the ledger includes an encrypted file, which v0.4.0 never reads (ADR-0059); the open's
  screen names it (BL-0154).

An engine death ends the job as any waited-for read's (§5.3: `ENGINE_STOPPED` with Try again, from the scheduler;
each try is counted, since a user waits for it); a Cancel ends it with nothing to undo (§4). Each try after the first
runs in a fresh engine (the last one ran user code, §5.1). The open writes only the workspace's mirror folder in the
app's cache (owner, 2026-11-08, "Only its own mirror"). The request carries the civil date and the instant from the
sidecar's clock (the engine reads none, INV-07) and the floor for the generation number; TODO(BL-0126): the floor
comes from the open workspace's holder of the highest number published or served this session (0 until then). The
result is logged, counts only (INV-23): never the encrypted file's path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from app.domain.dates import civil_date
from app.domain.errors import DomainError
from app.platform.clock import Clock
from app.platform.engine import EngineFailure, EngineRefusal, OpenAnswer, Publish
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.rebuild import names_its_generation
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_open
from app.platform.errors import ErrorCode, WritesPausedDetails, error
from app.platform.observability.events import OPEN_LOADED, OPEN_NOT_LOADED

MAX_TRIES: Final = 3
"""§7: a moved fingerprint runs the job again, at most 3 times in a row."""
READY: Final[frozenset[str]] = frozenset({"published", "fresh"})


@dataclass(frozen=True, slots=True)
class OpenTarget:
    """The workspace to open: its registry key, the ledger's root, its lineage id and the app's machine-local folder
    (the engine looks for an `.open` journal under it; it publishes under the app cache folder it was started with),
    and the floor for the generation number (publish.md §3)."""

    workspace_key: str
    root: str
    lineage: str
    app_local_data: Path
    floor: int = 0


@dataclass(frozen=True, slots=True)
class Opened:
    """Beancount's own result for the disk (its counts; 0 when `fresh`), the tries it took, and what became of the
    mirror (`publish`); `number` and `ledger_version` name the current generation when published or fresh."""

    entries: int
    errors: int
    tries: int
    publish: Publish
    number: int | None = None
    ledger_version: str | None = None
    removed: int = 0
    remove_failed: int = 0

    @property
    def mirror_ready(self) -> bool:
        """Whether a fresh generation is published, so the save's gate can pass (publish.md §7)."""
        return self.publish in READY


@dataclass(frozen=True, slots=True)
class NotOpened:
    """The open did not reach a result: a registry error with its own words (§11)."""

    error: DomainError


@dataclass(frozen=True, slots=True)
class EncryptedFile:
    """The ledger includes an encrypted file, never read (ADR-0059): `path` names it for the open's screen."""

    path: str


type OpenValue = Opened | NotOpened | EncryptedFile
type NotLoadedWhy = Literal["changing", "paused", "encrypted", "refused", "violation"]


def open_spec(workspace: str) -> JobSpec:
    """The switch's, the boot's or a Reopen's own `open` (§2, §3): waited-for, unconfirmed (it writes no ledger)."""
    return JobSpec(kind="open", workspace=workspace, waited_for=True, confirmed=False, owner="switch")


def params_of(target: OpenTarget, clock: Clock) -> dict[str, object]:
    """The `open` request: the civil date where the app runs (INV-07) and the instant, from the sidecar's clock."""
    now = clock.now()
    return {
        "root": target.root,
        "app_local_data": str(target.app_local_data),
        "workspace_key": target.workspace_key,
        "lineage": target.lineage,
        "floor": target.floor,
        "today": civil_date(now).isoformat(),
        "built_at": now.isoformat(),
    }


def _holds_together(a: OpenAnswer) -> bool:
    """A loaded answer says what became of its build (never `fresh`); a fresh one says `fresh`; a published or fresh
    generation is named by its number and `ledger_version` (`names_its_generation`); only a refusal, and every
    refusal, names its file."""
    if (a.outcome == "refused") != (a.refused is not None):
        return False
    if not names_its_generation(a.publish, a.number, a.ledger_version):
        return False
    if a.outcome == "loaded":
        return a.publish is not None and a.publish != "fresh"
    if a.outcome == "fresh":
        return a.publish == "fresh"
    return a.publish is None


def _not_opened(code: ErrorCode, details: object | None = None) -> NotOpened:
    return NotOpened(error(code, details))


def run_open(ctx: JobContext, target: OpenTarget, clock: Clock) -> tuple[OpenValue, NotLoadedWhy | None] | None:
    """The module's docstring: at most `MAX_TRIES` tries, each in a fresh engine after the first. None when an engine
    failed or the job was cancelled (the scheduler answers the job then); else the value and, when it is not
    `Opened`, why (for the log)."""
    for tries in range(1, MAX_TRIES + 1):
        if tries > 1 and ctx.fresh_engine() is not None:
            return None  # the last try ran user code (§5.1); no engine came, or the job was cancelled
        out = ctx.call("open", params_of(target, clock))
        if isinstance(out, EngineFailure):
            return None
        if isinstance(out, EngineRefusal):
            return _not_opened(ErrorCode.INTERNAL), "refused"
        a = decode_open(out)
        if isinstance(a, Violation) or not _holds_together(a):
            return _not_opened(ErrorCode.INTERNAL), "violation"
        match a.outcome:
            case "loaded" | "fresh":
                assert a.publish is not None  # noqa: S101 (`_holds_together`)
                opened = Opened(
                    a.entries, a.errors, tries, a.publish, a.number, a.ledger_version, a.removed, a.remove_failed
                )
                return opened, None
            case "paused":
                return _not_opened(ErrorCode.WRITES_PAUSED, WritesPausedDetails("unfinished_save", None)), "paused"
            case "refused":
                assert a.refused is not None  # noqa: S101 (`_holds_together`)
                return EncryptedFile(a.refused), "encrypted"
            case "moved":
                continue
    return _not_opened(ErrorCode.LEDGER_CHANGING), "changing"


def log_result(ctx: JobContext, target: OpenTarget, value: OpenValue, why: NotLoadedWhy | None) -> None:
    """Content-free (INV-23): the job, the workspace key and counts, never a path, an account or an amount."""
    where = {"job": ctx.job_id, "workspace_key": target.workspace_key}
    if isinstance(value, Opened):
        ctx.log(
            OPEN_LOADED,
            entries=value.entries,
            errors=value.errors,
            tries=value.tries,
            published=value.publish,
            number=value.number,
            removed=value.removed,
            remove_failed=value.remove_failed,
            **where,
        )
    elif why is not None:
        ctx.log(OPEN_NOT_LOADED, why=why, **where)


def open_work(target: OpenTarget, clock: Clock) -> Work:
    def work(ctx: JobContext) -> WorkResult:
        out = run_open(ctx, target, clock)
        if out is None:
            return WorkResult(None)
        value, why = out
        log_result(ctx, target, value, why)
        return WorkResult(value, ran_user_code=True)

    return work


def submit_open(jobs: EngineJobs, workspace: str, target: OpenTarget, *, clock: Clock) -> JobTicket | Failed:
    """Queue the open of `workspace`: the switch's, the boot's or a Reopen's own job, which may name a workspace that is
    not open yet and starts only inside their window (§3: `EngineJobs.exclusive_began`, which the switch and the boot
    open, BL-0154)."""
    return jobs.submit(open_spec(workspace), open_work(target, clock))
