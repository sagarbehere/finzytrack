"""The restore shared by the save's rollback (`design/specs/engine-jobs.md` §6.4) and recovery's per-journal try
(§8.1 steps 1-4): it takes a journal record and the disk, judges each file it names, puts the pre bytes back by the
save's own durable discipline, deletes our temps by the spec's rule and closes the record (ADR-0012, ADR-0013 and its
kept-name amendment of 2026-10-29, ADR-0063, ADR-0064; S0061). Every write goes through the file-system port, one
handle per folder (`ledger.handles`).

**Kept copies** (owner, 2026-10-29: "Yes" to the kept-name design): an outside save this restore meets at one of our
names is never swapped back and never deleted: it is moved at once to the file's kept name beside it
(`journal.kept_name`: `<name>.kept-<job>`), the folder synced, and the journal stops. While a kept copy of this
journal exists beside any of its files, every try stops (`Stopped(kept_copy)`); once the user has deleted it, the
next try can resolve.

**The order.**
0. The journal folder's path guard; for the rollback after a close that may have happened (`closed=True`), a
   `.closed` record is renamed back to `.open` and the folder synced before anything else, so no power loss can
   leave a `.closed` record over files this restore put back (S0060 verifier Q1).
1. (`recover_journal` only) the ledger-set locks of every file the record names, without waiting, in `path_key`
   order (§8.1 step 1); one held: the journal is untouched, `Failed(lock_held)`.
2. Every file checked: its folder's path guard (a folder the save made may be absent: its files are then absent);
   no kept copy of this journal beside it; what the file (one `stat`, then its bytes), its save temp, a leftover
   restore temp and, for a file at post, its backup hold, judged by `core.recovery.judge` (the temp rule lives
   there); a file to restore must be a regular file with one link (the owner's rule, `handles.hard_links`), not
   locked, its extended attributes readable, allowed to be moved away, on a volume with the swap; a folder written
   in must allow entry changes; every folder the save made that is there must be removable from its parent. Any stop:
   `Stopped(cause)` with nothing written, with one exception: a temp holding an outside save beside a file at post
   (the temp rule's stop) is moved to its kept name first, so a later revert of the file cannot expose it to the
   temp rule's deletion (S0061 red team M1).
3. For each file at post, the save's temp checked again by the same rule (outside bytes: kept, stop) and removed;
   then root first (the record lists children first, the root last; ADR-0012 F-5): a leftover restore temp removed
   without a check (§6.4), the restore temp `.<name>.ft-restore-<job>.tmp` written from the backup (hashed again) with
   the file's mode and extended attributes and synced, **swapped** in; for a created file, **renamed aside** to the
   restore temp. What was displaced is ours when it is a regular file byte-identical to the post (or pre) bytes
   (deleting it loses nothing) and is removed; anything else is an outside save in the moment between the check and
   the swap: moved to the kept name, `Stopped(displaced)`; the file keeps what now stands there. Then the save's
   temps beside files at pre, whatever they hold (a regular file: a temp cut off while written is ours, review 6 P1),
   and leftover restore temps; every folder the record names synced; each folder the save made removed if it is
   empty, deepest first, then their parents synced.
4. **Only then** `.open` renamed `.resolved` and the journal folder synced: `Resolved`.

**The step rule.** Every port operation may raise `OSError`, and the process may die at any effect. In steps 0-2 an
`OSError` or a cause found writes nothing to a ledger folder but a kept copy's rename (`Stopped`; step 0's own
rename failing: `Failed`). From step 3 on, an `OSError` stops at once: `Failed(io_error)`, every file whole at pre or
post (each swap and rename is whole), the journal `.open`, or `.resolved` when only the last sync failed (every file
is then durably at pre). A crash leaves the journal `.open` (it was durable before the first write) with each file
whole, and the next try is idempotent. An outside save displaced into the restore temp by a swap and left there by a
crash (or by an I/O error on its move to the kept name) is deleted by the next try with the leftover restore temp:
spec §15's accepted loss of "an outside write in the instant between a recovery's check and its own restore swap".
"""

from __future__ import annotations

import errno
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem, Kind, Lock, Locks, Right
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import FileEntry, Record, State
from ledger_engine.ledger.core.recovery import ABSENT, Cause, Seen, Stop, Verdict, judge, seen_file
from ledger_engine.ledger.handles import (
    Displaced,
    Handles,
    PathMovedError,
    displaced,
    hard_links,
    keep,
    sha256,
    volume_without_swap,
)


@dataclass(frozen=True, slots=True)
class Resolved:
    """The record is `.resolved` and durable; every file it names durably holds its pre bytes (a created file is
    absent), except the rollback's skipped file; no temp or restore temp of the save is left, and no kept copy of
    this journal; each folder the save made is gone unless it holds other entries (§8.1 step 4)."""

    name: str


@dataclass(frozen=True, slots=True)
class Stopped:
    """A cause found on disk, or any failure while checking (§8.1 step 2: "Any failure ... counts as unrecoverable",
    cause `io_error`): the journal stays `.open` and counts as unrecoverable (blocking reason `stopped`, §11); no
    outside byte was deleted: an outside save met at one of our names is in the file's kept copy (cause `displaced`,
    `outside_save_in_temp`), and while a kept copy exists every try stops (`kept_copy`); each file is whole at pre or
    post, or holds the outside bytes found. `path`: the canonical path of the file it stopped at, if any."""

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
    mode: int
    folder_absent: bool  # its folder is one the save made, and it is not there


def _parent(canonical: str) -> tuple[str, str]:
    head, _, base = canonical.rpartition("/")
    return head or "/", base


def _look(h: DirHandle, name: str, path: str) -> tuple[Seen, EntryStat | None]:
    """What stands at `name`: one `stat` for its kind, then its bytes; a file that changed kind or vanished between the
    two is foreign (S0061 red team L3)."""
    st = h.stat(name)
    if st is None:
        return ABSENT, None
    if st.kind is not Kind.FILE:
        return Seen(st.kind), st
    try:
        data = h.read(name)
    except OSError:
        raise _Halt(Cause.FOREIGN, path) from None
    if data is None:
        raise _Halt(Cause.FOREIGN, path)
    return seen_file(data), st


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
    """Step 2 for one file (the module's docstring)."""
    path = f.canonical_path
    handles.on = path
    parent, base = _parent(path)
    h = _folder(handles, parent, created)
    if h is None:
        v = judge(f, ABSENT, ABSENT, None, skip=skip)
        if isinstance(v, Stop):
            raise _Halt(v.cause, path)
        return _Checked(f, parent, base, v, ABSENT, 0, True)
    if any(journal.is_kept_name(n, base, record.job) for n in h.names()):
        raise _Halt(Cause.KEPT_COPY, path)  # the user has not dealt with the kept copy yet (§11)
    file, st = _look(h, base, path)
    temp, _ = _look(h, f.temp, path)
    rt = journal.restore_temp_name(base, record.job)
    rst = h.stat(rt)
    if rst is not None and rst.kind is not Kind.FILE:
        raise _Halt(Cause.FOREIGN, path)  # not a restore temp of ours (S0061 verifier V2)
    backup: Seen | None = None
    if f.pre is not None and f.backup is not None and file == Seen(Kind.FILE, f.post) and not skip:
        try:
            backup, _ = _look(handles.at(record.backup_dir), f.backup, path)
        except PathMovedError:
            raise _Halt(Cause.BACKUP_BAD, path) from None
        except OSError:
            backup = Seen(Kind.OTHER)
    v = judge(f, file, temp, backup, skip=skip)
    if isinstance(v, Stop):
        if v.cause is Cause.OUTSIDE_SAVE_IN_TEMP:
            keep(h, f.temp, base, record.job)  # out of the temp rule's reach before anything else (red team M1)
        raise _Halt(v.cause, path)
    writes_here = v is Verdict.AT_POST or temp != ABSENT or rst is not None
    if writes_here and not h.allowed(".", Right.CHANGE_ENTRIES):
        raise _Halt(Cause.PERMISSION, path)
    if v is not Verdict.AT_POST:
        return _Checked(f, parent, base, v, file, 0, False)
    assert st is not None  # noqa: S101 (a file at post was seen by `_look`)
    if hard_links(st):
        raise _Halt(Cause.HARD_LINKED, path)
    if st.locked:
        raise _Halt(Cause.LOCKED, path)
    if not h.allowed(base, Right.DELETE):
        raise _Halt(Cause.PERMISSION, path)
    if f.pre is not None and not h.allowed(base, Right.READ_XATTRS):
        raise _Halt(Cause.PERMISSION, path)  # the restore temp copies them (S0061 red team L1)
    if volume_without_swap(h):
        raise _Halt(Cause.VOLUME_NO_SWAP, path)
    return _Checked(f, parent, base, v, file, st.mode, False)


def _check_folders(handles: Handles, record: Record, removable: frozenset[str]) -> None:
    """Step 2 for the folders the save made that are there: each must be removable from its parent (entry changes in
    the parent, and its own deletion), else nothing is written (S0061 verifier V1)."""
    for d in record.created_dirs:
        if d.canonical_path not in removable:
            continue
        parent, name = _parent(d.canonical_path)
        handles.on = d.canonical_path
        try:
            ph = handles.at(parent)
        except (FileNotFoundError, NotADirectoryError):
            continue  # its parent is a folder the save made that is not there
        st = ph.stat(name)
        if st is None or st.kind is not Kind.DIR:
            continue  # not there, or not a folder of ours any more: never removed
        if not ph.allowed(".", Right.CHANGE_ENTRIES) or not ph.allowed(name, Right.DELETE):
            raise _Halt(Cause.PERMISSION, d.canonical_path)


def _settle(h: DirHandle, name: str, x: _Checked, record: Record, ours: tuple[str | None, ...]) -> None:
    """What a restore swap or a rename aside displaced to `name`: ours (byte-identical to bytes we hold) is removed;
    an outside save is moved to the kept name and the journal stops (§6.4 as decided on 2026-10-29)."""
    d = displaced(h, name, ours)
    if d is Displaced.FOREIGN:
        keep(h, name, x.base, record.job)
        raise _Halt(Cause.DISPLACED, x.entry.canonical_path)
    if d is not Displaced.GONE:
        h.remove(name)  # ours, a second link included: its other name keeps the same bytes


def _put_back(handles: Handles, record: Record, x: _Checked) -> None:
    """Step 3 for one file at post: its pre bytes back (a created file removed)."""
    f = x.entry
    handles.on = f.canonical_path
    h = handles.at(x.parent)
    rt = journal.restore_temp_name(x.base, record.job)
    if h.stat(rt) is not None:
        h.remove(rt)  # a leftover of a try that was cut off: deleted without a check (§6.4)
    if f.pre is None:
        h.rename_excl(x.base, rt)  # aside first, then hashed (§6.4)
        _settle(h, rt, x, record, (f.post,))
        return
    assert f.backup is not None  # noqa: S101 (decode: a backup exactly when there is a pre hash)
    data = handles.at(record.backup_dir).read(f.backup)
    if data is None or sha256(data) != f.pre:
        raise _Halt(Cause.BACKUP_BAD, f.canonical_path)
    h.create(rt, data, x.mode, like=x.base)  # its data synced before the swap (fidelity review 2, H1)
    h.swap(rt, x.base)
    _settle(h, rt, x, record, (f.post, f.pre))


def _remove_save_temp(h: DirHandle, x: _Checked, record: Record) -> None:
    """The save's temp beside a file at post (or the skipped file), checked again by the temp rule, then removed; an
    outside program that wrote into it since step 2 has its bytes moved to the kept name (S0060 red team finding 2)."""
    if h.stat(x.entry.temp) is None:
        return
    try:
        temp, _ = _look(h, x.entry.temp, x.entry.canonical_path)
    except _Halt:
        temp = Seen(Kind.OTHER)
    v = judge(x.entry, x.file, temp, Seen(Kind.FILE, x.entry.pre), skip=x.verdict is Verdict.KEEP)
    if isinstance(v, Stop):
        keep(h, x.entry.temp, x.base, record.job)
        raise _Halt(v.cause, x.entry.canonical_path)
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
            _remove_save_temp(handles.at(x.parent), x, record)
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
    of a created path an outside file took before the save's exclusive rename (the rollback). `made`: the `path_key`s
    of the folders the save really made (the rollback; recovery: every recorded one)."""
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
            _check_folders(handles, record, removable)
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
