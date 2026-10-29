"""The restore shared by the save's rollback (`design/specs/engine-jobs.md` §6.4) and recovery's per-journal try
(§8.1 steps 1-4): it takes a journal record and the disk, judges each file it names, puts the pre bytes back by the
save's own durable discipline, deletes our temps by the spec's rule and closes the record (ADR-0012, ADR-0013,
ADR-0063, ADR-0064; S0061). Every write goes through the file-system port, one handle per folder (`ledger.handles`).

**The order.**
0. The journal folder's path guard; for the rollback after a close that may have happened (`closed=True`), a
   `.closed` record is renamed back to `.open` and the folder synced before anything else, so no power loss can
   leave a `.closed` record over files this restore put back (S0060 verifier Q1).
1. (`recover_journal` only) the ledger-set locks of every file the record names, without waiting, in `path_key`
   order (§8.1 step 1); one held: the journal is untouched, `Failed(lock_held)`.
2. Every file checked, nothing written: its folder's path guard (a folder the save made may be absent: its files
   are then absent), what the file, its save temp and, for a file at post, its backup hold, judged by
   `core.recovery.judge` (the temp rule lives there); a file to restore must be a regular file with one link (the
   owner's rule, `handles.hard_links`), not locked, on a volume with the swap, its folder writable and the file
   allowed to be moved away. Any stop: `Stopped(cause)`, nothing written.
3. For each file at post, the save's temp re-checked by the same rule and removed; then root first (the record lists
   children first, the root last; ADR-0012 F-5): a leftover restore temp removed without a check (§6.4), the restore
   temp `.<name>.ft-restore-<job>.tmp` written from the backup (hashed again) with the file's mode and extended
   attributes and synced, **swapped** in, and the displaced entry checked to be the object step 2 saw, with one link,
   holding the post bytes; for a created file, **renamed aside** to the restore temp and checked the same way.
   Anything else is an outside write in the moment between the check and the swap: swapped (renamed) back, the folder
   synced at once (model finding F3), `Stopped(displaced)`; a second outside save met by the swap-back is left where
   it is, nothing swapped again (S0060 red team r2 M1). The displaced post bytes are removed. Then the save's temps
   beside files at pre, whatever they hold (a regular file: a temp cut off while written is ours, review 6 P1), and
   leftover restore temps; every changed folder synced; each folder the save made removed if it is empty, deepest
   first, then their parents synced.
4. **Only then** `.open` renamed `.resolved` and the journal folder synced: `Resolved`.

**The step rule.** Every port operation may raise `OSError`, and the process may die at any effect. In steps 0-2 an
`OSError` or a cause found writes nothing to a ledger folder (`Stopped`; step 0's own rename failing: `Failed`). From
step 3 on, an `OSError` stops at once: `Failed(io_error)`, every file whole at pre or post (each swap and rename is
whole), the journal `.open`, or `.resolved` when only the last sync failed (every file is then durably at pre; a
power loss may bring the `.open` record back, and the next try finds the files at pre). A crash leaves the journal
`.open` (it was durable before the first write) with each file whole, and the next try is idempotent. An outside save
the swap-back itself failed to put back stays in the restore temp, which the next try deletes: spec §15's accepted
loss of "an outside write in the instant between a recovery's check and its own restore swap".
"""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from ledger_engine.fs.port import DirHandle, FileSystem, Kind, Lock, Locks, Right
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import FileEntry, Record, State
from ledger_engine.ledger.core.recovery import ABSENT, Cause, Seen, Stop, Verdict, judge, seen_file
from ledger_engine.ledger.handles import Handles, PathMovedError, hard_links, sha256, volume_without_swap


@dataclass(frozen=True, slots=True)
class Resolved:
    """The record is `.resolved` and durable; every file it names durably holds its pre bytes (a created file is
    absent), except the rollback's kept file; no temp or restore temp of the save is left; each folder the save made
    is gone unless it holds other entries (§8.1 step 4)."""

    name: str


@dataclass(frozen=True, slots=True)
class Stopped:
    """A cause found on disk, or any failure while checking (§8.1 step 2: "Any failure ... counts as unrecoverable",
    cause `io_error`): the journal stays `.open` and counts as unrecoverable (blocking reason `stopped`, §11); no
    outside byte was deleted; each file is whole at pre or post, or holds the outside bytes
    found. `path`: the canonical path of the file it stopped at, if any."""

    cause: Cause
    path: str | None


@dataclass(frozen=True, slots=True)
class Failed:
    """An I/O error while restoring, or a ledger-set lock held by another process: the journal stays `.open` (or
    `.resolved` when only the last sync failed), each file whole at pre or post; blocking reason `failed` (§8.1 step 1,
    step 3; Retry may still undo it)."""

    cause: Cause
    path: str | None


RestoreOutcome = Resolved | Stopped | Failed


class _Halt(Exception):
    def __init__(self, cause: Cause, path: str | None) -> None:
        super().__init__(cause.value)
        self.cause = cause
        self.path = path


@dataclass(frozen=True, slots=True)
class _Checked:
    """What step 2 found for one file of the record."""

    entry: FileEntry
    parent: str
    base: str
    verdict: Verdict
    file: Seen
    identity: tuple[int, int] | None  # the file at post, as step 2 saw it
    mode: int
    folder_absent: bool  # its folder is one the save made, and it is not there


def _parent(canonical: str) -> tuple[str, str]:
    head, _, base = canonical.rpartition("/")
    return head or "/", base


def _seen(h: DirHandle, name: str) -> Seen:
    st = h.stat(name)
    if st is None:
        return ABSENT
    if st.kind is not Kind.FILE:
        return Seen(st.kind)
    data = h.read(name)
    return ABSENT if data is None else seen_file(data)


def _folder(handles: Handles, folder: str, created: frozenset[str]) -> DirHandle | None:
    """The handle on a file's folder, path-guarded; None when it is a folder the save makes and it is not there (its
    files are then absent), once the nearest folder that existed before the save is found where it was."""
    try:
        return handles.at(folder)
    except (FileNotFoundError, NotADirectoryError):
        if folder not in created:
            raise _Halt(Cause.UNREACHABLE, handles.on) from None
    _folder(handles, _parent(folder)[0], created)
    return None


def _check(handles: Handles, record: Record, f: FileEntry, *, skip: bool, created: frozenset[str]) -> _Checked:
    """Step 2 for one file: nothing is written."""
    handles.on = f.canonical_path
    parent, base = _parent(f.canonical_path)
    h = _folder(handles, parent, created)
    if h is None:
        v = judge(f, ABSENT, ABSENT, None, skip=skip)
        if isinstance(v, Stop):
            raise _Halt(v.cause, f.canonical_path)
        return _Checked(f, parent, base, v, ABSENT, None, 0, True)
    st = h.stat(base)
    try:
        file = _seen(h, base)
        temp = _seen(h, f.temp)
    except OSError:
        raise _Halt(Cause.FOREIGN, f.canonical_path) from None  # it changed kind while read
    backup: Seen | None = None
    if f.pre is not None and f.backup is not None and file == Seen(Kind.FILE, f.post) and not skip:
        try:
            backup = _seen(handles.at(record.backup_dir), f.backup)
        except PathMovedError:
            raise _Halt(Cause.BACKUP_BAD, f.canonical_path) from None
        except OSError:
            backup = Seen(Kind.OTHER)
    v = judge(f, file, temp, backup, skip=skip)
    if isinstance(v, Stop):
        raise _Halt(v.cause, f.canonical_path)
    writes_here = v is Verdict.AT_POST or temp != ABSENT
    if writes_here and not h.allowed(".", Right.CHANGE_ENTRIES):
        raise _Halt(Cause.PERMISSION, f.canonical_path)
    if v is not Verdict.AT_POST:
        return _Checked(f, parent, base, v, file, None, 0, False)
    assert st is not None  # noqa: S101 (a file at post was seen)
    if hard_links(st):
        raise _Halt(Cause.HARD_LINKED, f.canonical_path)
    if st.locked:
        raise _Halt(Cause.LOCKED, f.canonical_path)
    if not h.allowed(base, Right.DELETE):
        raise _Halt(Cause.PERMISSION, f.canonical_path)
    if volume_without_swap(h):
        raise _Halt(Cause.VOLUME_NO_SWAP, f.canonical_path)
    return _Checked(f, parent, base, v, file, st.identity, st.mode, False)


def _holds(h: DirHandle, name: str, identity: tuple[int, int] | None, sha: str | None) -> bool:
    """The entry `name` is the object `identity`, a regular file with one link holding the bytes `sha`."""
    st = h.stat(name)
    if st is None or st.identity != identity or st.kind is not Kind.FILE or hard_links(st):
        return False
    try:
        data = h.read(name)
    except OSError:
        return False
    return data is not None and sha256(data) == sha


def _put_back(handles: Handles, record: Record, x: _Checked) -> None:
    """Step 3 for one file at post: its pre bytes back (a created file removed), each displaced entry checked."""
    f = x.entry
    handles.on = f.canonical_path
    h = handles.at(x.parent)
    rt = journal.restore_temp_name(x.base, record.job)
    if h.stat(rt) is not None:
        h.remove(rt)  # a leftover of a try that was cut off: deleted without a check (§6.4)
    if f.pre is None:
        h.rename_excl(x.base, rt)  # aside first, then hashed (§6.4)
        if not _holds(h, rt, x.identity, f.post):
            with contextlib.suppress(FileExistsError):  # a newer outside file took the name: both are kept
                h.rename_excl(rt, x.base)  # the outside save back in place
            h.sync()  # at once (model finding F3)
            raise _Halt(Cause.DISPLACED, f.canonical_path)
        h.remove(rt)
        return
    assert f.backup is not None  # noqa: S101 (decode: a backup exactly when there is a pre hash)
    data = handles.at(record.backup_dir).read(f.backup)
    if data is None or sha256(data) != f.pre:
        raise _Halt(Cause.BACKUP_BAD, f.canonical_path)
    h.create(rt, data, x.mode, like=x.base)  # its data synced before the swap (fidelity review 2, H1)
    h.swap(rt, x.base)
    if not _holds(h, rt, x.identity, f.post):
        # the outside save back in place, kept (§6.4); once only: if a second outside save took the name meanwhile,
        # the swap-back moves it into the restore temp and the first back into the file, both kept, and the next
        # try stops on the file (S0060 red team r2 M1)
        h.swap(rt, x.base)
        h.sync()  # at once (model finding F3)
        raise _Halt(Cause.DISPLACED, f.canonical_path)
    h.remove(rt)  # the displaced post bytes: ours


def _remove_save_temp(h: DirHandle, x: _Checked) -> None:
    """The save's temp beside a file at post (or the kept file), checked again by the temp rule, then removed."""
    if h.stat(x.entry.temp) is None:
        return
    try:
        temp = _seen(h, x.entry.temp)
    except OSError:
        temp = Seen(Kind.OTHER)
    v = judge(x.entry, x.file, temp, Seen(Kind.FILE, x.entry.pre), skip=x.verdict is Verdict.KEEP)
    if isinstance(v, Stop):
        raise _Halt(v.cause, x.entry.canonical_path)  # an outside program wrote into it since step 2: kept
    h.remove(x.entry.temp)


def _sync_folders(handles: Handles, folders: list[str]) -> None:
    """A `durable_sync` of every folder in `folders` that is there (path-guarded), each once."""
    for folder in dict.fromkeys(folders):
        try:
            h = handles.at(folder)
        except (FileNotFoundError, NotADirectoryError):
            continue  # a folder the save made, not there (checked at step 2)
        h.sync()


def _restore_all(handles: Handles, record: Record, checked: list[_Checked], removable: frozenset[str]) -> None:
    """Step 3 (the module's docstring). The folders synced are every folder the record names, not only those this
    try changed: an earlier try cut off by a crash may have left its swaps and removals unsynced, and `.resolved`
    must never be durable before them (found by the crash-during-recovery sweep, S0061)."""
    for x in checked:
        if x.verdict is not Verdict.AT_PRE:
            handles.on = x.entry.canonical_path
            _remove_save_temp(handles.at(x.parent), x)
    for x in reversed(checked):
        if x.verdict is Verdict.AT_POST:
            _put_back(handles, record, x)
    for x in checked:
        if x.folder_absent:
            continue
        handles.on = x.entry.canonical_path
        h = handles.at(x.parent)
        if x.verdict is Verdict.AT_PRE and h.stat(x.entry.temp) is not None:
            h.remove(x.entry.temp)  # whatever it holds (review 6 P1); step 2 saw a regular file or none
        rt = journal.restore_temp_name(x.base, record.job)
        if h.stat(rt) is not None:
            h.remove(rt)  # without a check (§6.4)
    handles.on = None
    _sync_folders(handles, [x.parent for x in checked])  # children before the folders that hold them go
    parents = [_parent(d.canonical_path)[0] for d in record.created_dirs]
    for d in reversed(record.created_dirs):
        if d.canonical_path not in removable:
            continue
        parent, name = _parent(d.canonical_path)
        try:
            ph = handles.at(parent)
            ph.rmdir(name)
        except (FileNotFoundError, NotADirectoryError):
            continue  # never made, already removed, or not a folder of ours any more
        except OSError as e:
            if e.errno == errno.ENOTEMPTY:
                continue  # it holds another entry: kept
            raise
    _sync_folders(handles, parents)  # every removal of a folder durable, this try's and an earlier one's


def restore(
    fs: FileSystem,
    record: Record,
    journal_dir: str,
    *,
    closed: bool = False,
    skip: str | None = None,
    made: frozenset[str] | None = None,
) -> RestoreOutcome:
    """§8.1 steps 2-4 and §6.4 for one record (the module's docstring). `journal_dir`: the canonical path of its
    journal folder. `closed`: the record may be `.closed` (the rollback after a failed close). `skip`: the `path_key`
    of the file whose outside save the save kept (the rollback). `made`: the `path_key`s of the folders the save
    really made (the rollback; recovery: every recorded one)."""
    handles = Handles(fs)
    name = record.name
    created = frozenset(d.canonical_path for d in record.created_dirs)
    removable = (
        created if made is None else frozenset(d.canonical_path for d in record.created_dirs if d.path_key in made)
    )
    try:
        try:
            jd = handles.at(journal_dir)
            if closed and jd.stat(journal.file_name(name, State.CLOSED)) is not None:
                jd.rename_excl(journal.file_name(name, State.CLOSED), journal.file_name(name, State.OPEN))
                jd.sync()
        except PathMovedError:
            return Stopped(Cause.PATH_MOVED, None)
        except OSError:
            return Failed(Cause.IO_ERROR, None)
        try:
            checked = [_check(handles, record, f, skip=f.path_key == skip, created=created) for f in record.files]
        except _Halt as e:
            return Stopped(e.cause, e.path)
        except PathMovedError:
            return Stopped(Cause.PATH_MOVED, handles.on)
        except OSError:
            return Stopped(Cause.IO_ERROR, handles.on)  # §8.1 step 2: "Any failure ... counts as unrecoverable"
        try:
            _restore_all(handles, record, checked, removable)
            handles.on = None
            jd = handles.at(journal_dir)
            jd.rename_excl(journal.file_name(name, State.OPEN), journal.file_name(name, State.RESOLVED))
            jd.sync()
        except _Halt as e:
            return Stopped(e.cause, e.path)
        except PathMovedError:
            return Stopped(Cause.PATH_MOVED, handles.on)
        except OSError:
            return Failed(Cause.IO_ERROR, handles.on)
        return Resolved(name)
    finally:
        handles.close()


@contextmanager
def _locks(locks: Locks, record: Record) -> Generator[str | None]:
    """§8.1 step 1: every lock the record names, without waiting, in `path_key` order; yields the canonical path of a
    file whose lock is held (None: all taken). Released at the end."""
    taken: list[Lock] = []
    by_key = {f.path_key: f.canonical_path for f in record.files}
    try:
        held: str | None = None
        for key in sorted(by_key):
            lock = locks.try_lock(key)
            if lock is None:
                held = by_key[key]
                break
            taken.append(lock)
        yield held
    finally:
        for lock in taken:
            lock.release()


def recover_journal(fs: FileSystem, locks: Locks, journal_dir: str, name: str) -> RestoreOutcome:
    """Recovery's try of one `.open` journal (§8.1 steps 1-4): the record `<name>.open` in `journal_dir` read and
    decoded strictly (a torn, foreign or newer record, or one of another name, is `Stopped(unreadable)`), its locks
    taken without waiting, then `restore`."""
    try:
        r = fs.resolve(journal_dir)
        if r.rest or not r.existing:
            return Stopped(Cause.UNREACHABLE, None)
        canonical = core_paths.canonical_path(r)
        with fs.open_dir(canonical) as jd:
            st = jd.stat(journal.file_name(name, State.OPEN))
            if st is None:
                return Failed(Cause.IO_ERROR, None)  # listed, but gone: never expected under the engine lock
            if st.kind is not Kind.FILE:
                return Stopped(Cause.UNREADABLE, None)
            data = jd.read(journal.file_name(name, State.OPEN))
    except OSError:
        return Failed(Cause.IO_ERROR, None)
    record = journal.decode(data or b"")
    if not isinstance(record, Record) or record.name != name:
        return Stopped(Cause.UNREADABLE, None)
    try:
        with _locks(locks, record) as held:
            if held is not None:
                return Failed(Cause.LOCK_HELD, held)
            return restore(fs, record, canonical)
    except OSError:
        return Failed(Cause.IO_ERROR, None)
