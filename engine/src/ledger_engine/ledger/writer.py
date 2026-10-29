"""The save's durable order (`design/specs/engine-jobs.md` §6.1 step 8; ADR-0008 steps 5-6, ADR-0012, ADR-0013;
S0060): what a save does after its commit point (§6.1 step 7), through the file-system port only.

1. **Backups:** this save's backup folder `<backups_dir>/<session>-<job>/` made and its parent synced; each file the
   save replaces read through a handle on its folder, hashed against the planned pre hash (a file not at its pre
   bytes refuses: nothing was replaced), written as `<n>-<name>` and synced, read back and hashed again (ADR-0012
   F-1); the backup folder synced.
2. **Journal:** the record (`ledger.core.journal`) written as `<session>-<job>.part` and synced, renamed `.open`, the
   journal folder synced. It names every file by `canonical_path` and `path_key`, resolved at step 1.
3. **Per file, in the plan's order** (the caller orders children first and the root last): each directory the save
   creates made and its parent synced; the path guard (the folder's handle is still at the recorded canonical path,
   checked through the descriptor the write uses, §6.3); the temp `.<name>.ft-<job>.tmp` written with the file's
   mode and synced; a **swap** with the file, and the displaced bytes hashed against the pre hash (§6.4: other bytes
   are an outside save: swapped back at once and the folder synced, so they are kept); for a file the save creates,
   an **exclusive rename** that fails if a file appeared; the folder synced.
4. **Read-back:** every written file equals its post bytes; then the temps (now holding the displaced pre bytes, which
   the backups also hold) removed and their folders synced.
5. **Close:** `.open` renamed `.closed` and the journal folder synced. `Landed` is returned exactly then.

**The step rule.** Every port operation may raise `OSError`, and the process may die at any of them. A failure
before the first replace (the first swap or exclusive rename of a ledger file) returns `NotSaved`: no ledger file was
replaced, so it is true whatever the journal's state; `unchanged` is true only after every target was hashed again
and equals its pre bytes (§11), and `open_journal` says whether the journal was left `.open` (from its rename on),
which pauses the workspace's writes until recovery resolves it. Backups, a `.part`, temps and a folder the save made
may be left; recover-all removes a `.part` (§6.1 step 8.2) and an `.open` journal's temps. From the first replace on,
any failure returns `Unfinished`: the journal is left `.open` (the state of a failed rollback, §6.4).
TODO(BL-0151): slice 2 replaces the `.open` journal left by a failure after its rename (both outcomes) with the
in-engine rollback (§6.4, its last bullet for `NotSaved`), wherever that rollback succeeds.
A malformed plan raises `ValueError` before the first write. Anything else raised is a bug and propagates.
"""

from __future__ import annotations

import errno
import hashlib
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final

from ledger_engine.fs.port import DirHandle, FileSystem, check_name, split_absolute
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import DirEntry, FileEntry, Record, State

_SHA256: Final = re.compile(r"[0-9a-f]{64}")
BACKUP_MODE: Final = 0o600
JOURNAL_MODE: Final = 0o600
CREATED_MODE: Final = 0o644


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BadPlanError(ValueError):
    """A caller's error: a plan the writer refuses before its first write."""

    def __init__(self, what: str) -> None:
        super().__init__(f"bad save plan: {what}")


@dataclass(frozen=True, slots=True)
class Change:
    """One file the save writes: `path` as the ledger names it (absolute), `pre` the SHA-256 of its bytes when the
    plan was made (None: the save creates it), `post` its new bytes."""

    path: str
    pre: str | None
    post: bytes


@dataclass(frozen=True, slots=True)
class Commit:
    """A save past its commit point. `journal_dir` and `backups_dir` are the workspace's machine-local folders
    (`local/<key>/journal`, `local/<key>/backups`; INV-27, INV-30), absolute and existing, in any spelling (they are
    resolved first). The digests and the fingerprint are the closed record's fields (§6.1 step 8.2), carried, not
    computed, here."""

    session: str
    job: str
    workspace_key: str
    lineage: str
    engine_build: str
    post_fingerprint: str
    adopted_digest: str
    parse_digest: str
    changes: tuple[Change, ...]
    journal_dir: str
    backups_dir: str


class Reason(StrEnum):
    IO_ERROR = "io_error"  # an operation failed (a full disk, an I/O error)
    CHANGED = "changed"  # a file is not at its pre bytes, appeared, or an outside save was displaced by a swap
    PATH_MOVED = "path_moved"  # a folder is no longer at its recorded canonical path (§6.3 path guard)
    READ_BACK = "read_back"  # a written file does not hold its post bytes


class Step(StrEnum):
    BACKUP = "backup"
    JOURNAL = "journal"
    REPLACE = "replace"
    READ_BACK = "read_back"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class Landed:
    """The closed record is durable: the save has landed (§6.1 step 8.5)."""

    record: Record


@dataclass(frozen=True, slots=True)
class NotSaved:
    """No ledger file was replaced. `unchanged`: every target was hashed again and equals its pre bytes.
    `open_journal`: the journal may have been left `.open` (its rename was asked for), which pauses writes until
    recovery resolves it."""

    step: Step
    reason: Reason
    unchanged: bool
    open_journal: bool = False


@dataclass(frozen=True, slots=True)
class Unfinished:
    """The journal is `.open` and the save stopped: the files are each whole at pre or post (or an outside save kept
    in place), and recovery must resolve it. `path`: the canonical path of the file it stopped at, if any."""

    step: Step
    reason: Reason
    path: str | None


Outcome = Landed | NotSaved | Unfinished


@dataclass(frozen=True, slots=True)
class _Target:
    change: Change
    canonical: str
    key: str
    parent: str  # the canonical folder the file is in
    base: str
    temp: str
    new_dirs: tuple[str, ...]  # canonical paths of the folders the save makes for it, parents first


class _Stop(Exception):
    def __init__(self, reason: Reason, path: str | None = None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.path = path


@dataclass
class _Handles:
    """One handle per folder, opened once and checked at every use (§6.3: the check and the write go through one
    descriptor)."""

    fs: FileSystem
    open: dict[str, DirHandle] = field(default_factory=lambda: {})

    def at(self, canonical: str) -> DirHandle:
        h = self.open.get(canonical)
        if h is None:
            h = self.fs.open_dir(canonical)
            self.open[canonical] = h
        if h.path() != canonical:
            raise _Stop(Reason.PATH_MOVED, canonical)
        return h

    def close(self) -> None:
        for h in self.open.values():
            h.close()
        self.open.clear()


def _join(parent: str, name: str) -> str:
    return parent.rstrip("/") + "/" + name if parent != "/" else "/" + name


def _check_plan(c: Commit) -> None:
    journal.record_name(c.session, c.job)
    if not c.changes:
        raise BadPlanError("no_changes")
    for ch in c.changes:
        split_absolute(ch.path)
        if ch.pre is not None and not _SHA256.fullmatch(ch.pre):
            raise BadPlanError("pre_not_sha256")
    split_absolute(c.journal_dir)
    split_absolute(c.backups_dir)


def _targets(fs: FileSystem, c: Commit) -> list[_Target]:
    out: list[_Target] = []
    for ch in c.changes:
        r = fs.resolve(ch.path)
        canonical, key = core_paths.canonical_path(r), core_paths.path_key(r)
        exists = not r.rest
        if (ch.pre is not None) != exists:
            raise _Stop(Reason.CHANGED, canonical)  # a file vanished, or one the save creates appeared
        base = check_name((r.existing + r.rest)[-1])
        parent_parts = (r.existing + r.rest)[:-1]
        parent = "/" + "/".join(parent_parts)
        new_dirs = tuple("/" + "/".join(r.existing + r.rest[:i]) for i in range(1, len(r.rest)))
        out.append(
            _Target(ch, canonical, key, parent, base, journal.temp_name(base, c.job), new_dirs),
        )
    keys = [t.key for t in out]
    if len(set(keys)) != len(keys):
        raise BadPlanError("one_file_twice")
    return out


def _existing_dir(fs: FileSystem, path: str) -> str:
    """The canonical path of a folder that must exist (the journal and backup folders): the handles' path guard
    compares with it, so a caller's spelling through a link (`/var` on macOS) is resolved first."""
    r = fs.resolve(path)
    if r.rest:
        raise FileNotFoundError(errno.ENOENT, "no such folder")
    return core_paths.canonical_path(r)


def _unchanged(handles: _Handles, targets: list[_Target]) -> bool:
    """Every target hashed again equals its pre bytes (absent for a created file); any failure, or targets that
    could not be resolved, says no."""
    if not targets:
        return False
    try:
        for t in targets:
            try:
                folder = handles.at(t.parent)
            except FileNotFoundError:
                if t.new_dirs:
                    continue  # its folder was never made: absent, as planned
                raise
            data = folder.read(t.base)
            if (None if data is None else sha256(data)) != t.change.pre:
                return False
    except (OSError, _Stop):
        return False
    return True


def _backups(handles: _Handles, c: Commit, name: str, targets: list[_Target]) -> dict[str, str]:
    """Step 8.1. Returns the backup name per target key."""
    parent = handles.at(c.backups_dir)
    parent.mkdir(name)
    parent.sync()
    folder = handles.at(_join(c.backups_dir, name))
    names: dict[str, str] = {}
    for i, t in enumerate(targets):
        if t.change.pre is None:
            continue
        data = handles.at(t.parent).read(t.base)
        if data is None or sha256(data) != t.change.pre:
            raise _Stop(Reason.CHANGED, t.canonical)
        bname = f"{i}-{t.base}"
        folder.create(bname, data, BACKUP_MODE)
        back = folder.read(bname)
        if back is None or sha256(back) != t.change.pre:
            raise _Stop(Reason.IO_ERROR, t.canonical)  # ADR-0012 F-1
        names[t.key] = bname
    folder.sync()
    return names


def _record(c: Commit, name: str, targets: list[_Target], backups: dict[str, str]) -> Record:
    dirs: dict[str, DirEntry] = {}
    for t in targets:
        for d in t.new_dirs:
            if d not in dirs:
                dirs[d] = DirEntry(canonical_path=d, path_key=_dir_key(t, d))
    return Record(
        session=c.session,
        job=c.job,
        workspace_key=c.workspace_key,
        lineage=c.lineage,
        engine_build=c.engine_build,
        post_fingerprint=c.post_fingerprint,
        adopted_digest=c.adopted_digest,
        parse_digest=c.parse_digest,
        backup_dir=_join(c.backups_dir, name),
        files=tuple(
            FileEntry(
                canonical_path=t.canonical,
                path_key=t.key,
                pre=t.change.pre,
                post=sha256(t.change.post),
                backup=backups.get(t.key),
                temp=t.temp,
            )
            for t in targets
        ),
        created_dirs=tuple(dirs.values()),
    )


def _dir_key(t: _Target, d: str) -> str:
    """The key of a folder the save makes: the target's key cut to the folder's depth (same rules, same components)."""
    depth = len(split_absolute(d))
    return "/" + "/".join(split_absolute(t.key)[:depth])


@dataclass
class _Progress:
    open_journal: bool = False
    replaced: bool = False  # a ledger file's swap or exclusive rename was asked for


def _replace(handles: _Handles, t: _Target, progress: _Progress) -> None:
    """Step 8.3 for one file."""
    for d in t.new_dirs:
        parent, _, name = d.rpartition("/")
        h = handles.at(parent or "/")
        try:
            h.mkdir(name)
        except FileExistsError:
            raise _Stop(Reason.CHANGED, d) from None
        h.sync()
    h = handles.at(t.parent)
    if t.change.pre is None:
        h.create(t.temp, t.change.post, CREATED_MODE)
        progress.replaced = True
        try:
            h.rename_excl(t.temp, t.base)
        except FileExistsError:
            raise _Stop(Reason.CHANGED, t.canonical) from None
        h.sync()
        return
    h.create(t.temp, t.change.post, h.mode(t.base))
    progress.replaced = True
    h.swap(t.temp, t.base)
    try:
        displaced = h.read(t.temp)
    except OSError:
        displaced = None  # not a regular file: not ours
    if displaced is None or sha256(displaced) != t.change.pre:
        h.swap(t.temp, t.base)  # the outside save back in place, kept (§6.4)
        h.sync()  # at once (model finding F3)
        raise _Stop(Reason.CHANGED, t.canonical)
    h.sync()


def _read_back(handles: _Handles, targets: list[_Target]) -> None:
    """Step 8.4: every written file equals its post bytes (ADR-0008 step 6)."""
    for t in targets:
        if handles.at(t.parent).read(t.base) != t.change.post:
            raise _Stop(Reason.READ_BACK, t.canonical)


def durable_commit(fs: FileSystem, c: Commit) -> Outcome:
    """§6.1 step 8 for a save past its commit point (see the module's docstring)."""
    _check_plan(c)
    name = journal.record_name(c.session, c.job)
    handles = _Handles(fs)
    targets: list[_Target] = []
    step = Step.BACKUP
    try:
        try:
            targets = _targets(fs, c)
            c = replace(c, journal_dir=_existing_dir(fs, c.journal_dir), backups_dir=_existing_dir(fs, c.backups_dir))
            backups = _backups(handles, c, name, targets)
            data = journal.encode(_record(c, name, targets, backups))
            step = Step.JOURNAL
            jd = handles.at(c.journal_dir)
            jd.create(journal.file_name(name, State.PART), data, JOURNAL_MODE)
        except OSError:
            return NotSaved(step, Reason.IO_ERROR, _unchanged(handles, targets))
        except _Stop as stop:
            return NotSaved(step, stop.reason, _unchanged(handles, targets))
        progress = _Progress()
        try:
            progress.open_journal = True  # set before the call: a rename that fails may still have happened
            jd.rename_excl(journal.file_name(name, State.PART), journal.file_name(name, State.OPEN))
            jd.sync()
            step = Step.REPLACE
            for t in targets:
                _replace(handles, t, progress)
            step = Step.READ_BACK
            _read_back(handles, targets)
            touched: dict[str, DirHandle] = {}
            for t in targets:
                if t.change.pre is not None:
                    h = handles.at(t.parent)
                    h.remove(t.temp)
                    touched[t.parent] = h
            for h in touched.values():
                h.sync()
            step = Step.CLOSE
            jd.rename_excl(journal.file_name(name, State.OPEN), journal.file_name(name, State.CLOSED))
            jd.sync()
        except (OSError, _Stop) as e:
            reason, path = (e.reason, e.path) if isinstance(e, _Stop) else (Reason.IO_ERROR, None)
            if not progress.replaced:
                return NotSaved(step, reason, _unchanged(handles, targets), progress.open_journal)
            return Unfinished(step, reason, path)
        record = journal.decode(data)
        assert isinstance(record, Record)  # noqa: S101 (encode's own output)
        return Landed(record)
    finally:
        handles.close()
