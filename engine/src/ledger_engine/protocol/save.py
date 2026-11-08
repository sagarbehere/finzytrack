"""The save's ops on the protocol (S0063; `design/specs/engine-jobs.md` §6.1, §6.6, §11): plain data in and out of
`ledger_engine.ledger.save`, over the real file-system port (no per-file lock and no legacy lock: ADR-0068 D3,
D4). The server answers them only in an engine that holds the engine lock, and `app_local_data` must
be that lock's folder (as for recover-all's ops, `protocol.recovery`), so no op writes outside the app's own folders
and the ledger files the plan names.

- `save`: one confirmed plan, run to its end (`save.run`): it sends `gate` (and waits for `proceed`), `saving` and
  `committed`. It does no Beancount load (ADR-0068 D1); the sidecar still retires its engine after it.
- `save_outcome`: §6.6's reading of the disk after a save's engine died (`save.outcome`): parse-only.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import version
from typing import Final, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import UndecodableError, decode
from ledger_engine.ledger.writer import BadPlanError, Change, NotSaved, Unfinished
from ledger_engine.protocol.messages import (
    Message,
    SaveOutcomeParams,
    SaveOutcomeResult,
    SaveParams,
    SaveResult,
)
from ledger_engine.protocol.recovery import BadParamsError, Decider, app_local_data_of

TABLE: Final = "engine.save"
TABLE_VERSION: Final = 4  # 2 (S0068): no `check_pending` or `check_failed` refusal (ADR-0067); 3 (S0071): no
# `legacy_app_saving` refusal (ADR-0068 D4); 4 (S0074): the history trim's counts among the inputs


class SaveJob(Decider, Protocol):
    """The request's job context as the save sees it (`server.JobContext`): also the app cache folder this engine was
    started with (`--app-cache`), where the gate reads the published mirror (S0087)."""

    @property
    def app_cache(self) -> str | None: ...

    def progress(self, phase: sv.Phase) -> None: ...

    def gate(self) -> None: ...

    def committed(self) -> None: ...


def engine_build() -> str:
    """The engine build a closed record names (§6.5: which build wrote the save)."""
    return f"finzytrack-engine {version('finzytrack-engine')}; beancount {version('beancount')}"


class HasAppCache(Protocol):
    @property
    def app_cache(self) -> str | None: ...


def app_cache_of(ctx: HasAppCache) -> str:
    """The app cache folder this engine was started with (S0087: anchored at the start, never named by a request,
    so no request can make a job read or clean another folder); an engine started without one refuses."""
    if ctx.app_cache is None:
        msg = "this engine was started without the app's cache folder (--app-cache)"
        raise BadParamsError(msg)
    return ctx.app_cache


def _key(workspace_key: str) -> str:
    if workspace_key in (".", ".."):
        msg = "params.workspace_key: not a folder name"
        raise BadParamsError(msg)
    return workspace_key


def _post(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate: JSON can carry it, a file cannot
        msg = "params.changes.post: not valid Unicode text"
        raise BadParamsError(msg) from None


def _result(out: sv.SaveOutcome) -> SaveResult:
    match out:
        case sv.Saved(published=published):
            outcome, reason, path, unchanged, open_journal = "saved", None, None, False, False
            entry = detail = cause = cause_path = None
        case sv.Refused(reason=reason, unchanged=unchanged, path=path):
            outcome, open_journal, published = "refused", False, False
            entry = detail = cause = cause_path = None
        case NotSaved():
            outcome, reason, path, unchanged, open_journal = (
                "not_saved",
                out.reason.value,
                out.path,
                out.unchanged,
                out.open_journal,
            )
            entry, detail, cause, cause_path, published = out.entry, out.detail, None, None, False
        case Unfinished():
            outcome, reason, path, unchanged, open_journal = "unfinished", out.reason.value, out.path, False, True
            entry, detail, cause, cause_path, published = None, None, out.cause, out.cause_path, False
    return SaveResult(
        outcome=outcome,
        reason=reason,
        path=path,
        entry=entry,
        detail=detail,
        unchanged=unchanged,
        open_journal=open_journal,
        cause=cause,
        cause_path=cause_path,
        published=published,
    )


def save(params: Message, ctx: SaveJob) -> Message:
    assert isinstance(params, SaveParams)  # noqa: S101 (the handler's own params model)
    fs = for_this_os()
    ald = app_local_data_of(fs, params.app_local_data, ctx)
    try:
        fingerprint = decode(params.fingerprint.encode("ascii"))
    except (UnicodeEncodeError, UndecodableError):
        msg = "params.fingerprint: not a fingerprint's encoding"
        raise BadParamsError(msg) from None
    plan = sv.SavePlan(
        session=params.session,
        job=params.job,
        workspace_key=_key(params.workspace_key),
        lineage=params.lineage,
        engine_build=engine_build(),
        root=params.root,
        changes=tuple(Change(c.path, c.pre, _post(c.post)) for c in params.changes),
        fingerprint=fingerprint,
        app_cache=app_cache_of(ctx),
        app_local_data=ald,
    )
    ports = sv.Ports(fs)
    try:
        out = sv.run(plan, ports, ctx)
    except BadPlanError as e:
        raise BadParamsError(str(e)) from None
    result = _result(out)
    trimmed = out.trimmed if isinstance(out, sv.Saved) else None
    ctx.decide(
        TABLE,
        TABLE_VERSION,
        {
            "reason": result.reason,
            "unchanged": result.unchanged,
            "trimmed": None if trimmed is None else trimmed.removed,
            "trim_left": None if trimmed is None else trimmed.left,
            "trim_skipped": None if trimmed is None else trimmed.skipped,
        },
        result.outcome,
    )
    return result


def save_outcome(params: Message, ctx: Decider) -> Message:
    assert isinstance(params, SaveOutcomeParams)  # noqa: S101 (the handler's own params model)
    fs = for_this_os()
    ald = app_local_data_of(fs, params.app_local_data, ctx)
    changes = tuple(Change(t.path, t.pre, b"") for t in params.targets)
    facts = sv.outcome(fs, ald, _key(params.workspace_key), params.session, params.job, changes)
    ctx.decide(TABLE, TABLE_VERSION, {"record": facts.record, "unchanged": facts.unchanged}, "facts")
    return SaveOutcomeResult(record=facts.record, unchanged=facts.unchanged, path=facts.path, errno=facts.errno)


SAVE_HANDLERS: Final[dict[str, Callable[[Message, SaveJob], Message]]] = {"save": save}
OUTCOME_HANDLERS: Final[dict[str, Callable[[Message, Decider], Message]]] = {"save_outcome": save_outcome}
