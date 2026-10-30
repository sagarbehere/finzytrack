"""Recover-all's work on disk, inside an engine that holds the engine lock (`design/specs/engine-jobs.md` §8.1 and
its "Afterwards", §11's blocked-screen rows; ADR-0063 decision 2 and "Keep the files as they are now"; ADR-0064 and
its engine-lock amendment; S0062). The sidecar's `recover_all` job orders these through the protocol (ops
`recover_list`, `recover_try`, `recover_act`); it never reads a journal or a ledger file itself, so an engine of an
earlier session caught between its handshake and its `flock` can never run beside them (BL-0151's gap).

- **`list_journals`**: every `<app_local_data>/local/<key>/journal/` folder is `durable_sync`ed before it is read
  (what the listing finds, and what the quit notes are answered from, survives a power loss; model finding F1); every
  `.part` record in it is removed (no replace happens before a record is `.open`, §6.1 step 8.2: it names nothing to
  undo and never blocks; a removal that fails leaves it, harmless); every `.open` record is read and decoded strictly
  (`Listed.record` None when it cannot be: torn, foreign, newer, of another name, or a read error; such a journal
  names nothing it can be matched on and blocks every open, ADR-0063). A journal folder that cannot be synced,
  opened or listed is `Unlisted`: it may hold an `.open` record nobody can see, so it blocks every open too (fails
  closed). Only real folders are walked (a link or a file in `local/` is not ours and is skipped).
- **`inspect`**: what stands at each file a record names now, and every kept copy of this journal beside it (by
  `journal.is_kept_name`), each by its kind, SHA-256 and size; it writes nothing. The blocked screen shows these
  ("Show both", "Show it", Keep's list of the files it releases) and a delete names the SHA-256 it showed.
- **`keep`** ("Keep the files as they are now", §8.1 "Afterwards"; model `UserKeep`, finding N1): the ledger-set
  locks of every file the record names, without waiting; every file the record names that is there as a regular file
  `durable_sync`ed, and every folder it names that is there synced, so "now" is what a power loss keeps (a restore cut
  by an error may have left an exchange unsynced); then `.open` renamed `.kept` and the journal folder synced. A
  folder that is not there (an unplugged disk, a moved folder: `ENOENT`, `ENOTDIR`) has nothing to sync, and one no
  longer at its recorded path (a link in its place, a rename in case only) refuses Keep (`path_moved`: red team r1
  R1-3); a sync that answers `EROFS` has nothing pending (a read-only volume: probed on APFS, S0062, `F_FULLFSYNC`
  answers `EROFS` there); any other error renames nothing (`ActFailed`). A record that cannot be read is kept by its
  rename alone (ADR-0063: Keep resolves "one that cannot be read").
- **`delete_kept`** (owner, 2026-10-29: "Offer to delete it (Recommended)") and **`delete_created`** (owner,
  2026-10-30: "Never remove it (Recommended)": the user deletes it, here at their request): each removes one regular
  file and nothing else, only from an `.open` record that names it: a kept name this journal derives for one of its
  files (`journal.derives_kept_name`, exact up to NFD), or the path of a file the record says the save created (a
  `FileEntry` with no pre hash), through the path guard of its folder, under that file's ledger-set lock, and only
  when its bytes equal the SHA-256 the user was shown (`seen`): a program that wrote there after the look keeps its
  bytes (`changed`). Never a folder, a link, a FIFO, a name of ours or a file at a replaced path. The instant between
  the check of the bytes and the removal stays POSIX's (spec §15).

Every port operation may raise `OSError`; the engine may die at any effect. What each leaves: the listing writes only
`.part` removals; `inspect` nothing; `keep` the journal's rename, after syncs that write nothing (a crash leaves the
record `.open` or `.kept`, whole); a delete one removal and its folder's sync.
"""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem, Kind, Locks
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import FileEntry, Record, State
from ledger_engine.ledger.handles import Handles, PathMovedError, folder_blocked, sha256
from ledger_engine.ledger.handles import join as _join
from ledger_engine.ledger.restore import ledger_locks

LOCAL: Final = "local"
JOURNAL: Final = "journal"


@dataclass(frozen=True, slots=True)
class Listed:
    """An `.open` record found by the listing. `record` None: it cannot be read (`errno` when a read failed)."""

    journal_dir: str
    name: str
    record: Record | None
    errno: int | None = None


@dataclass(frozen=True, slots=True)
class Unlisted:
    """A journal folder (or `local/` itself) that could not be synced, opened or listed: blocks every open."""

    folder: str
    errno: int | None


@dataclass(frozen=True, slots=True)
class Listing:
    journals: tuple[Listed, ...]
    unlisted: tuple[Unlisted, ...]
    parts_removed: int


def _parent(canonical: str) -> tuple[str, str]:
    head, _, base = canonical.rpartition("/")
    return head or "/", base


def _sub_dir(h: DirHandle, name: str) -> bool:
    st = h.stat(name)
    return st is not None and st.kind is Kind.DIR


def list_journals(fs: FileSystem, app_local_data: str) -> Listing:
    """§8.1 "What" (the module's docstring)."""
    found: list[Listed] = []
    unlisted: list[Unlisted] = []
    removed = 0
    local = _join(app_local_data.rstrip("/") or "/", LOCAL)
    handles = Handles(fs)
    try:
        try:
            r = fs.resolve(local)
            if r.rest:
                return Listing((), (), 0)  # no `local/` yet: no save was ever journalled here
            local = core_paths.canonical_path(r)
            keys = [k for k in handles.at(local).names() if _sub_dir(handles.at(local), k)]
        except (OSError, PathMovedError) as e:
            return Listing((), (Unlisted(local, getattr(e, "errno", None)),), 0)
        for key in keys:
            folder = _join(_join(local, key), JOURNAL)
            try:
                if not _sub_dir(handles.at(_join(local, key)), JOURNAL):
                    continue
                jd = handles.at(folder)
                jd.sync()  # §8.1: list only what a power loss keeps
                names = jd.names()
            except (OSError, PathMovedError) as e:
                unlisted.append(Unlisted(folder, getattr(e, "errno", None)))
                continue
            parts = [n for n in names if (p := journal.parse_file_name(n)) is not None and p[1] is State.PART]
            gone = 0
            for n in parts:
                try:
                    jd.remove(n)
                    gone += 1
                except OSError:
                    pass  # a `.part` never blocks; the next listing tries again
            if gone:
                with contextlib.suppress(OSError):  # a `.part` a power loss brings back: the next listing's
                    jd.sync()
            removed += gone
            for n in names:
                parsed = journal.parse_file_name(n)
                if parsed is None or parsed[1] is not State.OPEN:
                    continue
                found.append(_read_record(jd, folder, n, parsed[0]))
        return Listing(tuple(found), tuple(unlisted), removed)
    finally:
        handles.close()


def _read_record(jd: DirHandle, folder: str, file: str, name: str) -> Listed:
    try:
        st = jd.stat(file)
        if st is None or st.kind is not Kind.FILE:
            return Listed(folder, name, None)
        data = jd.read(file)
    except OSError as e:
        return Listed(folder, name, None, e.errno)
    record = journal.decode(data or b"")
    if not isinstance(record, Record) or record.name != name:
        return Listed(folder, name, None)
    return Listed(folder, name, record)


# -- inspect ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Seen:
    """An entry as it stands: its kind and, for a regular file, the SHA-256 and size of its bytes."""

    kind: Kind
    sha256: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class KeptCopy:
    name: str
    path: str
    seen: Seen


@dataclass(frozen=True, slots=True)
class Inspected:
    """One file of the record. `reachable` False: its folder is not there or no longer at its recorded path. `now`
    None: nothing stands at the file. `errno`: a look that failed (then `now` and `kept` are what was seen before)."""

    path: str
    created: bool
    reachable: bool
    now: Seen | None
    kept: tuple[KeptCopy, ...]
    errno: int | None = None


def _seen(h: DirHandle, name: str, st: EntryStat | None) -> Seen | None:
    if st is None:
        return None
    if st.kind is not Kind.FILE:
        return Seen(st.kind)
    data = h.read(name)
    if data is None:
        return None
    return Seen(Kind.FILE, sha256(data), len(data))


def inspect(fs: FileSystem, record: Record) -> tuple[Inspected, ...]:
    """What the blocked screen shows for a journal (the module's docstring); writes nothing."""
    out: list[Inspected] = []
    handles = Handles(fs)
    try:
        for f in record.files:
            parent, base = _parent(f.canonical_path)
            created = f.pre is None
            try:
                h = handles.at(parent)
            except (PathMovedError, FileNotFoundError, NotADirectoryError):
                out.append(Inspected(f.canonical_path, created, False, None, ()))
                continue
            except OSError as e:
                out.append(Inspected(f.canonical_path, created, False, None, (), e.errno))
                continue
            now: Seen | None = None
            kept: list[KeptCopy] = []
            try:
                now = _seen(h, base, h.stat(base))
                for n in h.names():
                    if journal.is_kept_name(n, base, record.name):
                        seen = _seen(h, n, h.stat(n))
                        if seen is not None:
                            kept.append(KeptCopy(n, _join(parent, n), seen))
            except OSError as e:
                out.append(Inspected(f.canonical_path, created, True, now, tuple(kept), e.errno))
                continue
            out.append(Inspected(f.canonical_path, created, True, now, tuple(kept)))
        return tuple(out)
    finally:
        handles.close()


# -- the blocked screen's actions ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Done:
    """The action happened and is durable."""


@dataclass(frozen=True, slots=True)
class Refused:
    """Nothing was written. `reason`: `not_open` (no `.open` record of that name), `unreadable` (a delete needs the
    record's file list), `not_in_record`, `not_kept_name`, `not_created`, `absent`, `not_a_file`, `changed` (the bytes
    differ from those the user was shown), `path_moved`, `lock_held`, `permission` (the journal folder's rule: `path`
    the folder, `detail` the ACL entry, as for a save)."""

    reason: str
    path: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ActFailed:
    """An error the system answered; nothing was renamed or removed by the step that failed (a delete's removal may
    have happened when only its folder's sync failed: then the file is gone, which the next listing sees)."""

    path: str | None
    errno: int | None


type ActOutcome = Done | Refused | ActFailed


def _open_record(handles: Handles, journal_dir: str, name: str) -> tuple[DirHandle, Record | None] | Refused:
    """The journal folder (its path guard and the save's folder rule) and its `.open` record, decoded strictly (None
    when it cannot be read)."""
    jd = handles.at(journal_dir)
    blocked = folder_blocked(jd)
    if blocked is not None:
        return Refused("permission", journal_dir, blocked.detail)
    file = journal.file_name(name, State.OPEN)
    st = jd.stat(file)
    if st is None:
        return Refused("not_open")
    if st.kind is not Kind.FILE:
        return jd, None
    record = journal.decode(jd.read(file) or b"")
    return jd, record if isinstance(record, Record) and record.name == name else None


def _tolerant_sync(sync: Callable[[], None]) -> None:
    """A sync for Keep: `EROFS` means a read-only volume, where nothing can be pending (S0062 probe)."""
    try:
        sync()
    except OSError as e:
        if e.errno != errno.EROFS:
            raise


def keep(fs: FileSystem, locks: Locks, journal_dir: str, name: str) -> ActOutcome:
    """ "Keep the files as they are now" (the module's docstring)."""
    handles = Handles(fs)
    try:
        try:
            opened = _open_record(handles, journal_dir, name)
        except PathMovedError:
            return Refused("path_moved", journal_dir)
        except OSError as e:
            return ActFailed(journal_dir, e.errno)
        if isinstance(opened, Refused):
            return opened
        jd, record = opened
        files = record.files if record is not None else ()
        with ledger_locks(locks, files) as held:
            if held is not None:
                return Refused("lock_held", held)
            at: str | None = None
            try:
                folders: dict[str, DirHandle] = {}
                for f in files:
                    parent, base = _parent(f.canonical_path)
                    h = _present(handles, parent)
                    if h is None:
                        continue
                    folders[parent] = h
                    st = h.stat(base)
                    if st is not None and st.kind is Kind.FILE:
                        at = f.canonical_path
                        _tolerant_sync(lambda h=h, base=base: h.sync_file(base))
                for d in record.created_dirs if record is not None else ():
                    h = _present(handles, d.canonical_path)
                    if h is not None:
                        folders[d.canonical_path] = h
                for folder, h in sorted(folders.items(), key=lambda kv: kv[0].count("/"), reverse=True):
                    at = folder
                    _tolerant_sync(h.sync)
                at = journal_dir
                jd.rename_excl(journal.file_name(name, State.OPEN), journal.file_name(name, State.KEPT))
                jd.sync()
            except PathMovedError as e:  # red team r1 R1-3: what stands there now may not be synced
                return Refused("path_moved", e.canonical)
            except OSError as e:
                return ActFailed(at, e.errno)
        return Done()
    finally:
        handles.close()


def _present(handles: Handles, folder: str) -> DirHandle | None:
    """The folder's handle, or None when it is not there (`ENOENT`, `ENOTDIR`: nothing to sync). A folder no longer
    at its recorded path raises `PathMovedError`: something stands there whose bytes Keep cannot sync."""
    try:
        return handles.at(folder)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _entry_of(record: Record, file: str) -> FileEntry | None:
    return next((f for f in record.files if f.canonical_path == file), None)


def delete_kept(
    fs: FileSystem, locks: Locks, journal_dir: str, name: str, file: str, kept: str, seen: str
) -> ActOutcome:
    """Delete the kept copy `kept` (an entry name beside the record's file `file`) whose bytes the user saw as
    `seen` (the module's docstring)."""

    def chosen(record: Record) -> tuple[FileEntry, str] | Refused:
        f = _entry_of(record, file)
        if f is None:
            return Refused("not_in_record")
        _, base = _parent(f.canonical_path)
        if not journal.derives_kept_name(kept, base, record.name):
            return Refused("not_kept_name")
        return f, kept

    return _delete_one(fs, locks, journal_dir, name, seen, chosen)


def delete_created(fs: FileSystem, locks: Locks, journal_dir: str, name: str, file: str, seen: str) -> ActOutcome:
    """Delete what stands at `file`, a path the record says the save was creating, whose bytes the user saw as
    `seen` (the module's docstring)."""

    def chosen(record: Record) -> tuple[FileEntry, str] | Refused:
        f = _entry_of(record, file)
        if f is None:
            return Refused("not_in_record")
        if f.pre is not None:
            return Refused("not_created")
        return f, _parent(f.canonical_path)[1]

    return _delete_one(fs, locks, journal_dir, name, seen, chosen)


def _delete_one(
    fs: FileSystem,
    locks: Locks,
    journal_dir: str,
    name: str,
    seen: str,
    chosen: Callable[[Record], tuple[FileEntry, str] | Refused],
) -> ActOutcome:
    handles = Handles(fs)
    try:
        try:
            opened = _open_record(handles, journal_dir, name)
        except PathMovedError:
            return Refused("path_moved", journal_dir)
        except OSError as e:
            return ActFailed(journal_dir, e.errno)
        if isinstance(opened, Refused):
            return opened
        _, record = opened
        if record is None:
            return Refused("unreadable")
        pick = chosen(record)
        if isinstance(pick, Refused):
            return pick
        f, entry = pick
        parent, _ = _parent(f.canonical_path)
        target = _join(parent, entry)
        with ledger_locks(locks, (f,)) as held:
            if held is not None:
                return Refused("lock_held", held)
            try:
                h = handles.at(parent)
            except PathMovedError:
                return Refused("path_moved", parent)
            except (FileNotFoundError, NotADirectoryError):
                return Refused("absent", target)
            except OSError as e:
                return ActFailed(parent, e.errno)
            try:
                st = h.stat(entry)
                if st is None:
                    return Refused("absent", target)
                if st.kind is not Kind.FILE:
                    return Refused("not_a_file", target)
                data = h.read(entry)
                if data is None:
                    return Refused("absent", target)
                if sha256(data) != seen:
                    return Refused("changed", target)
                h.remove(entry)
                h.sync()
            except OSError as e:
                return ActFailed(target, e.errno)
        return Done()
    finally:
        handles.close()
