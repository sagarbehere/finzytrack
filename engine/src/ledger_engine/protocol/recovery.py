"""Recover-all's ops on the protocol (S0062; `design/specs/engine-jobs.md` §8.1, §11; ADR-0063 decision 2): plain
data in and out of `ledger_engine.ledger.recover_all` and `ledger_engine.ledger.restore.recover_journal`, over the
real file-system port and the ledger-set locks in `<app_local_data>/locks/`. The server answers them only in an
engine that holds the engine lock (`Engine.took_engine_lock`).

- `recover_list`: the listing (`recover_all.list_journals`).
- `recover_try`: one journal's try (`restore.recover_journal`); for a `stopped` one, `recover_all.inspect` of its
  record, so the blocked screen can show both versions or the file at a created path without another request.
- `recover_act`: `keep`, `delete_kept` or `delete_created`.

`app_local_data` must be the folder the engine's engine lock lives under (`<app_local_data>/locks/engine.lock`),
both resolved (red team r1 R1-2), and a `journal_dir` must be `<app_local_data>/local/<key>/journal` as the listing
named it (its canonical path): anything else is refused `protocol.invalid_params` (`BadParamsError`), so no op
writes outside the app's own journals. A try (and its inspection) opens the journal folder through the path guard:
one reached through a link stops `path_moved`, touching nothing (red team r1 R1-1); the actions do the same
(`recover_all`'s `Handles`). Between that check and the try's own open of the folder, a link swapped in stays
possible (only a process writing inside app-local data could; spec §15).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Final, Protocol

from ledger_engine.fs.darwin import DarwinLocks, for_this_os
from ledger_engine.fs.port import FileSystem, Kind, same_path
from ledger_engine.ledger import recover_all as ra
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.handles import Handles, PathMovedError, join
from ledger_engine.ledger.restore import Failed, Resolved, Stopped, recover_journal
from ledger_engine.protocol.messages import (
    DecisionScalar,
    EntryKind,
    EntrySeen,
    InspectedFile,
    JournalFile,
    KeptCopySeen,
    ListedJournal,
    Message,
    RecoverActParams,
    RecoverActResult,
    RecoverListParams,
    RecoverListResult,
    RecoverTryParams,
    RecoverTryResult,
    UnlistedFolder,
)

TABLE: Final = "engine.recovery"
TABLE_VERSION: Final = 1


class BadParamsError(ValueError):
    """Params the op cannot take (a journal folder outside the app's journals)."""


class Decider(Protocol):
    """What a handler needs of the request's job context (`server.JobContext`): the engine lock this engine holds
    (None: none; the server refuses these ops before they run then) and the decision record."""

    @property
    def engine_lock(self) -> str | None: ...

    def decide(self, table: str, version: int, inputs: dict[str, DecisionScalar], output: str) -> None: ...


def _fs() -> FileSystem:
    return for_this_os()


def ledger_locks_in(app_local_data: str) -> DarwinLocks:
    """The ledger-set locks in `<app_local_data>/locks/` (the engine lock's folder: made if missing, as the engine lock
    makes it)."""
    folder = join(app_local_data.rstrip("/") or "/", "locks")
    os.makedirs(folder, exist_ok=True)
    return DarwinLocks(folder)


def _canonical(fs: FileSystem, path: str) -> str | None:
    """`path`'s canonical path when every component of it exists (None otherwise)."""
    try:
        r = fs.resolve(path)
    except ValueError:  # a relative path, or a `.` or `..` component
        return None
    return None if r.rest else core_paths.canonical_path(r)


def app_local_data_of(fs: FileSystem, app_local_data: str, ctx: Decider) -> str:
    """`app_local_data` only when it is the folder this engine's engine lock lives under
    (`<app_local_data>/locks/engine.lock`, as the main sidecar names it), both resolved: any other tree, laid out as
    the app's or not, is refused before anything in it is read, made or removed (red team r1 R1-2)."""
    lock = ctx.engine_lock
    mine = None if lock is None else _canonical(fs, os.path.dirname(os.path.dirname(os.path.abspath(lock))))
    given = _canonical(fs, app_local_data)
    if mine is None or given is None or not same_path(given, mine):
        msg = "params.app_local_data: not the folder of this engine's engine lock"
        raise BadParamsError(msg)
    return app_local_data


def _journal_dir(fs: FileSystem, app_local_data: str, journal_dir: str) -> str:
    """`journal_dir` as the listing names it: `<canonical local>/<key>/journal`, one component for the key."""
    r = fs.resolve(join(app_local_data.rstrip("/") or "/", ra.LOCAL))
    local = core_paths.canonical_path(r)
    head, _, last = journal_dir.rpartition("/")
    key_parent, _, key = head.rpartition("/")
    if last != ra.JOURNAL or key_parent != local or not key or key in (".", ".."):
        msg = "params.journal_dir: not a journal folder of this app"
        raise BadParamsError(msg)
    return journal_dir


def _kind(kind: Kind) -> EntryKind:
    return kind.value


def _seen(seen: ra.Seen) -> EntrySeen:
    return EntrySeen(kind=_kind(seen.kind), sha256=seen.sha256, size=seen.size)


def _listed(j: ra.Listed) -> ListedJournal:
    r = j.record
    files = (
        []
        if r is None
        else [JournalFile(path=f.canonical_path, path_key=f.path_key, created=f.pre is None) for f in r.files]
    )
    return ListedJournal(
        journal_dir=j.journal_dir,
        name=j.name,
        readable=r is not None,
        workspace_key=None if r is None else r.workspace_key,
        lineage=None if r is None else r.lineage,
        files=files,
        errno=j.errno,
    )


def recover_list(params: Message, ctx: Decider) -> Message:
    assert isinstance(params, RecoverListParams)  # noqa: S101 (the handler's own params model)
    fs = _fs()
    listing = ra.list_journals(fs, app_local_data_of(fs, params.app_local_data, ctx))
    return RecoverListResult(
        journals=[_listed(j) for j in listing.journals],
        unlisted=[UnlistedFolder(folder=u.folder, errno=u.errno) for u in listing.unlisted],
        parts_removed=listing.parts_removed,
    )


def _guarded(fs: FileSystem, journal_dir: str) -> bool:
    """The journal folder is at its path: no link on the way leads elsewhere (§6.3's path guard; red team r1
    R1-1: a key or journal folder in `local/` that is a link to another key's journal)."""
    handles = Handles(fs)
    try:
        handles.at(journal_dir)
    except PathMovedError:
        return False
    except OSError:
        return True  # not there, or unreadable: the try itself answers that (`unreachable`, an error)
    finally:
        handles.close()
    return True


def _record(fs: FileSystem, journal_dir: str, name: str) -> Record | None:
    handles = Handles(fs)
    try:
        data = handles.at(journal_dir).read(journal.file_name(name, State.OPEN))
    except (OSError, PathMovedError):
        return None
    finally:
        handles.close()
    r = journal.decode(data or b"")
    return r if isinstance(r, Record) and r.name == name else None


def _inspection(fs: FileSystem, journal_dir: str, name: str) -> list[InspectedFile] | None:
    record = _record(fs, journal_dir, name)
    if record is None:
        return None
    return [
        InspectedFile(
            path=i.path,
            created=i.created,
            reachable=i.reachable,
            now=None if i.now is None else _seen(i.now),
            kept=[KeptCopySeen(name=k.name, path=k.path, seen=_seen(k.seen)) for k in i.kept],
            errno=i.errno,
        )
        for i in ra.inspect(fs, record)
    ]


def recover_try(params: Message, ctx: Decider) -> Message:
    assert isinstance(params, RecoverTryParams)  # noqa: S101 (the handler's own params model)
    fs = _fs()
    ald = app_local_data_of(fs, params.app_local_data, ctx)
    jd = _journal_dir(fs, ald, params.journal_dir)
    out = (
        recover_journal(fs, ledger_locks_in(ald), jd, params.name)
        if _guarded(fs, jd)
        else Stopped(Cause.PATH_MOVED, None, jd)
    )
    if isinstance(out, Resolved):
        result = RecoverTryResult(
            outcome="resolved", cause=None, path=None, entry=None, detail=None, errno=None, inspection=None
        )
    elif isinstance(out, Stopped):
        result = RecoverTryResult(
            outcome="stopped",
            cause=out.cause.value,
            path=out.path,
            entry=out.entry,
            detail=out.detail,
            errno=None,
            inspection=_inspection(fs, jd, params.name),
        )
    else:
        assert isinstance(out, Failed)  # noqa: S101 (the outcome union)
        result = RecoverTryResult(
            outcome="failed",
            cause=out.cause.value,
            path=out.path,
            entry=out.entry,
            detail=None,
            errno=out.errno,
            inspection=None,
        )
    ctx.decide(TABLE, TABLE_VERSION, {"cause": result.cause}, result.outcome)
    return result


def recover_act(params: Message, ctx: Decider) -> Message:
    assert isinstance(params, RecoverActParams)  # noqa: S101 (the handler's own params model)
    fs = _fs()
    ald = app_local_data_of(fs, params.app_local_data, ctx)
    jd = _journal_dir(fs, ald, params.journal_dir)
    locks = ledger_locks_in(ald)
    if params.action == "keep":
        out = ra.keep(fs, locks, jd, params.name)
    elif params.file is None or params.sha256 is None or (params.action == "delete_kept" and params.kept is None):
        msg = f"params: {params.action} needs file, sha256" + (" and kept" if params.action == "delete_kept" else "")
        raise BadParamsError(msg)
    elif params.action == "delete_kept":
        assert params.kept is not None  # noqa: S101 (checked above)
        out = ra.delete_kept(fs, locks, jd, params.name, params.file, params.kept, params.sha256)
    else:
        out = ra.delete_created(fs, locks, jd, params.name, params.file, params.sha256)
    if isinstance(out, ra.Done):
        return RecoverActResult(outcome="done", reason=None, path=None, detail=None, errno=None)
    if isinstance(out, ra.Refused):
        return RecoverActResult(outcome="refused", reason=out.reason, path=out.path, detail=out.detail, errno=None)
    return RecoverActResult(outcome="failed", reason=None, path=out.path, detail=None, errno=out.errno)


RECOVERY_HANDLERS: Final[dict[str, Callable[[Message, Decider], Message]]] = {
    "recover_list": recover_list,
    "recover_try": recover_try,
    "recover_act": recover_act,
}
