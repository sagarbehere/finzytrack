"""The copy job, the sidecar's side (S0081; `design/specs/engine-jobs.md` §1 "Commit point", §2's `copy`, §4, §5.3,
§8.1, §8.2, §11; ADR-0064 P2, ADR-0063 decision 1, ADR-0062 decision 2).

The work of a `copy` job: one `copy` request to the job's engine, which reads the source ledger (the legacy locks, its
load, the journals), writes the copy into the new folder, checks that it loads the same, sends `gate`, and writes
`.finzytrack/` last (`ledger_engine.ledger.copy`); the sidecar never reads either folder itself. It is the create's
work (`create.FolderJob`), with the copy's ops and reasons. Its answer becomes the job's value:

- **`Copied(folder)`** once the engine said so, or once the disk said so after the engine died. Registering the new
  workspace is the caller's (BL-0102), as for a create.
- **`NotCopied`** with its reason and what the job left in the folder (`copy_text` words it): no `.finzytrack/` of
  ours stands in the folder.
- **`NotDone(error)`**: `CHANGE_OUTCOME_UNKNOWN` when no engine could read the disk past the commit point, `INTERNAL`
  for a request the engine would not take (nothing read or written).

The request names the legacy write locks to try (the caller's legacy detection, M3) and carries the manifest with the
copy's new lineage id (`app.workspaces.creation.copy_manifest_files`): a copy is a new set of books (ADR-0017)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from app.platform.engine import CreateAnswer, CreateFacts
from app.platform.engine_jobs.create import FolderJob, NewFile, new_job_name
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.save import NotDone
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.errors import ErrorCode, error

type NotCopiedReason = Literal[
    "not_empty",
    "not_a_folder",
    "no_parent",
    "permission",
    "taken",
    "read_back",
    "load_failed",
    "load_outside",
    "manifest_taken",
    "path_moved",
    "io_error",
    "legacy_saving",
    "recovery_blocked",
    "source_missing",
    "source_unreadable",
    "source_failed",
    "encrypted",
    "source_changed",
    "cannot_rewrite",
    "cannot_place",
    "load_differs",
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
        "load_outside",
        "manifest_taken",
        "path_moved",
        "io_error",
        "legacy_saving",
        "recovery_blocked",
        "source_missing",
        "source_unreadable",
        "source_failed",
        "encrypted",
        "source_changed",
        "cannot_rewrite",
        "cannot_place",
        "load_differs",
    }
)
"""The engine's own reasons for a copy (`ledger_engine.ledger.create.Reason`); `stopped` is the sidecar's."""


@dataclass(frozen=True, slots=True)
class CopyRequest:
    """A confirmed copy (§8.2): the target folder (absolute), the source ledger's root (absolute, as the user named
    it), the files of `.finzytrack/` (`copy_manifest_files`: a new lineage id, the root's file name), the app's local
    data folder (the journals) and the legacy write-lock files to try (ADR-0062)."""

    target: str
    source: str
    manifest: tuple[NewFile, ...]
    app_local_data: Path
    legacy_locks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Copied:
    """The copy is complete on disk (`.finzytrack/` durable over files that load the same as the source). Not
    registered yet."""

    folder: str


@dataclass(frozen=True, slots=True)
class NotCopied:
    """No `.finzytrack/` of ours stands in `folder` (the target as requested). `left` as the create's (`NotCreated`):
    what the job kept because it could not be sure each is only its own; None when the folder was not cleaned up or
    listed."""

    reason: NotCopiedReason
    folder: str
    left: tuple[str, ...] | None
    path: str | None = None
    detail: str | None = None
    errno: int | None = None


type CopyValue = Copied | NotCopied | NotDone


def copy_spec() -> JobSpec:
    """A copy the user confirmed: app-scoped, waited for (§2)."""
    return JobSpec(kind="copy", workspace=None, waited_for=True, confirmed=True, owner="user")


def params_of(r: CopyRequest, job: str) -> dict[str, object]:
    return {
        "job": job,
        "target": r.target,
        "source": r.source,
        "manifest": [{"path": f.path, "text": f.text} for f in r.manifest],
        "app_local_data": str(r.app_local_data),
        "legacy_locks": list(r.legacy_locks),
    }


def answer_of(a: CreateAnswer) -> CopyValue:
    """The engine's `copy` answer as the job's value (§11)."""
    if a.outcome == "created":
        return Copied(a.folder)
    reason = a.reason if a.reason in REASONS else "io_error"
    return NotCopied(cast("NotCopiedReason", reason), a.folder, a.left, a.path, a.detail, a.errno)


def copy_outcome(facts: CreateFacts, *, gated: bool, folder: str) -> CopyValue:
    """§1 "resolved from disk", as the create's (`create.create_outcome`): `.finzytrack` holding our bytes is copied;
    past the commit point a folder that could not be read is unknown; anything else is "stopped", naming every entry
    in the folder."""
    if facts.manifest == "ours":
        return Copied(folder)
    if gated and facts.manifest == "unknown":
        return NotDone(error(ErrorCode.CHANGE_OUTCOME_UNKNOWN))
    return NotCopied("stopped", folder, facts.left, errno=facts.errno)


def _from_facts(facts: CreateFacts, gated: bool, folder: str) -> CopyValue:
    return copy_outcome(facts, gated=gated, folder=folder)


def copy_work(request: CopyRequest) -> Work:
    """The work of a copy job."""

    def work(ctx: JobContext) -> WorkResult:
        params = params_of(request, new_job_name())  # the copy and its outcome read name the same job
        return FolderJob(ctx, "copy", "copy_outcome", params, request.target, answer_of, _from_facts).run()

    return work


def submit_copy(jobs: EngineJobs, request: CopyRequest) -> JobTicket | Failed:
    """Queue a copy. TODO(BL-0102): register the folder once it is `Copied` (§8.2 step 8, INV-29)."""
    return jobs.submit(copy_spec(), copy_work(request))
