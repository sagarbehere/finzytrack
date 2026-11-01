"""The rebuild's op on the protocol (S0070; `design/specs/engine-jobs.md` §6.7, §7; ADR-0068 D1): plain data in and out
of `ledger_engine.ledger.rebuild`. The server answers it only in an engine that holds the engine lock (it reads ledger
files, engine-jobs §5.1).

- `rebuild` (`rebuild.run`): Beancount's own load of the ledger, the set named by `fingerprint` observed before and
  after it; it writes nothing. It runs the ledger's plugins: the sidecar retires its engine after it.

An encrypted file met (`EncryptedFileMet`, never decrypted) and any `OSError` reach the server as `engine.internal`:
the rebuild did not run to a result, which the sidecar logs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from ledger_engine.ledger import rebuild
from ledger_engine.ledger.core.ledger_set import UndecodableError, decode, encode
from ledger_engine.protocol.messages import Message, RebuildParams, RebuildResult
from ledger_engine.protocol.recovery import BadParamsError, Decider

TABLE: Final = "engine.rebuild"
TABLE_VERSION: Final = 1  # 1 (S0070): the rebuild after a save (ADR-0068 D1)


class RebuildJob(Decider, Protocol):
    def progress(self, phase: rebuild.Phase) -> None: ...


def run(params: Message, ctx: RebuildJob) -> Message:
    assert isinstance(params, RebuildParams)  # noqa: S101 (the handler's own params model)
    try:
        hint = decode(params.fingerprint.encode("ascii"))
    except (UnicodeEncodeError, UndecodableError):
        msg = "params.fingerprint: not a fingerprint's encoding"
        raise BadParamsError(msg) from None
    if hint.root != params.root:
        msg = "params.root: not the fingerprint's root"
        raise BadParamsError(msg)
    a = rebuild.run(rebuild.Ports(), hint, ctx)
    ctx.decide(TABLE, TABLE_VERSION, {"errors": a.errors}, a.outcome)
    fp = encode(a.fingerprint).decode("ascii")
    return RebuildResult(outcome=a.outcome, fingerprint=fp, entries=a.entries, errors=a.errors)


REBUILD_HANDLERS: Final[dict[str, Callable[[Message, RebuildJob], Message]]] = {"rebuild": run}
