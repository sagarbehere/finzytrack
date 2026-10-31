"""R4's ops on the protocol (S0066; `design/specs/engine-jobs.md` §6.5, §6.7, §11): plain data in and out of
`ledger_engine.ledger.r4`, over the real file-system port. The server answers them only in an engine that holds the
engine lock, and `app_local_data` must be that lock's folder (as for recover-all's and the save's ops), so no op
renames outside the app's own journals; `key` is one folder name and `name` a record's name.

- `r4_check`: steps 1-3 (`r4.check`); it runs the ledger's plugins when it loads (`loaded`): the sidecar retires its
  engine after it.
- `r4_confirm`: step 4 (`r4.confirm`), parse-only, in a fresh engine.
- `r4_attempt`: one failure to run counted (`r4.attempt`), parse-only.
- `r4_resume`: Resume saving (`r4.resume`), parse-only.

An `R4Error` (the replica refused a ledger it accepted at the save) and any `OSError` reach the server as
`engine.internal`: a failure to run, which the sidecar counts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import r4
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.protocol.messages import (
    Message,
    R4AttemptResult,
    R4CheckResult,
    R4ConfirmResult,
    R4Params,
    R4Result,
    R4ResumeResult,
)
from ledger_engine.protocol.recovery import BadParamsError, Decider, app_local_data_of
from ledger_engine.protocol.save import engine_build

TABLE: Final = "engine.r4"
TABLE_VERSION: Final = 1


class R4Job(Decider, Protocol):
    def progress(self, phase: r4.Phase) -> None: ...


def _params(params: Message, ctx: Decider) -> tuple[str, str, str]:
    assert isinstance(params, R4Params)  # noqa: S101 (the handler's own params model)
    if params.key in (".", ".."):
        msg = "params.key: not a folder name"
        raise BadParamsError(msg)
    return app_local_data_of(for_this_os(), params.app_local_data, ctx), params.key, params.name


RESULTS: Final[dict[str, type[R4Result]]] = {
    "check": R4CheckResult,
    "confirm": R4ConfirmResult,
    "attempt": R4AttemptResult,
    "resume": R4ResumeResult,
}


def _result(ctx: Decider, op: str, a: r4.Answer) -> R4Result:
    """The op's own result model: an outcome outside its set fails validation here (an engine bug), never on the
    wire."""
    ctx.decide(TABLE, TABLE_VERSION, {"op": op, "loaded": a.loaded}, a.outcome)
    return RESULTS[op].model_validate(
        {"outcome": a.outcome, "loaded": a.loaded, "record": a.record, "backup": a.backup}
    )


def check(params: Message, ctx: R4Job) -> Message:
    ald, key, name = _params(params, ctx)
    fs = for_this_os()
    return _result(ctx, "check", r4.check(r4.desktop(fs, engine_build(), DesktopPolicy()), ald, key, name, ctx))


def confirm(params: Message, ctx: R4Job) -> Message:
    ald, key, name = _params(params, ctx)
    fs = for_this_os()
    return _result(ctx, "confirm", r4.confirm(r4.desktop(fs, engine_build(), DesktopPolicy()), ald, key, name, ctx))


def attempt(params: Message, ctx: R4Job) -> Message:
    ald, key, name = _params(params, ctx)
    return _result(ctx, "attempt", r4.attempt(for_this_os(), ald, key, name))


def resume(params: Message, ctx: R4Job) -> Message:
    ald, key, name = _params(params, ctx)
    return _result(ctx, "resume", r4.resume(for_this_os(), ald, key, name))


R4_HANDLERS: Final[dict[str, Callable[[Message, R4Job], Message]]] = {
    "r4_check": check,
    "r4_confirm": confirm,
    "r4_attempt": attempt,
    "r4_resume": resume,
}
