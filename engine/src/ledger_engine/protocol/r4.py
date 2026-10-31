"""R4's op on the protocol (S0066, S0068; `design/specs/engine-jobs.md` §6.7; ADR-0067 decision 3): plain data in
and out of `ledger_engine.ledger.r4`, over the real file-system port. The server answers it only in an engine that
holds the engine lock, and `app_local_data` must be that lock's folder (as for recover-all's and the save's ops), so
it reads only the app's own journals; `key` is one folder name and `name` a record's name.

- `r4_check` (`r4.check`): reads the closed record, loads the disk with Beancount's own loader and compares; it
  writes nothing. It runs the ledger's plugins when it loads (`loaded`): the sidecar retires its engine after it.

An `R4Error` (an encrypted file met) and any `OSError` reach the server as `engine.internal`: R4 did not run, which
the sidecar logs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import r4
from ledger_engine.protocol.messages import Message, R4CheckResult, R4Params
from ledger_engine.protocol.recovery import BadParamsError, Decider, app_local_data_of
from ledger_engine.protocol.save import engine_build

TABLE: Final = "engine.r4"
TABLE_VERSION: Final = 2  # 2 (S0068): `r4_check` only; it renames nothing (ADR-0067)


class R4Job(Decider, Protocol):
    def progress(self, phase: r4.Phase) -> None: ...


def check(params: Message, ctx: R4Job) -> Message:
    assert isinstance(params, R4Params)  # noqa: S101 (the handler's own params model)
    if params.key in (".", ".."):
        msg = "params.key: not a folder name"
        raise BadParamsError(msg)
    ald = app_local_data_of(for_this_os(), params.app_local_data, ctx)
    a = r4.check(r4.desktop(for_this_os(), engine_build()), ald, params.key, params.name, ctx)
    ctx.decide(TABLE, TABLE_VERSION, {"op": "check", "loaded": a.loaded}, a.outcome)
    return R4CheckResult(outcome=a.outcome, loaded=a.loaded)


R4_HANDLERS: Final[dict[str, Callable[[Message, R4Job], Message]]] = {"r4_check": check}
