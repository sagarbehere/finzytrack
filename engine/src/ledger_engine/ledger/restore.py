"""The restore shared by the save's rollback (`design/specs/engine-jobs.md` §6.4) and recovery's per-journal try
(§8.1 steps 1-4): it takes a journal record and the disk, judges each file it names, puts the pre bytes back by the
save's own durable discipline, deletes our temps by the spec's rule and closes the record (ADR-0012, ADR-0013 and its
kept-name amendment of 2026-10-29, ADR-0063, ADR-0064; S0061). Every write goes through the file-system port, one
handle per folder (`ledger.handles`).

**Kept copies** (owner, 2026-10-29: "Yes" to the kept-name design): an outside save this restore meets at one of our
names is never swapped back and never deleted: it is moved at once to the file's kept name beside it
(`journal.kept_name`: `<name>.kept-<session>-<job>`), the folder synced, and the journal stops. While a kept copy of
this journal exists beside any of its files, every try stops (`Stopped(kept_copy)`); once the user has deleted it, the
next try can resolve.

**The order.**
0. The journal folder's path guard; for the rollback after a close that may have happened (`closed=True`), a
   `.closed` record is renamed back to `.open` and the folder synced before anything else, so no power loss can
   leave a `.closed` record over files this restore put back (S0060 verifier Q1).
1. (`recover_journal` only) the ledger-set locks of every file the record names, without waiting, in `path_key`
   order (§8.1 step 1); one held: the journal is untouched, `Failed(lock_held)`.
2. Every file checked: its folder's path guard (a folder the save made may be absent: its files are then absent);
   no kept copy of this journal beside it; what the file (one `stat`, then its bytes), its four names of ours (the
   save's temp and staging name, the restore temp and restore staging name: `journal.scratch_names`) and, for a file
   at post, its backup hold, judged by `core.recovery.judge`; the names by the temp rule (owner, 2026-10-30:
   "Staging name (Recommended)": a regular file at a staging name is ours whatever it holds,
   `core.recovery.classify_staging`; a temp name is ours only when it equals the pre or the post bytes,
   `classify_temp`). **Any read error** (the file, a name of ours, the backup) fails the try, `Failed(io_error)`, with
   nothing written (owner, 2026-10-30: "Yes to both (Recommended)"; red team r4 O1, r5 F3). Anything at a created
   path is `taken` and never touched (owner, 2026-10-30: "Never remove it (Recommended)"). Every name of ours that
   step 3 would remove must be removable (its deletion allowed, no lock flag: red team r5 F2); a file to restore must
   be a regular file with one link (the owner's rule, `handles.hard_links`), not locked, its extended attributes
   readable, allowed to be moved away, on a volume with the swap; a folder written in must allow entry changes;
   every folder the save made that would be removed must be removable from its parent. Any stop: `Stopped(cause)`
   with nothing written, with one exception: a temp that is not ours is moved to its kept name first, out of reach of
   every deletion.
3. Every folder the record names synced first (a swap a crashed save or try left unsynced is then durable before
   anything it displaced is removed: S0061 r4). Each name of ours checked again by the temp rule right before its
   removal (not ours: kept, stop; unreadable: `Failed`), beside a file at pre and beside a created path alike; beside
   a replaced file at post, the save's temp (the old object, at the pre bytes) only after that file's pre bytes are
   back, so it is never the last copy of them while the backup is read (red team r5 overflow 2). Root first (the
   record lists children first, the root last; ADR-0012 F-5): the pre bytes (the backup, hashed again) written with
   the file's mode and extended attributes under the restore staging name `.<name>.ft-restore-<session>-<job>.new`
   and synced, renamed exclusively to the restore temp `.<name>.ft-restore-<session>-<job>.tmp`, that rename made
   durable by the folder's sync (S0061 r3: verifier F1, red team F1), then **swapped** in and the folder synced (no
   removal of ours is ever durable before the swap it follows). What was displaced is ours when it is a regular file
   byte-identical to the post (or pre) bytes (deleting it loses nothing): removed; anything else is an outside save
   in the moment between the check and the swap: moved to the kept name, `Stopped(displaced)`; the file keeps what
   now stands there. Every folder the record names synced. A created path that is `taken` ends the try here:
   `Stopped(created_path_taken)`, the other files durably at pre, nothing of ours left beside them, the journal
   `.open` (§11: the user deletes the file and presses Retry, or keeps the files as they are now). Otherwise each
   folder the save made is removed if it is empty, deepest first, and their parents synced.
4. **Only then** `.open` renamed `.resolved` and the journal folder synced: `Resolved`.

**The step rule.** Every port operation may raise `OSError`, and the process may die at any effect. In steps 0-2 an
`OSError` or a cause found writes nothing to a ledger folder but a kept copy's rename (`Stopped`; a read error, or
step 0's own rename failing: `Failed`). From step 3 on, an `OSError` stops at once: `Failed(io_error)`, every file
whole at pre or post (each swap and rename is whole), the journal `.open`, or `.resolved` when only the last sync
failed (every file is then durably at pre). A crash leaves the journal `.open` (it was durable before the first
write) with each file whole, and the next try is idempotent. An outside save displaced into a restore temp and left
there by a crash, or by an I/O error on its move to the kept name, is kept by the next try (the temp rule), never
deleted; a write of ours cut off at a staging name is deleted by it, whatever shape a power loss tore it into.
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
from ledger_engine.ledger.core.recovery import (
    ABSENT,
    Cause,
    Seen,
    Stop,
    Temps,
    TempState,
    Verdict,
    classify_staging,
    classify_temp,
    judge,
    seen_file,
)
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
    absent); no kept copy of this journal; no name of ours is left beside any of its files, and each folder the save
    made is gone unless it holds other entries (§8.1 step 4; an `rmdir` that answered `ENOENT` or `ENOTEMPTY` may
    leave an empty one, spec §15)."""

    name: str


@dataclass(frozen=True, slots=True)
class Stopped:
    """A cause found on disk, or a failure other than a read while checking (§8.1 step 2: "Any failure ... counts as
    unrecoverable", cause `io_error`): the journal stays `.open` and counts as unrecoverable (blocking reason
    `stopped`, §11); no outside byte was deleted: an outside save met at one of our names is in the file's kept copy
    (cause `displaced`, `outside_save_in_temp`), and while a kept copy exists every try stops (`kept_copy`); each file
    is whole at pre or post, or holds the outside bytes found. `created_path_taken`: something stands at a path the
    save was creating, left as it is; every other file is durably at pre. `path`: the canonical path of the file it
    stopped at, if any."""

    cause: Cause
    path: str | None


@dataclass(frozen=True, slots=True)
class Failed:
    """An I/O error while restoring, any read error while checking, or a ledger-set lock held by another process: the
    journal stays `.open` (or `.resolved` when only the last sync failed), each file whole at pre or post; nothing
    was moved by the check that failed; blocking reason `failed` (§8.1 step 1, step 3; Retry may still undo it).
    `path` and `errno`: the file and the error, when known, so a permanent cause (a file nobody may read) is visible
    on the blocked screen (§11)."""

    cause: Cause
    path: str | None
    errno: int | None = None


RestoreOutcome = Resolved | Stopped | Failed


class _Halt(Exception):
    def __init__(self, cause: Cause, path: str | None) -> None:
        super().__init__(cause.value)
        self.cause = cause
        self.path = path


class _Fail(_Halt):
    """A read that failed (the file, a name of ours, the backup, what a swap displaced) or a name of ours that vanished
    between its `stat` and its read: `Failed(io_error)`, nothing written by the check that met it, so the next try
    reads it again (S0061 red team r4 O1, r5 F3)."""

    def __init__(self, cause: Cause, path: str | None, err: int | None = None) -> None:
        super().__init__(cause, path)
        self.errno = err


@dataclass(frozen=True, slots=True)
class _Checked:
    """What step 2 found for one file of the record."""

    entry: FileEntry
    parent: str
    base: str
    verdict: Verdict
    mode: int
    folder_absent: bool  # its folder is one the save made, and it is not there


def _parent(canonical: str) -> tuple[str, str]:
    head, _, base = canonical.rpartition("/")
    return head or "/", base


def _read(h: DirHandle, name: str, path: str) -> bytes | None:
    """The bytes of the regular file `name`; a read error fails the try with nothing written (`_Fail`)."""
    try:
        return h.read(name)
    except OSError as e:
        raise _Fail(Cause.IO_ERROR, path, e.errno) from None


def _look(h: DirHandle, name: str, path: str) -> tuple[Seen, EntryStat | None]:
    """What stands at `name`: one `stat` for its kind, then its bytes; a file that vanished between the two is foreign
    (S0061 red team L3); one whose bytes cannot be read fails the try (`_Fail`)."""
    st = h.stat(name)
    if st is None:
        return ABSENT, None
    if st.kind is not Kind.FILE:
        return Seen(st.kind), st
    data = _read(h, name, path)
    if data is None:
        raise _Halt(Cause.FOREIGN, path)
    return seen_file(data), st


def _temp_state(
    h: DirHandle, name: str, held: tuple[str | None, ...], path: str, *, staged: bool
) -> tuple[TempState, EntryStat | None]:
    """The temp rule for what stands at `name` now: a staging name (`staged`) by `classify_staging`, a temp name by
    `classify_temp` (the module's docstring); with its `stat`."""
    st = h.stat(name)
    if st is None:
        return TempState.ABSENT, None
    if staged:
        return classify_staging(st.kind), st
    data = _read(h, name, path) if st.kind is Kind.FILE else None
    return classify_temp(st.kind, data, held), st


_STAGED: tuple[bool, ...] = (False, True, False, True)  # which of `journal.scratch_names` are staging names


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


def _check(handles: Handles, record: Record, f: FileEntry, *, created: frozenset[str]) -> _Checked:
    """Step 2 for one file (the module's docstring)."""
    path = f.canonical_path
    handles.on = path
    parent, base = _parent(path)
    h = _folder(handles, parent, created)
    if h is None:
        v = judge(f, ABSENT, Temps(), None)
        if isinstance(v, Stop):
            raise _Halt(v.cause, path)
        return _Checked(f, parent, base, v, 0, True)
    if any(journal.is_kept_name(n, base, record.name) for n in h.names()):
        raise _Halt(Cause.KEPT_COPY, path)  # the user has not dealt with the kept copy yet (§11)
    file, st = _look(h, base, path)
    names = journal.scratch_names(base, record.name)
    looked = [_temp_state(h, n, (f.pre, f.post), path, staged=s) for n, s in zip(names, _STAGED, strict=True)]
    temps = Temps(*(state for state, _ in looked))
    backup: Seen | None = None
    if f.pre is not None and f.backup is not None and file == Seen(Kind.FILE, f.post):
        try:
            backup, _ = _look(handles.at(record.backup_dir), f.backup, path)
        except PathMovedError:
            raise _Halt(Cause.BACKUP_BAD, path) from None
        except OSError:
            backup = Seen(Kind.OTHER)  # its folder or its entry cannot be reached
    v = judge(f, file, temps, backup)
    if isinstance(v, Stop) and v.cause is Cause.IO_ERROR:
        raise _Fail(v.cause, path)  # a name of ours vanished between its stat and its read: nothing written
    if isinstance(v, Stop):
        for n, state in zip(names, temps.states(), strict=True):
            if state is TempState.FOREIGN:
                keep(h, n, base, record.name)  # out of reach of every deletion (owner, 2026-10-29)
        raise _Halt(v.cause, path)
    ours = [(n, nst) for n, (state, nst) in zip(names, looked, strict=True) if state is TempState.OURS]
    for n, nst in ours:  # each removal of step 3 allowed (red team r5 F2: an inherited `deny delete`, a lock flag;
        # `DELETE` answers for the folder's side too: a mode 0555 or `deny delete_child`, probed on APFS)
        if (nst is not None and nst.locked) or not h.allowed(n, Right.DELETE):
            raise _Halt(Cause.PERMISSION, path)
    if v is not Verdict.AT_POST:
        return _Checked(f, parent, base, v, 0, False)
    if not h.allowed(".", Right.CHANGE_ENTRIES):
        raise _Halt(Cause.PERMISSION, path)  # the restore temp is made here (`deny add_file` allows removals only)
    assert st is not None  # noqa: S101 (a file at post was seen by `_look`)
    if hard_links(st):
        raise _Halt(Cause.HARD_LINKED, path)
    if st.locked:
        raise _Halt(Cause.LOCKED, path)
    if not h.allowed(base, Right.DELETE):
        raise _Halt(Cause.PERMISSION, path)
    if not h.allowed(base, Right.READ_XATTRS):
        raise _Halt(Cause.PERMISSION, path)  # the restore temp copies them (S0061 red team L1)
    if volume_without_swap(h):
        raise _Halt(Cause.VOLUME_NO_SWAP, path)
    return _Checked(f, parent, base, v, st.mode, False)


def _ours_in(record: Record, folder: str) -> set[str]:
    """The names this restore may remove from `folder`: the names of ours beside the record's files there
    (`journal.scratch_names`; never a file at a created path), and the folders the save made in it."""
    out: set[str] = set()
    for f in record.files:
        parent, base = _parent(f.canonical_path)
        if parent == folder:
            out |= set(journal.scratch_names(base, record.name))
    for d in record.created_dirs:
        parent, base = _parent(d.canonical_path)
        if parent == folder:
            out.add(base)
    return out


def _check_folders(handles: Handles, record: Record, removable: frozenset[str]) -> None:
    """Step 2 for the folders the save made that are there and would be removed (they hold nothing but names this
    restore removes): each must be removable from its parent (entry changes in the parent, and its own deletion),
    else nothing is written (S0061 verifier V1; a folder holding other entries is never removed: red team r2 L5)."""
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
        if not set(handles.at(d.canonical_path).names()) <= _ours_in(record, d.canonical_path):
            continue  # it holds another entry: it stays
        if not ph.allowed(".", Right.CHANGE_ENTRIES) or not ph.allowed(name, Right.DELETE):
            raise _Halt(Cause.PERMISSION, d.canonical_path)


def _settle(h: DirHandle, name: str, x: _Checked, record: Record, ours: tuple[str | None, ...]) -> None:
    """What a restore swap displaced to `name`: ours (byte-identical to bytes we hold) is removed;
    an outside save is moved to the kept name and the journal stops (§6.4 as decided on 2026-10-29); when every kept
    name is taken it stays where it is, and the journal stops anyway."""
    d = displaced(h, name, ours)
    if d is Displaced.FOREIGN:
        keep(h, name, x.base, record.name)
        raise _Halt(Cause.DISPLACED, x.entry.canonical_path)
    if d is not Displaced.GONE:
        h.remove(name)  # ours, a second link included: its other name keeps the same bytes


def _put_back(handles: Handles, record: Record, x: _Checked) -> None:
    """Step 3 for one replaced file at post: its pre bytes back."""
    f = x.entry
    handles.on = f.canonical_path
    h = handles.at(x.parent)
    _, _, rt, rs = journal.scratch_names(x.base, record.name)
    assert f.backup is not None  # noqa: S101 (decode: a backup exactly when there is a pre hash; a file at post)
    data = handles.at(record.backup_dir).read(f.backup)
    if data is None or sha256(data) != f.pre:
        raise _Halt(Cause.BACKUP_BAD, f.canonical_path)
    h.create(rs, data, x.mode, like=x.base)  # its data synced before the swap (fidelity review 2, H1)
    h.rename_excl(rs, rt)  # exclusive: an entry that appeared there since its clean-up is never replaced (`Failed`)
    h.sync()  # the restore temp's name durable before its swap (S0061 r3: verifier F1, red team F1)
    h.swap(rt, x.base)
    h.sync()  # the swap durable before what it displaced is removed (S0061 r4: an in-place write, then a power loss)
    _settle(h, rt, x, record, (f.post, f.pre))


def _clean(h: DirHandle, name: str, x: _Checked, record: Record, *, staged: bool) -> None:
    """A name of ours, checked again by the temp rule right before its removal: ours is removed; an outside save
    (written into a temp since step 2, or anything not a regular file) is moved to the kept name, and the journal
    stops; a read error fails the try with nothing moved."""
    path = x.entry.canonical_path
    state, _ = _temp_state(h, name, (x.entry.pre, x.entry.post), path, staged=staged)
    if state is TempState.UNREADABLE:
        raise _Fail(Cause.IO_ERROR, path)  # vanished since its stat: left as it is, the next try looks again
    if state is TempState.FOREIGN:
        keep(h, name, x.base, record.name)
        raise _Halt(Cause.OUTSIDE_SAVE_IN_TEMP, path)
    if state is TempState.OURS:
        h.remove(name)


def _sync_folders(handles: Handles, folders: list[str]) -> None:
    """A `durable_sync` of every folder in `folders` that is there (path-guarded), each once."""
    for folder in dict.fromkeys(folders):
        try:
            h = handles.at(folder)
        except (FileNotFoundError, NotADirectoryError):
            continue  # a folder the save made, not there (checked at step 2)
        h.sync()


def _restore_all(handles: Handles, record: Record, checked: list[_Checked], removable: frozenset[str]) -> str | None:
    """Step 3 (the module's docstring); the canonical path of a created path that is `taken`, if any (then nothing
    more is done: the journal stops). The folders synced are every folder the record names, not only those this try
    changed: an earlier try cut off by a crash may have left its swaps and removals unsynced, and `.resolved` must
    never be durable before them (found by the crash-during-recovery sweep, S0061). They are synced first too, before
    the first removal: a swap a crashed save or try left unsynced must be durable before we remove what it displaced,
    or a power loss could drop the swap and keep the removal, deleting what an editor wrote in place through the
    file's name meanwhile (S0061 r4, found by re-running the red team's restore probe)."""
    _sync_folders(handles, [x.parent for x in checked if not x.folder_absent])
    for x in checked:
        if x.folder_absent:
            continue
        handles.on = x.entry.canonical_path
        h = handles.at(x.parent)
        for k, (n, staged) in enumerate(zip(journal.scratch_names(x.base, record.name), _STAGED, strict=True)):
            if k == 0 and x.verdict is Verdict.AT_POST:
                continue  # the old object at the pre bytes: removed only once they are back (below)
            _clean(h, n, x, record, staged=staged)
    for x in reversed(checked):
        if x.verdict is Verdict.AT_POST:
            _put_back(handles, record, x)
            _clean(handles.at(x.parent), journal.scratch_names(x.base, record.name)[0], x, record, staged=False)
    handles.on = None
    _sync_folders(handles, [x.parent for x in checked])
    taken = next((x.entry.canonical_path for x in checked if x.verdict is Verdict.TAKEN), None)
    if taken is not None:
        return taken  # the folders the save made stay for the try that resolves
    parents = [_parent(d.canonical_path)[0] for d in record.created_dirs]
    for d in reversed(record.created_dirs):
        if d.canonical_path not in removable:
            continue
        parent, name = _parent(d.canonical_path)
        try:
            handles.at(parent).rmdir(name)
        except (FileNotFoundError, NotADirectoryError):
            continue  # never made, already removed, or not a folder of ours any more
        except OSError as e:
            if e.errno == errno.ENOTEMPTY:
                continue  # it holds another entry: kept
            raise
    _sync_folders(handles, parents)  # every removal of a folder durable, this try's and an earlier one's
    return None


def restore(
    fs: FileSystem,
    record: Record,
    journal_dir: str,
    *,
    closed: bool = False,
    made: frozenset[str] | None = None,
) -> RestoreOutcome:
    """§8.1 steps 2-4 and §6.4 for one record (the module's docstring). `journal_dir`: the canonical path of its
    journal folder. `closed`: the record may be `.closed` (the rollback after a failed close). `made`: the `path_key`s
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
        except OSError as e:
            return Failed(Cause.IO_ERROR, None, e.errno)
        try:
            checked = [_check(handles, record, f, created=created) for f in record.files]
            _check_folders(handles, record, removable)
        except _Fail as e:
            return Failed(e.cause, e.path, e.errno)
        except _Halt as e:
            return Stopped(e.cause, e.path)
        except PathMovedError:
            return Stopped(Cause.PATH_MOVED, handles.on)
        except OSError:
            return Stopped(Cause.IO_ERROR, handles.on)  # §8.1 step 2: "Any failure ... counts as unrecoverable"
        try:
            taken = _restore_all(handles, record, checked, removable)
            if taken is not None:
                return Stopped(Cause.CREATED_PATH_TAKEN, taken)  # owner, 2026-10-30: "Never remove it (Recommended)"
            handles.on = None
            jd = handles.at(journal_dir)
            jd.rename_excl(journal.file_name(name, State.OPEN), journal.file_name(name, State.RESOLVED))
            jd.sync()
        except _Fail as e:
            return Failed(e.cause, e.path, e.errno)
        except _Halt as e:
            return Stopped(e.cause, e.path)
        except PathMovedError:
            return Stopped(Cause.PATH_MOVED, handles.on)
        except OSError as e:
            return Failed(Cause.IO_ERROR, handles.on, e.errno)
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
    except OSError as e:
        return Failed(Cause.IO_ERROR, None, e.errno)
    record = journal.decode(data or b"")
    if not isinstance(record, Record) or record.name != name:
        return Stopped(Cause.UNREADABLE, None)
    try:
        with _locks(locks, record) as held:
            if held is not None:
                return Failed(Cause.LOCK_HELD, held)
            return restore(fs, record, canonical)
    except OSError as e:
        return Failed(Cause.IO_ERROR, None, e.errno)
