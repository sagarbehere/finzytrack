"""Recover-all, the sidecar's side (S0062; `design/specs/engine-jobs.md` §4's `recover_all` row, §8.1 and its
"Afterwards", §9.1 step 6, §9.2; ADR-0063 decision 2; INV-32).

The work of a `recover_all` job: every step on disk is an engine op (`recover_list`, `recover_try`, `recover_act`)
answered only by an engine that holds the engine lock, so the sidecar never reads a journal or a ledger file itself
(BL-0151's handshake-to-`flock` gap is closed by construction). The work orders them:

1. **The listing** (`recover_list`): every `.open` journal after a `durable_sync` of each journal folder; `.part`
   records removed. An engine's death before it: a fresh engine lists again (model `rc_spawn` then `rc_init`). Then
   `JobContext.listed()`: from now on a Cancel ends only the engine (§4).
2. **The actions** the blocked screen asked for (Keep the files as they are now, Delete the kept copy, Delete it), in
   order, each on a journal the listing found `.open`; a kept journal is not tried.
3. **Each journal still `.open` is tried** (`recover_try`), in the listing's order, in the same engine (it runs no
   user code). A record that cannot be read is not tried: it blocks (`stopped`, cause `unreadable`).

How each step can end, and what the journal in hand counts as (§8.1): the try's own answer (`resolved`, `stopped`,
`failed`); our Cancel (`cancelled`: the call fails, the work asks a fresh engine for the next one); an engine's death
or a reply that is not of the op's shape (`failed`, cause `engine_stopped`); no engine to be had (the stop bound, an
incompatible engine, a failed lock wait: `fresh_engine` fails, and every journal not yet tried is `not_tried`). So
**every listed `.open` journal ends the run resolved, kept by an action of this run, or blocking with exactly one
reason**; none is skipped (INV-32; model mutants `crash_unmarked`, `untried_unmarked`, `cancel_stops_all`).

**The report** (`Report`) is the job's value: what the boot's or the switch's open needs (BL-0152, BL-0154): the
journals undone (for "Finzytrack undid an interrupted save in *Personal*."), the blocking ones with their reason,
cause, file, entry, ACL entry and error number and what stands on disk now (`inspection`), and the folders that could
not be listed. `blocking_for` matches it to a workspace (§8.1 "Afterwards", ADR-0063): a journal blocks a workspace
whose registry key or lineage id it records, or whose ledger set shares one of its files by `path_key`; a record
that cannot be read, a journal folder that could not be listed, or a run that never listed (no report) blocks every
open (fail closed). Retry and Back are this work run again (§9.1 step 6): a fresh listing, so a record that became
`.resolved` meanwhile is neither tried nor reported (S0061 red team r1 L7).

The quit notes answered at the end of a recover-all (§9.3) are BL-0154's; the screens are BL-0092's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.platform.engine import (
    ActOutcome,
    EngineFailure,
    EngineRefusal,
    InspectedFile,
    JournalFile,
    JournalListing,
    ListedJournal,
    TryOutcome,
    UnlistedFolder,
)
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.scheduler import JobContext, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_recover_act, decode_recover_list, decode_recover_try

type Reason = Literal["stopped", "failed", "cancelled", "not_tried"]
"""Why a journal blocks (§11): its try stopped on a cause found on disk; the try failed (an error the system
answered, the engine died); the user cancelled it; it was not tried because no engine could run."""

ENGINE_STOPPED = "engine_stopped"
"""The cause of a `failed` journal whose try ended without an answer (the engine died, or broke the protocol)."""
UNREADABLE = "unreadable"


# -- the blocked screen's actions (§8.1 "Afterwards", §11) -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Keep:
    """ "Keep the files as they are now" for one journal."""

    journal_dir: str
    name: str


@dataclass(frozen=True, slots=True)
class DeleteKept:
    """ "Delete the kept copy" `kept` (its entry name) beside the journal's file `file`, whose bytes the user was
    shown as `sha256` (from the report's inspection)."""

    journal_dir: str
    name: str
    file: str
    kept: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DeleteCreated:
    """ "Delete it": the file at `file`, a path the journal records as created, shown to the user as `sha256`."""

    journal_dir: str
    name: str
    file: str
    sha256: str


type Action = Keep | DeleteKept | DeleteCreated


def action_params(action: Action) -> dict[str, object]:
    match action:
        case Keep():
            return {"journal_dir": action.journal_dir, "name": action.name, "action": "keep"}
        case DeleteKept():
            return {
                "journal_dir": action.journal_dir,
                "name": action.name,
                "action": "delete_kept",
                "file": action.file,
                "kept": action.kept,
                "sha256": action.sha256,
            }
        case DeleteCreated():
            return {
                "journal_dir": action.journal_dir,
                "name": action.name,
                "action": "delete_created",
                "file": action.file,
                "sha256": action.sha256,
            }


# -- the report ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Journal:
    """A journal as the listing found it: where, its name, and what its record says (None fields: unreadable)."""

    journal_dir: str
    name: str
    readable: bool
    workspace_key: str | None
    lineage: str | None
    files: tuple[JournalFile, ...]

    @property
    def path_keys(self) -> frozenset[str]:
        return frozenset(f.path_key for f in self.files)


@dataclass(frozen=True, slots=True)
class Blocking:
    """A journal still `.open` after this run (§11's `RECOVERY_BLOCKED {path, workspace, reason}`): `cause`, `path`,
    `entry`, `detail` and `errno` from its try (`Stopped`/`Failed`), `inspection` what stands there now (a stopped
    try's)."""

    journal: Journal
    reason: Reason
    cause: str | None = None
    path: str | None = None
    entry: str | None = None
    detail: str | None = None
    errno: int | None = None
    inspection: tuple[InspectedFile, ...] | None = None


@dataclass(frozen=True, slots=True)
class ActionResult:
    """What an action of this run did: the engine's answer, or `not_open` when the listing did not find its journal
    `.open`, or `cancelled`/`failed` when its call ended without an answer."""

    action: Action
    outcome: ActOutcome


@dataclass(frozen=True, slots=True)
class Report:
    """The run's outcome (the module's docstring). `listed` False: the run ended before its listing (no engine, a
    refusal); then nothing is known and every open is blocked."""

    listed: bool
    undone: tuple[Journal, ...] = ()
    kept: tuple[Journal, ...] = ()
    blocking: tuple[Blocking, ...] = ()
    unlisted: tuple[UnlistedFolder, ...] = ()
    actions: tuple[ActionResult, ...] = ()
    parts_removed: int = 0
    why_not_listed: str | None = None


@dataclass(frozen=True, slots=True)
class NotRecovered:
    """Every open is blocked: the run never listed (`folder` None), or a journal folder could not be listed."""

    folder: str | None
    errno: int | None
    why: str | None = None


def _journal(j: ListedJournal) -> Journal:
    return Journal(j.journal_dir, j.name, j.readable, j.workspace_key, j.lineage, j.files)


def blocking_for(
    report: Report | None,
    *,
    workspace_key: str | None,
    lineage: str | None,
    ledger_keys: Iterable[str],
) -> tuple[Blocking | NotRecovered, ...]:
    """What blocks an open of the workspace (`workspace_key`, `lineage`; None for a copy, which is matched by its
    files only) whose ledger set has the `path_key`s `ledger_keys` (§8.1 "Afterwards", ADR-0063 decision 2): empty
    when it may open. `report` None: recover-all ended without one (it never ran its work, or was cancelled before
    its listing): every open is blocked."""
    if report is None:
        return (NotRecovered(None, None, "no_report"),)
    if not report.listed:
        return (NotRecovered(None, None, report.why_not_listed),)
    keys = frozenset(ledger_keys)
    out: list[Blocking | NotRecovered] = [NotRecovered(u.folder, u.errno) for u in report.unlisted]
    for b in report.blocking:
        j = b.journal
        if (
            not j.readable
            or (workspace_key is not None and j.workspace_key == workspace_key)
            or (lineage is not None and j.lineage == lineage)
            or bool(j.path_keys & keys)
        ):
            out.append(b)
    return tuple(out)


# -- the work -------------------------------------------------------------------------------------------------------


def recover_all_spec() -> JobSpec:
    """The boot's, a switch's, a Reopen's, a Retry's or a Back's own `recover_all` job (§3, §8.1)."""
    return JobSpec(kind="recover_all", workspace=None, waited_for=True, confirmed=False, owner="switch")


def recover_all_work(app_local_data: Path, actions: Sequence[Action] = ()) -> Work:
    """The work of a `recover_all` job over the app's machine-local folder, with the blocked screen's `actions`
    first (empty: the boot's, a switch's or a Retry's)."""

    def work(ctx: JobContext) -> WorkResult:
        return WorkResult(_Run(ctx, str(app_local_data), tuple(actions)).run(), ran_user_code=False)

    return work


class _NoEngine(Exception):
    """No engine could be had for the next step: every journal not yet settled is `not_tried`."""

    def __init__(self, failure: EngineFailure) -> None:
        super().__init__(failure.kind)
        self.failure = failure


class _Run:
    def __init__(self, ctx: JobContext, app_local_data: str, actions: tuple[Action, ...]) -> None:
        self._ctx = ctx
        self._ald = app_local_data
        self._actions = actions

    def _call(self, op: str, params: Mapping[str, object]) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        return self._ctx.call(op, {"app_local_data": self._ald, **params})

    def _fresh(self) -> None:
        failure = self._ctx.fresh_engine()
        if failure is not None:
            raise _NoEngine(failure)

    def _list(self) -> JournalListing | Report:
        while True:
            out = self._call("recover_list", {})
            if isinstance(out, EngineFailure):
                if out.kind == "cancelled":
                    return Report(listed=False, why_not_listed="cancelled")
                try:
                    self._fresh()  # model `rc_spawn`, then `rc_init`: list again in a fresh engine
                except _NoEngine as e:
                    return Report(listed=False, why_not_listed=e.failure.kind)
                continue
            if isinstance(out, EngineRefusal):
                return Report(listed=False, why_not_listed=out.key)
            listing = decode_recover_list(out)
            if isinstance(listing, Violation):
                return Report(listed=False, why_not_listed="protocol_violation")
            return listing

    def run(self) -> Report:
        listing = self._list()
        if isinstance(listing, Report):
            return listing
        self._ctx.listed()
        journals = [_journal(j) for j in listing.journals]
        by_name = {(j.journal_dir, j.name): j for j in journals}
        settled: dict[tuple[str, str], Journal | Blocking] = {}
        undone: list[Journal] = []
        kept: list[Journal] = []
        results: list[ActionResult] = []
        try:
            for action in self._actions:
                where = (action.journal_dir, action.name)
                j = by_name.get(where)
                if j is None or where in settled:
                    results.append(ActionResult(action, ActOutcome("refused", "not_open", None, None, None)))
                    continue
                out = self._call("recover_act", action_params(action))
                if isinstance(out, EngineFailure | EngineRefusal) or isinstance(
                    act := decode_recover_act(out), Violation
                ):
                    blocking = self._lost(j, out)
                    results.append(ActionResult(action, ActOutcome("failed", blocking.cause, None, None, None)))
                    settled[where] = blocking
                    if isinstance(out, EngineFailure):
                        self._fresh()
                    continue
                results.append(ActionResult(action, act))
                if isinstance(action, Keep) and act.outcome == "done":
                    settled[where] = j
                    kept.append(j)
            for j in journals:
                where = (j.journal_dir, j.name)
                if where in settled:
                    continue
                if not j.readable:
                    settled[where] = Blocking(j, "stopped", UNREADABLE)
                    continue
                out = self._call("recover_try", {"journal_dir": j.journal_dir, "name": j.name})
                if isinstance(out, EngineFailure | EngineRefusal) or isinstance(
                    tried := decode_recover_try(out), Violation
                ):
                    settled[where] = self._lost(j, out)
                    if isinstance(out, EngineFailure):
                        self._fresh()
                    continue
                if tried.outcome == "resolved":
                    settled[where] = j
                    undone.append(j)
                else:
                    settled[where] = _blocking(j, tried)
        except _NoEngine as e:
            why = "lock_failed" if e.failure.kind == "start_failed" else e.failure.kind
            for j in journals:
                settled.setdefault((j.journal_dir, j.name), Blocking(j, "not_tried", why))
        blocking = tuple(b for b in settled.values() if isinstance(b, Blocking))
        assert len(settled) == len(journals), "a listed journal ended neither recovered, kept nor blocking"  # noqa: S101
        return Report(
            listed=True,
            undone=tuple(undone),
            kept=tuple(kept),
            blocking=blocking,
            unlisted=listing.unlisted,
            actions=tuple(results),
            parts_removed=listing.parts_removed,
        )

    def _lost(self, j: Journal, out: Mapping[str, object] | EngineRefusal | EngineFailure) -> Blocking:
        """A step that ended without the op's answer: our Cancel (`cancelled`), else `failed`."""
        if isinstance(out, EngineFailure) and out.kind == "cancelled":
            return Blocking(j, "cancelled")
        if isinstance(out, EngineRefusal):
            return Blocking(j, "failed", out.key)
        return Blocking(j, "failed", ENGINE_STOPPED)


def _blocking(j: Journal, t: TryOutcome) -> Blocking:
    reason: Reason = "stopped" if t.outcome == "stopped" else "failed"
    return Blocking(j, reason, t.cause, t.path, t.entry, t.detail, t.errno, t.inspection)
