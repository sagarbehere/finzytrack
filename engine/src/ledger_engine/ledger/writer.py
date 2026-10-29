"""The save's durable order (`design/specs/engine-jobs.md` §6.1 step 8; ADR-0008 steps 5-6, ADR-0012, ADR-0013;
S0060): what a save does after its commit point (§6.1 step 7), through the file-system port only.

0. **Before the first effect** (`_prepare`; every refusal here writes nothing): every path resolved (§6.3) and
   checked to be the file the plan meant: a replaced file a regular file (never a link, a FIFO or a folder), not
   locked (`uchg`), not hard-linked (`_hard_links`, the owner's decision point), the same object its canonical path
   names (`st_dev`, `st_ino`); a created path free of any entry (a dangling link included) under an existing folder.
   The plan as a whole: one entry per `path_key`, no target inside another target's path, no target at another's
   temp name, the folders the save makes once each and never two that differ only in case, no target in the
   journal or backup folder, and neither of those folders inside a folder the save writes in.
1. **Backups:** this save's backup folder `<backups_dir>/<session>-<job>/` made and its parent synced; each file the
   save replaces read through a handle on its folder (checked to be the same object again), hashed against the
   planned pre hash (a file not at its pre bytes refuses: nothing was replaced), written as `<n>-<name>` and synced,
   read back and hashed again (ADR-0012 F-1); the backup folder synced.
2. **Journal:** the record (`ledger.core.journal`) written as `<session>-<job>.part` and synced, renamed `.open`, the
   journal folder synced. It names every file by `canonical_path` and `path_key`, resolved at step 1.
3. **Per file, in the plan's order** (the caller orders children first and the root last): each directory the save
   creates made and its parent synced; the path guard (the folder's handle is still at the recorded canonical path,
   checked through the descriptor the write uses, §6.3); the temp `.<name>.ft-<job>.tmp` written with the file's
   mode, extended attributes and ACL, and synced; a **swap** with the file, and the displaced entry checked to be the
   planned object holding the pre bytes (§6.4: anything else is an outside save: swapped back at once and the folder
   synced, so it is kept); for a file the save creates, an **exclusive rename** that fails if a file appeared; the
   folder synced. Each folder the save creates is made once, before the first file in it.
4. **Read-back:** every written file equals its post bytes; then each temp, checked to still be the displaced object
   holding the pre bytes (an outside program may write through a descriptor it held: those bytes are kept, and the
   save stops unfinished, §6.4), removed, and the folders synced.
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
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem, Kind, check_name, fold, split_absolute
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
    NOT_A_FILE = "not_a_file"  # a replaced path is not a regular file (a folder, a FIFO, a device)
    LOCKED = "locked"  # a replaced file is immutable or append-only (`chflags uchg`)
    HARD_LINKED = "hard_linked"  # a replaced file has more than one hard link (`_hard_links`)


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
    recovery resolves it. `path`: the canonical path of the file the refusal is about, if any (the message names it:
    a hard-linked file, a locked one, one that changed)."""

    step: Step
    reason: Reason
    unchanged: bool
    open_journal: bool = False
    path: str | None = None


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
    index: int
    change: Change
    canonical: str
    key: str
    parent: str  # the canonical folder the file is in
    parent_key: str
    base: str
    temp: str
    temp_key: str
    identity: tuple[int, int] | None  # the replaced file's (st_dev, st_ino); None for a created file
    new_dirs: tuple[tuple[str, str], ...]  # (canonical, key) of the folders the save makes for it, parents first
    rest: tuple[str, ...]  # the components that did not exist at resolution


@dataclass(frozen=True, slots=True)
class _Plan:
    targets: tuple[_Target, ...]
    dirs: tuple[tuple[str, str], ...]  # every folder the save makes, once, parents first
    journal_dir: str
    backups_dir: str


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


def _hard_links(st: EntryStat) -> Reason | None:
    """A ledger file with more than one hard link refuses the save before anything is written (owner, 2026-10-29:
    "Refuse to save it (Recommended)"; ADR-0013 amendment of 2026-10-29). The swap gives the name a new file, so the
    other link would silently keep the old bytes. Reads are not affected. The one place this rule lives."""
    return Reason.HARD_LINKED if st.nlink > 1 else None


def _join(parent: str, name: str) -> str:
    return "/" + name if parent == "/" else f"{parent}/{name}"


def _within(key: str, folder: str) -> bool:
    """`key` is `folder` or inside it."""
    return key == folder or key.startswith(folder.rstrip("/") + "/")


def _check_plan(c: Commit) -> None:
    journal.record_name(c.session, c.job)
    if not c.changes:
        raise BadPlanError("no_changes")
    for ch in c.changes:
        split_absolute(ch.path)
        if ch.pre is not None and not _SHA256.fullmatch(ch.pre):
            raise BadPlanError("pre_not_sha256")
    for field_value in (
        c.workspace_key,
        c.lineage,
        c.engine_build,
        c.post_fingerprint,
        c.adopted_digest,
        c.parse_digest,
    ):
        if not field_value:
            raise BadPlanError("empty_record_field")  # the journal could not be written (S0060 verifier F5)
    for folder in (c.journal_dir, c.backups_dir):
        if not split_absolute(folder):
            raise BadPlanError("app_folder_is_root")


def _app_folder(fs: FileSystem, path: str) -> tuple[str, str]:
    """The canonical path and key of a folder that must exist (the journal and backup folders): the handles' path
    guard compares with the canonical path, so a caller's spelling through a link (`/var` on macOS) is resolved."""
    r = fs.resolve(path)
    if r.rest:
        raise FileNotFoundError(errno.ENOENT, "no such folder")
    if not r.existing:
        raise BadPlanError("app_folder_is_root")
    return core_paths.canonical_path(r), core_paths.path_key(r)


def _check_replaced(st: EntryStat | None, identity: tuple[int, int] | None, canonical: str) -> EntryStat:
    """The entry at a replaced file's path is the object resolution found, a regular file the save may swap."""
    if st is None or st.identity != identity:
        raise _Stop(Reason.CHANGED, canonical)  # it moved or was replaced since the path was resolved
    if st.kind is not Kind.FILE:
        raise _Stop(Reason.NOT_A_FILE, canonical)
    if st.locked:
        raise _Stop(Reason.LOCKED, canonical)
    reason = _hard_links(st)
    if reason is not None:
        raise _Stop(reason, canonical)
    return st


def _target(fs: FileSystem, handles: _Handles, index: int, c: Commit, ch: Change) -> _Target:
    r = fs.resolve(ch.path)
    canonical, key = core_paths.canonical_path(r), core_paths.path_key(r)
    parts, kparts = split_absolute(canonical), split_absolute(key)
    if not parts:
        raise BadPlanError("target_is_root")
    base = check_name(parts[-1])
    parent, parent_key = "/" + "/".join(parts[:-1]), "/" + "/".join(kparts[:-1])
    temp = journal.temp_name(base, c.job)
    temp_key = _join(parent_key, fold(temp, r.rules[-1]))
    if not r.rest:
        return _Target(index, ch, canonical, key, parent, parent_key, base, temp, temp_key, r.identity, (), ())
    n = len(r.existing)
    new_dirs = tuple(("/" + "/".join(parts[:i]), "/" + "/".join(kparts[:i])) for i in range(n + 1, len(parts)))
    return _Target(index, ch, canonical, key, parent, parent_key, base, temp, temp_key, None, new_dirs, r.rest)


def _check_target(handles: _Handles, t: _Target) -> None:
    """One target against the disk, before the first effect."""
    if not t.rest:
        if t.change.pre is None:
            raise _Stop(Reason.CHANGED, t.canonical)  # a file the save creates already exists
        _check_replaced(handles.at(t.parent).stat(t.base), t.identity, t.canonical)
        return
    if t.change.pre is not None:
        raise _Stop(Reason.CHANGED, t.canonical)  # a file the save replaces vanished
    depth = len(split_absolute(t.canonical)) - len(t.rest)
    existing = "/" + "/".join(split_absolute(t.canonical)[:depth])
    try:
        holder = handles.at(existing)
    except NotADirectoryError:
        raise _Stop(Reason.CHANGED, t.canonical) from None  # the deepest existing component is not a folder
    if holder.stat(t.rest[0]) is not None:
        raise _Stop(Reason.CHANGED, t.canonical)  # an entry stands at the path (a dangling link, S0060 verifier F3)


def _prepare(fs: FileSystem, handles: _Handles, c: Commit, targets: tuple[_Target, ...]) -> _Plan:
    """Step 0: check each target and the plan as a whole before the first effect (the module's docstring)."""
    for t in targets:
        _check_target(handles, t)
    keys = [t.key for t in targets]
    if len(set(keys)) != len(keys):
        raise BadPlanError("one_file_twice")
    for a in targets:
        for b in targets:
            if a is not b and _within(b.key, a.key):
                raise BadPlanError("file_inside_file")  # a target is a folder another target is in
            if a is not b and b.key == a.temp_key:
                raise BadPlanError("temp_name_taken")  # recovery deletes temps (S0060 red team finding 7)
    dirs: dict[str, str] = {}
    for t in targets:
        for canonical, key in t.new_dirs:
            if dirs.setdefault(key, canonical) != canonical:
                raise BadPlanError("created_folders_differ_in_case")
    journal_dir, journal_key = _app_folder(fs, c.journal_dir)
    backups_dir, backups_key = _app_folder(fs, c.backups_dir)
    for folder in (journal_key, backups_key):
        for t in targets:
            if _within(t.key, folder) or _within(folder, t.parent_key):
                raise BadPlanError("app_folder_and_ledger_folder_overlap")  # INV-27, INV-30
    return _Plan(targets, tuple((cn, k) for k, cn in dirs.items()), journal_dir, backups_dir)


def _unchanged(handles: _Handles, targets: tuple[_Target, ...]) -> bool:
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


def _backups(handles: _Handles, plan: _Plan, name: str) -> dict[str, str]:
    """Step 8.1. Returns the backup name per target key."""
    parent = handles.at(plan.backups_dir)
    parent.mkdir(name)
    parent.sync()
    folder = handles.at(_join(plan.backups_dir, name))
    names: dict[str, str] = {}
    for t in plan.targets:
        if t.change.pre is None:
            continue
        h = handles.at(t.parent)
        _check_replaced(h.stat(t.base), t.identity, t.canonical)
        data = h.read(t.base)
        if data is None or sha256(data) != t.change.pre:
            raise _Stop(Reason.CHANGED, t.canonical)
        bname = journal.backup_name(t.index, t.base)
        folder.create(bname, data, BACKUP_MODE)
        back = folder.read(bname)
        if back is None or sha256(back) != t.change.pre:
            raise _Stop(Reason.IO_ERROR, t.canonical)  # ADR-0012 F-1
        names[t.key] = bname
    folder.sync()
    return names


def _record(c: Commit, plan: _Plan, name: str, backups: dict[str, str]) -> Record:
    return Record(
        session=c.session,
        job=c.job,
        workspace_key=c.workspace_key,
        lineage=c.lineage,
        engine_build=c.engine_build,
        post_fingerprint=c.post_fingerprint,
        adopted_digest=c.adopted_digest,
        parse_digest=c.parse_digest,
        backup_dir=_join(plan.backups_dir, name),
        files=tuple(
            FileEntry(
                canonical_path=t.canonical,
                path_key=t.key,
                pre=t.change.pre,
                post=sha256(t.change.post),
                backup=backups.get(t.key),
                temp=t.temp,
            )
            for t in plan.targets
        ),
        created_dirs=tuple(DirEntry(canonical_path=cn, path_key=k) for cn, k in plan.dirs),
    )


@dataclass
class _Progress:
    open_journal: bool = False
    replaced: bool = False  # a ledger file's swap or exclusive rename was asked for, and may have happened
    made: set[str] = field(default_factory=lambda: set())  # keys of the folders made so far


def _displaced_is_ours(h: DirHandle, name: str, t: _Target) -> bool:
    """The entry `name` is the planned object (`identity`) and holds the pre bytes (§6.4)."""
    st = h.stat(name)
    if st is None or st.identity != t.identity:
        return False
    try:
        data = h.read(name)
    except OSError:
        return False
    return data is not None and sha256(data) == t.change.pre


def _replace(handles: _Handles, t: _Target, progress: _Progress) -> None:
    """Step 8.3 for one file."""
    for canonical, key in t.new_dirs:
        if key in progress.made:
            continue
        parent, _, name = canonical.rpartition("/")
        h = handles.at(parent or "/")
        try:
            h.mkdir(name)
        except FileExistsError:
            raise _Stop(Reason.CHANGED, canonical) from None
        progress.made.add(key)
        h.sync()
    h = handles.at(t.parent)
    if t.change.pre is None:
        h.create(t.temp, t.change.post, CREATED_MODE)
        before = progress.replaced
        progress.replaced = True  # an exclusive rename that answers an error may still have happened...
        try:
            h.rename_excl(t.temp, t.base)
        except FileExistsError:
            progress.replaced = before  # ...except this one: a file stood there, nothing was replaced
            raise _Stop(Reason.CHANGED, t.canonical) from None
        h.sync()
        return
    st = _check_replaced(h.stat(t.base), t.identity, t.canonical)
    h.create(t.temp, t.change.post, st.mode, like=t.base)
    progress.replaced = True
    h.swap(t.temp, t.base)
    if not _displaced_is_ours(h, t.temp, t):
        h.swap(t.temp, t.base)  # the outside save back in place, kept (§6.4)
        h.sync()  # at once (model finding F3)
        raise _Stop(Reason.CHANGED, t.canonical)
    h.sync()


def _read_back(handles: _Handles, targets: tuple[_Target, ...]) -> None:
    """Step 8.4: every written file equals its post bytes (ADR-0008 step 6)."""
    for t in targets:
        if handles.at(t.parent).read(t.base) != t.change.post:
            raise _Stop(Reason.READ_BACK, t.canonical)


def _remove_temps(handles: _Handles, targets: tuple[_Target, ...]) -> None:
    """Step 8.4: each temp removed only while it is still the displaced object with the pre bytes; otherwise an outside
    program wrote into it (through a descriptor it held from before the swap), and it is kept (§6.4; S0060 red team
    finding 2)."""
    touched: dict[str, DirHandle] = {}
    for t in targets:
        if t.change.pre is None:
            continue
        h = handles.at(t.parent)
        if not _displaced_is_ours(h, t.temp, t):
            raise _Stop(Reason.CHANGED, t.canonical)
        h.remove(t.temp)
        touched[t.parent] = h
    for h in touched.values():
        h.sync()


def durable_commit(fs: FileSystem, c: Commit) -> Outcome:
    """§6.1 step 8 for a save past its commit point (see the module's docstring)."""
    _check_plan(c)
    name = journal.record_name(c.session, c.job)
    handles = _Handles(fs)
    targets: tuple[_Target, ...] = ()
    step = Step.BACKUP
    try:
        try:
            targets = tuple(_target(fs, handles, i, c, ch) for i, ch in enumerate(c.changes))
            plan = _prepare(fs, handles, c, targets)
            backups = _backups(handles, plan, name)
            data = journal.encode(_record(c, plan, name, backups))
            step = Step.JOURNAL
            jd = handles.at(plan.journal_dir)
            jd.create(journal.file_name(name, State.PART), data, JOURNAL_MODE)
        except (OSError, _Stop) as e:
            reason, path = (e.reason, e.path) if isinstance(e, _Stop) else (Reason.IO_ERROR, None)
            return NotSaved(step, reason, _unchanged(handles, targets), path=path)
        progress = _Progress()
        try:
            progress.open_journal = True  # set before the call: a rename that fails may still have happened
            jd.rename_excl(journal.file_name(name, State.PART), journal.file_name(name, State.OPEN))
            jd.sync()
            step = Step.REPLACE
            for t in plan.targets:
                _replace(handles, t, progress)
            step = Step.READ_BACK
            _read_back(handles, plan.targets)
            _remove_temps(handles, plan.targets)
            step = Step.CLOSE
            jd.rename_excl(journal.file_name(name, State.OPEN), journal.file_name(name, State.CLOSED))
            jd.sync()
        except (OSError, _Stop) as e:
            reason, path = (e.reason, e.path) if isinstance(e, _Stop) else (Reason.IO_ERROR, None)
            if not progress.replaced:
                return NotSaved(step, reason, _unchanged(handles, plan.targets), progress.open_journal, path)
            return Unfinished(step, reason, path)
        record = journal.decode(data)
        assert isinstance(record, Record)  # noqa: S101 (encode's own output)
        return Landed(record)
    finally:
        handles.close()
