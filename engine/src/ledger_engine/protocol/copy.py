"""The copy's ops on the protocol (S0081; `design/specs/engine-jobs.md` §1, §4, §8.1, §8.2): plain data in and out of
`ledger_engine.ledger.copy`, over the real file-system port and legacy locks. The server answers them only in an
engine that holds the engine lock (they read and write user files and read the journals, engine-jobs §5.1). Their
results are the create's (`protocol.create.answer`, `facts_result`).

- `copy` (`copy.run`): the sources read (the legacy locks, the source's load, the journals), the copy written and
  read back, "loads the same" and the re-check, `gate` (and waits for `proceed`), then `.finzytrack/` last. Its two
  loads run the ledger's plugins: the sidecar retires its engine after it.
- `copy_outcome` (`copy.outcome`): §1's "resolved from disk" after a copy's engine died: the target synced, then
  what stands at `.finzytrack` and every entry in the target. It loads and removes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from ledger_engine.fs.darwin import DarwinLegacyLocks, for_this_os
from ledger_engine.ledger import copy as cp
from ledger_engine.ledger import create as cr
from ledger_engine.ledger.writer import BadPlanError
from ledger_engine.protocol.create import CreateJob, answer, facts_result
from ledger_engine.protocol.messages import CopyParams, Message
from ledger_engine.protocol.recovery import BadParamsError, Decider

TABLE: Final = "engine.copy"
TABLE_VERSION: Final = 1  # 1 (S0081): the copy of engine-jobs §8.2


def _bytes(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate: JSON can carry it, a file cannot
        msg = "params.manifest.text: not valid Unicode text"
        raise BadParamsError(msg) from None


def _plan(params: Message) -> cp.CopyPlan:
    assert isinstance(params, CopyParams)  # noqa: S101 (the handler's own params model)
    return cp.CopyPlan(
        job=params.job,
        target=params.target,
        source=params.source,
        manifest=tuple(cr.NewFile(f.path, _bytes(f.text)) for f in params.manifest),
        app_local_data=params.app_local_data,
        legacy_locks=tuple(params.legacy_locks),
    )


def copy(params: Message, ctx: CreateJob) -> Message:
    plan = _plan(params)
    try:
        out = cp.run(plan, cp.CopyPorts(for_this_os(), DarwinLegacyLocks()), ctx)
    except BadPlanError as e:
        raise BadParamsError(str(e)) from None
    return answer(out, ctx, TABLE, TABLE_VERSION)


def copy_outcome(params: Message, ctx: Decider) -> Message:
    plan = _plan(params)
    try:
        facts = cp.outcome(for_this_os(), plan)
    except BadPlanError as e:
        raise BadParamsError(str(e)) from None
    return facts_result(facts, ctx, TABLE, TABLE_VERSION)


COPY_HANDLERS: Final[dict[str, Callable[[Message, CreateJob], Message]]] = {"copy": copy}
COPY_OUTCOME_HANDLERS: Final[dict[str, Callable[[Message, Decider], Message]]] = {"copy_outcome": copy_outcome}
