"""The rebuild's op on the protocol (S0070; `design/specs/engine-jobs.md` §6.7, §7; ADR-0068 D1): plain data in and out
of `ledger_engine.ledger.rebuild`. The server answers it only in an engine that holds the engine lock (it reads ledger
files, engine-jobs §5.1).

- `rebuild` (`rebuild.run`): Beancount's own load of the ledger, the set named by `fingerprint` observed before and
  after it, and its publish into the workspace's mirror folder in the app cache (S0087, BL-0113;
  `design/specs/mirror/publish.md` §4). It writes nothing else. It runs the ledger's plugins: the sidecar retires its
  engine after it. `app_local_data` must be the folder of this engine's lock (as for the save); the mirror folder is
  always `<app cache>/workspaces/<workspace_key>/mirror/` under the cache folder the engine was started with
  (`--app-cache`, S0087 round 1), so no request names another folder to clean.

An encrypted file met (`EncryptedFileMet`, never decrypted) and any `OSError` reach the server as `engine.internal`:
the rebuild did not run to a result, which the sidecar logs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Final, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import rebuild
from ledger_engine.ledger.core.ledger_set import UndecodableError, decode, encode
from ledger_engine.mirror import publish as pb
from ledger_engine.protocol.messages import Message, Publish, RebuildParams, RebuildResult
from ledger_engine.protocol.recovery import BadParamsError, Decider, app_local_data_of
from ledger_engine.protocol.save import app_cache_of, engine_build

TABLE: Final = "engine.rebuild"
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
TABLE_VERSION: Final = 2  # 1 (S0070): the rebuild after a save (ADR-0068 D1); 2 (S0087): its publish


class RebuildJob(Decider, Protocol):
    def progress(self, phase: rebuild.Phase) -> None: ...

    @property
    def app_cache(self) -> str | None: ...


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
    if params.workspace_key in (".", ".."):
        msg = "params.workspace_key: not a folder name"
        raise BadParamsError(msg)
    try:
        today = date.fromisoformat(params.today)
    except ValueError:
        msg = "params.today: not a date"
        raise BadParamsError(msg) from None
    try:
        at = datetime.fromisoformat(params.built_at)
    except ValueError:
        at = None
    if at is None or at.utcoffset() is None:
        msg = "params.built_at: not an instant with a UTC offset"
        raise BadParamsError(msg)
    fs = for_this_os()
    target = rebuild.Target(
        app_cache=app_cache_of(ctx),
        workspace_key=params.workspace_key,
        app_local_data=app_local_data_of(fs, params.app_local_data, ctx),
        lineage=params.lineage,
        floor=params.floor,
        today=today,
        built_at=params.built_at,
        now_ns=(at - EPOCH) // timedelta(microseconds=1) * 1000,
        engine_build=engine_build(),
    )
    a = rebuild.run(rebuild.Ports(fs=fs), hint, ctx, target)
    p = a.published
    done = p if isinstance(p, pb.Published) else None
    publish: Publish | None = None
    if a.outcome == "fresh":
        publish = "fresh"
    elif isinstance(p, pb.Published):
        publish = "published"
    elif isinstance(p, pb.NotPublished) and p.why != "moved" and p.why != "paused":  # those two are outcomes
        publish = p.why
    removed = 0 if a.cleaned is None else a.cleaned.removed
    failed = 0 if a.cleaned is None else a.cleaned.failed
    inputs = {"errors": a.errors, "publish": publish, "removed": removed, "remove_failed": failed}
    ctx.decide(TABLE, TABLE_VERSION, inputs, a.outcome)
    return RebuildResult(
        outcome=a.outcome,
        fingerprint=encode(a.fingerprint).decode("ascii"),
        entries=a.entries,
        errors=a.errors,
        publish=publish,
        number=None if done is None else done.gen.number,
        ledger_version=None if done is None else done.ledger_version,
        removed=removed,
        remove_failed=failed,
    )


REBUILD_HANDLERS: Final[dict[str, Callable[[Message, RebuildJob], Message]]] = {"rebuild": run}
