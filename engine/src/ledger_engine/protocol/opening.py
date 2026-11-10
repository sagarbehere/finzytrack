"""The open's op on the protocol (S0088; `design/specs/engine-jobs.md` §2's `open`, §5.1, §7; BL-0152): plain data in
and out of `ledger_engine.ledger.opening`. The server answers it only in an engine that holds the engine lock (it
reads ledger and journal files, engine-jobs §5.1).

- `open` (`opening.run`): the overlay load of the ledger at `root` and its publish into the workspace's mirror folder
  in the app cache (`design/specs/mirror/publish.md` §4). It writes nothing else. It runs the ledger's plugins: the
  sidecar retires its engine after it. `app_local_data` must be the folder of this engine's lock (as for the save and
  the rebuild); the mirror folder is always `<app cache>/workspaces/<workspace_key>/mirror/` under the cache folder
  the engine was started with (`--app-cache`), so no request names another folder to clean.

Any `OSError` reaches the server as `engine.internal`: the open did not run to a result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import opening
from ledger_engine.protocol.messages import Message, OpenParams, OpenRefusal, OpenResult
from ledger_engine.protocol.rebuild import Publisher, fields_of, target_of


class OpenJob(Publisher, Protocol):
    def progress(self, phase: opening.Phase) -> None: ...


REFUSAL: Final[dict[opening.Refusal, OpenRefusal]] = {
    "encrypted": "encrypted",
    "number-failed": "number_failed",
    "beancount-failed": "beancount_failed",
}
TABLE: Final = "engine.open"
TABLE_VERSION: Final = 2  # 1 (S0088): the open's load and publish; 2 (S0098): `refusal` among the inputs


def run(params: Message, ctx: OpenJob) -> Message:
    assert isinstance(params, OpenParams)  # noqa: S101 (the handler's own params model)
    fs = for_this_os()
    target = target_of(params, ctx, fs)
    a = opening.run(opening.Ports(fs=fs), params.root, ctx, target)
    f = fields_of(a.outcome, a.published, a.cleaned)
    inputs = {
        "errors": a.errors,
        "publish": f.publish,
        "removed": f.removed,
        "remove_failed": f.remove_failed,
        "refusal": a.refusal,
    }
    ctx.decide(TABLE, TABLE_VERSION, inputs, a.outcome)
    return OpenResult(
        outcome=a.outcome,
        entries=a.entries,
        errors=a.errors,
        publish=f.publish,
        number=f.number,
        ledger_version=f.ledger_version,
        removed=f.removed,
        remove_failed=f.remove_failed,
        refused=a.refused,
        refusal=None if a.refusal is None else REFUSAL[a.refusal],
        line=a.line,
    )


OPEN_HANDLERS: Final[dict[str, Callable[[Message, OpenJob], Message]]] = {"open": run}
