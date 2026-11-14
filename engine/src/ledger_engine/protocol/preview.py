"""The preview's op on the protocol (S0116; `design/specs/engine-jobs.md` §2's `preview`, §5.1, §7): plain data in and
out of `ledger_engine.ledger.preview`. The server answers it only in an engine that holds the engine lock: it reads
ledger files, the mirror and the journals (engine/CLAUDE.md: a new op that reads ledger or journal files sets
`OpSpec.needs_engine_lock`), so no save of ours runs beside it. It writes nothing, so it does not check the app
folder's identity (`writes_app_folder` stays false, as for the `open`).

- `preview` (`preview.run`): the published generation `generation` as the baseline, the ledger's files read, the
  mutator's producer, the contract's prepare, the re-check. It runs the ledger's plugins: the sidecar retires its
  engine after it. `app_local_data` must be the folder of this engine's lock (as for the save).

Any `OSError` (a ledger file that cannot be read) reaches the server as `engine.internal`: the preview did not run to
a result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from ledger_engine.fs.this_os import for_this_os
from ledger_engine.ledger import preview
from ledger_engine.ledger.core.ledger_set import encode
from ledger_engine.protocol.messages import Message, PreviewError, PreviewFile, PreviewParams, PreviewResult
from ledger_engine.protocol.rebuild import Publisher
from ledger_engine.protocol.recovery import BadParamsError, app_local_data_of
from ledger_engine.protocol.save import app_cache_of, engine_build

TABLE: Final = "engine.preview"
TABLE_VERSION: Final = 1  # 1 (S0116): the baseline, the producer, the contract's prepare, the re-check


class PreviewJob(Publisher, Protocol):
    def progress(self, phase: preview.Phase) -> None: ...


def _abs(path: str | None) -> str | None:
    """A path the answer may name (`AbsPath`): a refusal's subject can be a plugin's name, which is not one."""
    return path if path is not None and path.startswith("/") else None


def result_of(a: preview.Answer) -> PreviewResult:
    p, r = a.prepared, a.refused
    return PreviewResult(
        outcome=a.outcome,
        files=[] if p is None else [PreviewFile(path=f.path, pre=f.pre, post=f.post, text=f.text) for f in p.files],
        fingerprint=None if p is None else encode(p.fingerprint).decode("ascii"),
        new_errors=[]
        if p is None
        else [
            PreviewError(key=e.key, type=e.type, message=e.message, path=_abs(e.path), line=e.line)
            for e in p.new_errors
        ],
        errors=0 if p is None else p.errors,
        step=None if r is None else r.step,
        reason=None if r is None else r.reason,
        path=None if r is None else _abs(r.path),
        detail=None if r is None else r.detail,
        stale=a.stale,
    )


def run(params: Message, ctx: PreviewJob) -> Message:
    assert isinstance(params, PreviewParams)  # noqa: S101 (the handler's own params model)
    if params.workspace_key in (".", ".."):
        msg = "params.workspace_key: not a folder name"
        raise BadParamsError(msg)
    fs = for_this_os()
    where = preview.Where(
        app_cache=app_cache_of(ctx),
        workspace_key=params.workspace_key,
        app_local_data=app_local_data_of(fs, params.app_local_data, ctx),
        lineage=params.lineage,
        engine_build=engine_build(),
    )
    a = preview.run(preview.Ports(fs=fs), params.root, params.generation, params.mutator, params.request, where, ctx)
    out = result_of(a)
    inputs = {
        "files": len(out.files),
        "new_errors": len(out.new_errors),
        "errors": out.errors,
        "step": out.step,
        "reason": out.reason,
        "stale": out.stale,
    }
    ctx.decide(TABLE, TABLE_VERSION, inputs, a.outcome)
    return out


PREVIEW_HANDLERS: Final[dict[str, Callable[[Message, PreviewJob], Message]]] = {"preview": run}
