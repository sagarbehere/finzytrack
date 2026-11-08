"""Recover-all's work on disk, inside an engine that holds the engine lock (`design/specs/engine-jobs.md` §8.1 and
its "Afterwards", §11's blocked-screen rows; ADR-0063 decision 2 and "Keep the files as they are now"; ADR-0064 and
its engine-lock amendment; S0062). The sidecar's `recover_all` job orders these through the protocol (ops
`recover_list`, `recover_try`, `recover_act`); it never reads a journal or a ledger file itself, so an engine of an
earlier session caught between its handshake and its `flock` can never run beside them (BL-0151's gap).

- **`list_journals`**: every `<app_local_data>/local/<key>/journal/` folder is `durable_sync`ed before it is read
  (what the listing finds survives a power loss; model finding F1); every
  `.part` record in it is removed (no replace happens before a record is `.open`, §6.1 step 8.2: it names nothing to
  undo and never blocks; a removal that fails leaves it, harmless); every `.open` record is read and decoded strictly
  (`Listed.record` None when it cannot be: torn, foreign, newer, of another name, or a read error; such a journal
  names nothing it can be matched on and blocks every open, ADR-0063). A journal folder that cannot be synced,
  opened or listed is `Unlisted`: it may hold an `.open` record nobody can see, so it blocks every open too (fails
  closed). Only real folders are walked; a file in `local/` is not ours and is skipped. **A link in place of a
  `local/<key>` or `local/<key>/journal` folder** (S0077, BL-0193) is never walked as ours (no sync, no `.part` removed,
  no record read through it: the save refuses to write through one, `save._ensure_dir`), but it is never skipped
  blindly either, since the save's gate reads through it: its names are listed through the link, read-only, and one
  that holds an `.open` record, or cannot be opened or listed through the link (it may lead to an unplugged disk; a link
  to a file holds no record and blocks nothing), is `Unlisted(linked=True)`: it blocks every open, and the screen
  says to put the real folder back or remove the link. A real journal folder holding an `.open` record whose key's
  `backups` is a link is `Unlisted(linked=True)` naming that link too, and is not tried (the try's path guard would
  stop it `backup_bad` with the backups whole behind the link; red team M1).
- **`inspect`**: what stands at each file a record names now, and every kept copy of this journal beside it (by
  `journal.is_kept_name`), each by its kind, SHA-256 and size; it writes nothing. The blocked screen shows these
  ("Show both", "Show it", Keep's list of the files it releases) and a delete names the SHA-256 it showed.
- **`keep`** ("Keep the files as they are now", §8.1 "Afterwards"; finding N1): every file the record
  names that is there as a regular file `durable_sync`ed, and every folder it names that is there synced, so "now" is
  what a power loss keeps (a save cut by a crash may have left an exchange unsynced); then `.open` renamed `.kept`
  and the journal folder synced. A folder that is not there (an unplugged disk, a moved folder: `ENOENT`, `ENOTDIR`) has
  nothing to sync, and one no longer at its recorded path (a link in its place, a rename in case only) refuses Keep
  (`path_moved`: red team r1 R1-3); a sync that answers `EROFS` has nothing pending (a read-only volume: probed on APFS,
  S0062, `F_FULLFSYNC` answers `EROFS` there); any other error renames nothing (`ActFailed`). A record that cannot be
  read is kept by its rename alone (ADR-0063: Keep resolves "one that cannot be read"). A replaced file that is absent
  is kept absent (ADR-0068 D2: no refusal while a file is missing on a volume without the swap); its old version stays
  in the backup.
- **`delete_kept`** (owner, 2026-10-29: "Offer to delete it (Recommended)") and **`delete_created`** (owner,
  2026-10-30: "Never remove it (Recommended)": the user deletes it, here at their request): each removes one regular
  file and nothing else, only from an `.open` record that names it: a kept name this journal derives for one of its
  files (`journal.derives_kept_name`, exact up to NFD), or the path of a file the record says the save created (a
  `FileEntry` with no pre hash), through the path guard of its folder, and only
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

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem, Kind
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import FileEntry, Record, State
from ledger_engine.ledger.handles import Handles, PathMovedError, WrongDriveError, folder_blocked, sha256
from ledger_engine.ledger.handles import join as _join

LOCAL: Final = "local"
JOURNAL: Final = "journal"
BACKUPS: Final = "backups"


@dataclass(frozen=True, slots=True)
class Listed:
    """An `.open` record found by the listing. `record` None: it cannot be read (`errno` when a read failed)."""

    journal_dir: str
    name: str
    record: Record | None
    errno: int | None = None


@dataclass(frozen=True, slots=True)
class Unlisted:
    """A journal folder (or `local/` itself) that could not be synced, opened or listed: blocks every open. `linked`:
    a link stands in place of the folder (`folder`: the link) and holds an `.open` record or could not be listed
    through (`errno`); S0077."""

    folder: str
    errno: int | None
    linked: bool = False


@dataclass(frozen=True, slots=True)
class Listing:
    journals: tuple[Listed, ...]
    unlisted: tuple[Unlisted, ...]
    parts_removed: int


def _parent(canonical: str) -> tuple[str, str]:
    head, _, base = canonical.rpartition("/")
    return head or "/", base


def _kind(h: DirHandle, name: str) -> Kind | None:
    st = h.stat(name)
    return None if st is None else st.kind


def _linked_backups(handles: Handles, key_dir: str) -> bool:
    """The key's `backups` is a link. A failed look says no: the try's own checks then fail closed."""
    try:
        return _kind(handles.at(key_dir), BACKUPS) is Kind.LINK
    except (OSError, PathMovedError):
        return False


def _through_link(fs: FileSystem, key_dir: str, linked: str) -> Unlisted | None:
    """A link in place of a key folder (`linked` is `key_dir`) or of its journal folder (`linked` is the journal
    folder): the journal folder's names listed through the link, read-only. None when it holds no `.open` record."""
    folder = _join(key_dir, JOURNAL)
    try:
        if linked == key_dir:
            with fs.open_dir(key_dir) as kd:
                if kd.stat(JOURNAL) is None:
                    return None  # the linked key folder holds no journal folder
        with fs.open_dir(folder) as jd:
            names = jd.names()
    except NotADirectoryError:
        return None  # a link to a file, or a journal that is a file behind it: no record can be there (the gate agrees)
    except OSError as e:
        return Unlisted(linked, e.errno, linked=True)
    if any((p := journal.parse_file_name(n)) is not None and p[1] is State.OPEN for n in names):
        return Unlisted(linked, None, linked=True)
    return None


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
            root = handles.at(local)
            keys = [(k, kind) for k in root.names() if (kind := _kind(root, k)) in (Kind.DIR, Kind.LINK)]
        except (OSError, PathMovedError) as e:
            return Listing((), (Unlisted(local, getattr(e, "errno", None)),), 0)
        for key, kind in keys:
            key_dir = _join(local, key)
            folder = _join(key_dir, JOURNAL)
            try:
                if kind is Kind.LINK:
                    linked: str | None = key_dir
                else:
                    jkind = _kind(handles.at(key_dir), JOURNAL)
                    if jkind not in (Kind.DIR, Kind.LINK):
                        continue
                    linked = folder if jkind is Kind.LINK else None
                if linked is not None:
                    if (u := _through_link(fs, key_dir, linked)) is not None:
                        unlisted.append(u)
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
            opens = [(n, p[0]) for n in names if (p := journal.parse_file_name(n)) is not None and p[1] is State.OPEN]
            if opens and _linked_backups(handles, key_dir):
                # the try would stop `backup_bad` at the path guard though the backups are whole behind the link
                # (S0077 red team M1): never tried; Retry works once the real folder is back
                unlisted.append(Unlisted(_join(key_dir, BACKUPS), None, linked=True))
                continue
            found.extend(_read_record(jd, folder, n, name) for n, name in opens)
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
    None: nothing stands at the file. `errno`: a look that failed (then `now` and `kept` are what was seen before).
    `backup_good`: a replaced file's backup is there and holds the bytes the record names as its old version (hashed
    again); False for a created file and whenever that is not known (S0071 round 2: the screen says "Finzytrack has
    the old version in its backup" only then)."""

    path: str
    created: bool
    reachable: bool
    now: Seen | None
    kept: tuple[KeptCopy, ...]
    errno: int | None = None
    backup_good: bool = False


def _seen(h: DirHandle, name: str, st: EntryStat | None) -> Seen | None:
    if st is None:
        return None
    if st.kind is not Kind.FILE:
        return Seen(st.kind)
    data = h.read(name)
    if data is None:
        return None
    return Seen(Kind.FILE, sha256(data), len(data))


def _backup_good(handles: Handles, record: Record, f: FileEntry) -> bool:
    """`Inspected.backup_good`: the backup read again and hashed; any error, a moved folder or another entry there
    is "not known good" (it never fails the inspection)."""
    if f.pre is None or f.backup is None:
        return False
    try:
        data = handles.at(record.backup_dir).read(f.backup)
    except (OSError, PathMovedError):
        return False
    return data is not None and sha256(data) == f.pre


def inspect(fs: FileSystem, record: Record) -> tuple[Inspected, ...]:
    """What the blocked screen shows for a journal (the module's docstring); writes nothing."""
    out: list[Inspected] = []
    handles = Handles(fs, volumes=journal.volumes(record))
    try:
        for f in record.files:
            parent, base = _parent(f.canonical_path)
            created = f.pre is None
            good = _backup_good(handles, record, f)
            try:
                h = handles.at(parent)
            except (PathMovedError, WrongDriveError, FileNotFoundError, NotADirectoryError):  # S0089: never another
                # drive's files shown as this save's
                out.append(Inspected(f.canonical_path, created, False, None, (), backup_good=good))
                continue
            except OSError as e:
                out.append(Inspected(f.canonical_path, created, False, None, (), e.errno, good))
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
                out.append(Inspected(f.canonical_path, created, True, now, tuple(kept), e.errno, good))
                continue
            out.append(Inspected(f.canonical_path, created, True, now, tuple(kept), backup_good=good))
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
    differ from those the user was shown), `path_moved`, `permission` (the journal folder's rule: `path` the folder,
    `detail` the ACL entry, as for a save), `wrong_drive` and `unknown_drive` (the file's folder is on another drive
    than the record names, or on one with no UUID: S0089; `path` the folder)."""

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


def keep(fs: FileSystem, journal_dir: str, name: str) -> ActOutcome:
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
        if record is not None:
            handles.volumes = journal.volumes(record)  # S0089: nothing synced on another drive
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
    """The folder's handle, or None when it is not there (`ENOENT`, `ENOTDIR`: nothing to sync) or on another drive than
    the record names, or one with no UUID (S0089: Keep writes no ledger byte and syncs nothing there, as for a drive
    that is not plugged in; the user chose to stop undoing this save). A folder no longer at its recorded path raises
    `PathMovedError`: something stands there whose bytes Keep cannot sync."""
    try:
        return handles.at(folder)
    except (FileNotFoundError, NotADirectoryError, WrongDriveError):
        return None


def _entry_of(record: Record, file: str) -> FileEntry | None:
    return next((f for f in record.files if f.canonical_path == file), None)


def delete_kept(fs: FileSystem, journal_dir: str, name: str, file: str, kept: str, seen: str) -> ActOutcome:
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

    return _delete_one(fs, journal_dir, name, seen, chosen)


def delete_created(fs: FileSystem, journal_dir: str, name: str, file: str, seen: str) -> ActOutcome:
    """Delete what stands at `file`, a path the record says the save was creating, whose bytes the user saw as
    `seen` (the module's docstring)."""

    def chosen(record: Record) -> tuple[FileEntry, str] | Refused:
        f = _entry_of(record, file)
        if f is None:
            return Refused("not_in_record")
        if f.pre is not None:
            return Refused("not_created")
        return f, _parent(f.canonical_path)[1]

    return _delete_one(fs, journal_dir, name, seen, chosen)


def _delete_one(
    fs: FileSystem,
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
        handles.volumes = journal.volumes(record)  # S0089: a removal only on the drive the record names
        try:
            h = handles.at(parent)
        except PathMovedError:
            return Refused("path_moved", parent)
        except WrongDriveError as e:
            return Refused(str(e), parent)
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
