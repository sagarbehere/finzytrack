"""The create's ops on the protocol (S0080; `design/specs/engine-jobs.md` §1, §4, §8.2): plain data in and out of
`ledger_engine.ledger.create`, over the real file-system port. The server answers them only in an engine that holds
the engine lock (they write and read user files, engine-jobs §5.1).

- `create` (`create.run`): the new folder's files, the load check, `gate` (and waits for `proceed`), then
  `.finzytrack/` last. Its load runs the ledger's plugins: the sidecar retires its engine after it.
- `create_outcome` (`create.outcome`): §1's "resolved from disk" after a create's engine died: the target synced,
  then what stands at `.finzytrack` and every entry at the job's names, whatever it holds. It loads and removes
  nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import create as cr
from ledger_engine.ledger.writer import BadPlanError
from ledger_engine.protocol.messages import CreateOutcomeResult, CreateParams, CreateResult, Message
from ledger_engine.protocol.recovery import BadParamsError, Decider

TABLE: Final = "engine.create"
TABLE_VERSION: Final = 1  # 1 (S0080): the create of engine-jobs §8.2


class CreateJob(Decider, Protocol):
    """The request's job context as the create sees it (`server.JobContext`)."""

    def progress(self, phase: cr.Phase) -> None: ...

    def gate(self) -> None: ...


def _bytes(text: str, what: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate: JSON can carry it, a file cannot
        msg = f"params.{what}: not valid Unicode text"
        raise BadParamsError(msg) from None


def _plan(params: Message) -> cr.CreatePlan:
    assert isinstance(params, CreateParams)  # noqa: S101 (the handler's own params model)
    return cr.CreatePlan(
        job=params.job,
        target=params.target,
        files=tuple(cr.NewFile(f.path, _bytes(f.text, "files.text")) for f in params.files),
        root=params.root,
        manifest=tuple(cr.NewFile(f.path, _bytes(f.text, "manifest.text")) for f in params.manifest),
    )


def create(params: Message, ctx: CreateJob) -> Message:
    plan = _plan(params)
    try:
        out = cr.run(plan, cr.Ports(for_this_os()), ctx)
    except BadPlanError as e:
        raise BadParamsError(str(e)) from None
    return answer(out, ctx, TABLE, TABLE_VERSION)


def answer(out: cr.CreateOutcome, ctx: Decider, table: str, version: int) -> CreateResult:
    """A create's or a copy's outcome as its result, with its decision line."""
    match out:
        case cr.Created(folder=folder):
            result = CreateResult(
                outcome="created",
                reason=None,
                folder=folder,
                path=None,
                detail=None,
                errno=None,
                left=[],
            )
        case cr.NotCreated():
            result = CreateResult(
                outcome="not_created",
                reason=out.reason,
                folder=out.folder,
                path=out.path,
                detail=out.detail,
                errno=out.errno,
                left=None if out.left is None else list(out.left),
            )
    ctx.decide(
        table,
        version,
        {"reason": result.reason, "left": None if result.left is None else len(result.left)},
        result.outcome,
    )
    return result


def create_outcome(params: Message, ctx: Decider) -> Message:
    plan = _plan(params)
    try:
        facts = cr.outcome(for_this_os(), plan)
    except BadPlanError as e:
        raise BadParamsError(str(e)) from None
    return facts_result(facts, ctx, TABLE, TABLE_VERSION)


def facts_result(facts: cr.Facts, ctx: Decider, table: str, version: int) -> CreateOutcomeResult:
    """A create's or a copy's outcome read as its result, with its decision line."""
    ctx.decide(
        table,
        version,
        {"manifest": facts.manifest, "left": None if facts.left is None else len(facts.left)},
        "facts",
    )
    left = None if facts.left is None else list(facts.left)
    return CreateOutcomeResult(manifest=facts.manifest, left=left, errno=facts.errno)


CREATE_HANDLERS: Final[dict[str, Callable[[Message, CreateJob], Message]]] = {"create": create}
CREATE_OUTCOME_HANDLERS: Final[dict[str, Callable[[Message, Decider], Message]]] = {"create_outcome": create_outcome}
