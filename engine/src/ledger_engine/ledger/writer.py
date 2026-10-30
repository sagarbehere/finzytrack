"""The save's durable order (`design/specs/engine-jobs.md` §6.1 step 8; ADR-0008 steps 5-6, ADR-0012, ADR-0013;
S0060): what a save does after its commit point (§6.1 step 7), through the file-system port only.

0. **Before the first effect** (`_prepare`; every refusal here writes nothing): every path resolved (§6.3) and
   checked to be the file the plan meant: a replaced file a regular file (never a link, a FIFO or a folder), not
   locked (`uchg`), not hard-linked (`handles.hard_links`, the owner's decision point), the same object its
   canonical path names (`st_dev`, `st_ino`); a created path free of any entry (a dangling link included) under an
   existing folder; every folder written in (or a folder made in) on a volume with the swap, allowing entries to be
   added and removed (`access(2)`: write and search, `_RMFILE_OK`), and with no ACL deny entry, whoever it names,
   other than one that new entries do not inherit and that protects only the folder itself (`handles.acl_denial`;
   owner, 2026-10-30: "Allowlist (Recommended)", "Any subject (Recommended)"). The plan as a whole: one
   entry per `path_key`, no target inside another target's path, no target at or under another's temp, staging,
   restore-temp or restore-staging name (`journal.scratch_names`) or kept name and no entry standing at one, the
   folders the save makes once each and never two that differ only in case, no target in the journal or backup
   folder, and neither of those folders inside a folder the save writes in. The journal and backups folders pass
   the same folder rule as a ledger folder (S0061 r7).
1. **Backups:** this save's backup folder `<backups_dir>/<session>-<job>/` made and its parent synced; each file the
   save replaces synced through a handle on its folder (`sync_file`: what an editor wrote into it without a sync is
   then durable, so the old object the swap displaces can never be torn by a power loss: S0061 red team r4 F2), read
   (checked to be the same object again), hashed against the
   planned pre hash (a file not at its pre bytes refuses: nothing was replaced), written as `<n>-<name>` and synced,
   read back and hashed again (ADR-0012 F-1); the backup folder synced.
2. **Journal:** the record (`ledger.core.journal`) written as `<session>-<job>.part` and synced, renamed `.open`, the
   journal folder synced. It names every file by `canonical_path` and `path_key`, resolved at step 1.
3. **Per file, in the plan's order** (the caller orders children first and the root last): each directory the save
   creates made and its parent synced; the path guard (the folder's handle is still at the recorded canonical path,
   checked through the descriptor the write uses, §6.3); the new bytes written under the **staging name**
   `.<name>.ft-<session>-<job>.new` with the file's mode and extended attributes (not its ACL: owner, 2026-10-29) and
   synced, renamed exclusively to the temp name `.<name>.ft-<session>-<job>.tmp`, and that rename made durable by the
   folder's sync (owner, 2026-10-30: "Staging name (Recommended)": the temp name then only ever holds our synced bytes
   or the displaced old object); a **swap** with the file, and what it displaced checked (§6.4: the pre or the post
   bytes are ours; anything else is an outside save, moved at once to its kept name, and the save stops); for a file
   the save creates, an **exclusive rename** of the temp to the path that fails if a file appeared; the folder synced.
   Each folder the save creates is made once, before the first file in it.
4. **Read-back:** every written file equals its post bytes; then each temp, checked to still hold the pre (or post)
   bytes (an outside program may write through a descriptor it held: those bytes go to the kept name, and the save
   stops, §6.4), removed, and the folders synced.
5. **Close:** `.open` renamed `.closed` and the journal folder synced. `Landed` is returned exactly then.

**The step rule.** Every port operation may raise `OSError`, and the process may die at any of them. A failure
before the journal's rename to `.open` is not a rollback (§6.4: nothing was replaced): `NotSaved`, with `unchanged`
true only after every target was hashed again and equals its pre bytes (§11); backups and a `.part` may be left
(recover-all removes a `.part`, §6.1 step 8.2). **Any failure from that rename on rolls back at once** (§6.4,
S0061): the restore of `ledger.restore` on this save's own record, which first applies recovery's checks (the temp
rule included), never touches anything at a created path (whether the save's own file or another program's: owner,
2026-10-30, "Never remove it (Recommended)"; the journal then stops), removes only the folders this save made, and
after a failed close renames a `.closed` record back to `.open` first, so no power loss shows "saved" over restored
files. The outcome then:
- the rollback resolved the journal (or no `.open` record was ever made): `NotSaved`, `open_journal` false;
- it did not (a cause found, an I/O error), before the first replace (the first swap or exclusive rename of a ledger
  file): `NotSaved` with `open_journal` true, which pauses the workspace's writes until recovery resolves it;
- it did not, from the first replace on: `Unfinished` (the journal is `.open`, or `.resolved` when only the
  rollback's last sync failed, or `.closed` when even its reopening failed: never "saved").
A malformed plan raises `ValueError` before the first write. Anything else raised is a bug and propagates.
"""

from __future__ import annotations

import errno
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem, Kind, Right, check_name, fold, split_absolute
from ledger_engine.ledger import restore as _restore
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import DirEntry, FileEntry, Record, State
from ledger_engine.ledger.handles import (
    Displaced,
    Handles,
    PathMovedError,
    displaced,
    folder_blocked,
    hard_links,
    keep,
    sha256,
    volume_without_swap,
)
from ledger_engine.ledger.handles import join as _join

_SHA256: Final = re.compile(r"[0-9a-f]{64}")
BACKUP_MODE: Final = 0o600
JOURNAL_MODE: Final = 0o600
CREATED_MODE: Final = 0o644


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
    PERMISSION = "permission"  # the system would not allow the swap or its clean-up (a mode, an ACL)
    VOLUME_NO_SWAP = "volume_no_swap"  # the volume has no RENAME_SWAP/RENAME_EXCL (`_volume_without_swap`)
    NAME_NOT_UTF8 = "name_not_utf8"  # a path is not valid UTF-8 (the journal cannot name it)


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
    """No ledger file was replaced, or the rollback put every replaced one back (§6.4; the file whose outside save the
    save kept holds that outside save). `unchanged`: every target was hashed again and equals its pre bytes.
    `open_journal`: the journal may have been left `.open` (its rename was asked for and the rollback did not resolve
    it), which pauses writes until recovery resolves it. `path`: the canonical path of the file the refusal is about,
    if any (the message names it: a hard-linked file, a locked one, one that changed). `entry`: the folder a
    `permission` refusal is about, when it is the folder's (its mode, a lock flag or an ACL entry: §11 names it), and
    `detail` the ACL entry that blocks it in `chmod`'s words (`handles.acl_denial`; S0061 r6)."""

    step: Step
    reason: Reason
    unchanged: bool
    open_journal: bool = False
    path: str | None = None
    entry: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Unfinished:
    """The save stopped after a ledger file may have been replaced and its rollback (§6.4) could not resolve the
    journal: the journal is `.open` (or `.resolved` when only the rollback's last sync failed, or `.closed` when the
    close's own sync and then the record's reopening failed: never "saved"), the files are each whole at pre or post,
    or an outside save is kept in the file or in our temp, and recovery must resolve it. `path`: the canonical path of
    the file it stopped at, if known."""

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
    staging: str  # where the new bytes are written first (owner, 2026-10-30: "Staging name (Recommended)")
    scratch: tuple[str, ...]  # every name of ours beside the file (`journal.scratch_names`), never taken by another
    scratch_keys: tuple[str, ...]
    identity: tuple[int, int] | None  # the replaced file's (st_dev, st_ino); None for a created file
    new_dirs: tuple[tuple[str, str], ...]  # (canonical, key) of the folders the save makes for it, parents first
    rest: tuple[str, ...]  # the components that did not exist at resolution
    tag: str = ""  # the record's name `<session>-<job>`, which every name derived from the save carries

    def kept_free(self, h: DirHandle) -> bool:
        """No entry in its folder is one of this file's kept names (a stranger there would stop recovery for ever)."""
        return not any(journal.is_kept_name(n, self.base, self.tag) for n in h.names())


@dataclass(frozen=True, slots=True)
class _Plan:
    targets: tuple[_Target, ...]
    dirs: tuple[tuple[str, str], ...]  # every folder the save makes, once, parents first
    journal_dir: str
    backups_dir: str


class _Stop(Exception):
    def __init__(
        self, reason: Reason, path: str | None = None, entry: str | None = None, detail: str | None = None
    ) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.path = path
        self.entry = entry
        self.detail = detail


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
    if hard_links(st):
        raise _Stop(Reason.HARD_LINKED, canonical)
    return st


def _target(fs: FileSystem, handles: Handles, index: int, c: Commit, ch: Change) -> _Target:
    r = fs.resolve(ch.path)
    canonical, key = core_paths.canonical_path(r), core_paths.path_key(r)
    try:
        canonical.encode()
    except UnicodeEncodeError:
        raise _Stop(Reason.NAME_NOT_UTF8, canonical.encode(errors="backslashreplace").decode()) from None
    parts, kparts = split_absolute(canonical), split_absolute(key)
    if not parts:
        raise BadPlanError("target_is_root")
    base = check_name(parts[-1])
    parent, parent_key = "/" + "/".join(parts[:-1]), "/" + "/".join(kparts[:-1])
    tag = journal.record_name(c.session, c.job)
    scratch = journal.scratch_names(base, tag)
    keys = tuple(_join(parent_key, fold(n, r.rules[-1])) for n in scratch)
    names = (scratch[0], scratch[1], scratch, keys)
    if not r.rest:
        return _Target(index, ch, canonical, key, parent, parent_key, base, *names, r.identity, (), (), tag)
    n = len(r.existing)
    new_dirs = tuple(("/" + "/".join(parts[:i]), "/" + "/".join(kparts[:i])) for i in range(n + 1, len(parts)))
    return _Target(index, ch, canonical, key, parent, parent_key, base, *names, None, new_dirs, r.rest, tag)


def _check_folder(h: DirHandle, canonical: str, folder: str) -> None:
    """The folder `folder` the save writes in (or makes a folder in), for the file `canonical`: on a volume with the
    swap; the save may add entries there and remove them (`deny delete_child` answers yes to write and search: S0061
    red team r6 F1, probed); and no ACL deny entry, whoever it names, blocks it (`handles.acl_denial`: owner,
    2026-10-30, "Allowlist (Recommended)", "Any subject (Recommended)"; what new entries inherit, a folder the save
    makes included, is then never a deny: red team r5 F2, r6 F1-F3, r7 F1)."""
    if volume_without_swap(h):
        raise _Stop(Reason.VOLUME_NO_SWAP, canonical)
    blocked = folder_blocked(h)  # a mode, a lock flag (probed: `uchg`), an ACL entry
    if blocked is not None:
        raise _Stop(Reason.PERMISSION, canonical, folder, blocked.detail)


def _check_app_folder(h: DirHandle, folder: str) -> None:
    """The journal folder or the backups folder, before the save writes anything in it (the backups, the `.part`):
    the same rule as a ledger folder's (`handles.folder_blocked`: write and search, `_RMFILE_OK`, the ACL rule;
    owner, 2026-10-30: "Any subject (Recommended)", whose option checks "the journal and backup folders too"; S0061
    verifier r7 F1, red team r7 F3: an inheritable deny there was inherited by the record or the backups, and every
    recovery failed on them).
    Else `permission`, naming the folder; no ledger file is named."""
    blocked = folder_blocked(h)
    if blocked is not None:
        raise _Stop(Reason.PERMISSION, None, folder, blocked.detail)


def _check_target(handles: Handles, t: _Target) -> None:
    """One target against the disk, before the first effect."""
    handles.on = t.canonical
    if not t.rest:
        if t.change.pre is None:
            raise _Stop(Reason.CHANGED, t.canonical)  # a file the save creates already exists
        h = handles.at(t.parent)
        _check_replaced(h.stat(t.base), t.identity, t.canonical)
        _check_folder(h, t.canonical, t.parent)
        if not h.allowed(t.base, Right.DELETE):
            raise _Stop(Reason.PERMISSION, t.canonical)  # the swap moves it away; an ACL denies that (deny delete)
        if not h.allowed(t.base, Right.READ_XATTRS):
            raise _Stop(Reason.PERMISSION, t.canonical)  # its extended attributes could not be copied to the temp
        if any(h.stat(n) is not None for n in t.scratch) or not t.kept_free(h):
            raise _Stop(Reason.CHANGED, t.canonical)  # an entry stands at our temp name (S0060 red team r2 L2)
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
    _check_folder(holder, t.canonical, existing)
    if len(t.rest) == 1 and (any(holder.stat(n) is not None for n in t.scratch) or not t.kept_free(holder)):
        raise _Stop(Reason.CHANGED, t.canonical)  # an entry stands at our temp name


def _prepare(fs: FileSystem, handles: Handles, c: Commit, targets: tuple[_Target, ...]) -> _Plan:
    """Step 0: check each target and the plan as a whole before the first effect (the module's docstring)."""
    for t in targets:
        _check_target(handles, t)
    handles.on = None
    keys = [t.key for t in targets]
    if len(set(keys)) != len(keys):
        raise BadPlanError("one_file_twice")
    for a in targets:
        for b in targets:
            if a is not b and _within(b.key, a.key):
                raise BadPlanError("file_inside_file")  # a target is a folder another target is in
            if a is not b and (
                any(_within(b.key, k) for k in a.scratch_keys)
                or (b.parent_key == a.parent_key and journal.is_kept_name(b.base, a.base, a.tag))
                or any(  # a folder the save makes at a kept name (S0061 red team r2 L2)
                    dk.rpartition("/")[0] == a.parent_key and journal.is_kept_name(dc.rpartition("/")[2], a.base, a.tag)
                    for dc, dk in b.new_dirs
                )
                or set(b.scratch_keys) & set(a.scratch_keys)
            ):
                raise BadPlanError("temp_name_taken")  # recovery deletes temps (S0060 red team 7, r2 L1); a folder the
                # save makes at a temp name is covered too: a target file is inside it
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
    for folder in (journal_dir, backups_dir):
        _check_app_folder(handles.at(folder), folder)
    return _Plan(targets, tuple((cn, k) for k, cn in dirs.items()), journal_dir, backups_dir)


def _unchanged(handles: Handles, targets: tuple[_Target, ...]) -> bool:
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
    except (OSError, _Stop, PathMovedError):
        return False
    return True


def _backups(handles: Handles, plan: _Plan, name: str) -> dict[str, str]:
    """Step 8.1. Returns the backup name per target key."""
    parent = handles.at(plan.backups_dir)
    parent.mkdir(name)
    parent.sync()
    folder = handles.at(_join(plan.backups_dir, name))
    names: dict[str, str] = {}
    for t in plan.targets:
        if t.change.pre is None:
            continue
        handles.on = t.canonical
        h = handles.at(t.parent)
        _check_replaced(h.stat(t.base), t.identity, t.canonical)
        h.sync_file(t.base)  # the displaced old object durable before the swap (S0061 red team r4 F2)
        data = h.read(t.base)
        if data is None or sha256(data) != t.change.pre:
            raise _Stop(Reason.CHANGED, t.canonical)
        bname = journal.backup_name(t.index, t.base)
        folder.create(bname, data, BACKUP_MODE)
        back = folder.read(bname)
        if back is None or sha256(back) != t.change.pre:
            raise _Stop(Reason.IO_ERROR, t.canonical)  # ADR-0012 F-1
        names[t.key] = bname
    handles.on = None
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
    made: set[str] = field(default_factory=lambda: set())  # keys of the folders made so far (or that may have been)


def _check_displaced(h: DirHandle, name: str, t: _Target) -> None:
    """What the swap moved to our temp name (or what our temp holds at its clean-up), §6.4 as decided on 2026-10-29
    (owner: "Yes" to the kept-name design; ADR-0013 amendment): a file byte-identical to the pre bytes (the backup
    holds them) or to our post bytes is ours; with a second hard link the save stops (the owner's hard-link rule, S0060
    red team r2 M2; nothing is lost, the rollback puts the pre bytes back); anything else is an outside save, moved at
    once to the file's kept name beside it, never swapped back, and the save stops. A read error is raised by
    `displaced`: the save fails and rolls back like any I/O error, with nothing moved to a kept name (S0061 r5)."""
    d = displaced(h, name, (t.change.pre, sha256(t.change.post)))
    if d is Displaced.OURS:
        return
    if d is Displaced.LINKED:
        raise _Stop(Reason.HARD_LINKED, t.canonical)
    if d is Displaced.FOREIGN:
        keep(h, name, t.base, t.tag)  # none free: it stays at the temp name beside a file at post, so it blocks
    raise _Stop(Reason.CHANGED, t.canonical)


def _stage(h: DirHandle, t: _Target, mode: int, like: str | None) -> None:
    """The new bytes at the temp name, by way of the staging name (owner, 2026-10-30: "Staging name (Recommended)"):
    written and synced under the staging name, renamed exclusively to the temp name, and that rename made durable by
    the folder's sync before the swap or the exclusive rename that follows. A cut or torn write is then only ever at
    the staging name, which recovery deletes whatever it holds; and if a power loss drops the swap, whatever an editor
    wrote in place through the file's name meanwhile is in an object with a durable name, our temp's, which the temp
    rule keeps (S0061 verifier r2 F2, red team r2 M1). An entry that appeared at the temp name is never replaced: the
    save stops (`changed`), and the rollback's temp rule keeps it."""
    h.create(t.staging, t.change.post, mode, like=like)
    try:
        h.rename_excl(t.staging, t.temp)
    except FileExistsError:
        raise _Stop(Reason.CHANGED, t.canonical) from None
    h.sync()


def _replace(handles: Handles, t: _Target, progress: _Progress) -> None:
    """Step 8.3 for one file."""
    handles.on = t.canonical
    for canonical, key in t.new_dirs:
        if key in progress.made:
            continue
        parent, _, name = canonical.rpartition("/")
        h = handles.at(parent or "/")
        progress.made.add(key)  # a mkdir that answers an error may still have happened...
        try:
            h.mkdir(name)
        except FileExistsError:
            progress.made.discard(key)  # ...except this one: another program's folder stood there, never removed
            raise _Stop(Reason.CHANGED, canonical) from None
        h.sync()
    h = handles.at(t.parent)
    if t.change.pre is None:
        _stage(h, t, CREATED_MODE, None)
        before = progress.replaced
        progress.replaced = True  # an exclusive rename that answers an error may still have happened...
        try:
            h.rename_excl(t.temp, t.base)
        except FileExistsError:
            progress.replaced = before  # ...except this one: a file stood there, nothing was replaced (the
            # rollback never touches it: anything at a created path is left alone, owner, 2026-10-30)
            raise _Stop(Reason.CHANGED, t.canonical) from None
        h.sync()
        return
    st = _check_replaced(h.stat(t.base), t.identity, t.canonical)
    _stage(h, t, st.mode, t.base)
    if h.stat(t.temp) is None:
        raise _Stop(Reason.CHANGED, t.canonical)  # our temp vanished before the swap
    progress.replaced = True
    h.swap(t.temp, t.base)
    _check_displaced(h, t.temp, t)
    h.sync()


def _read_back(handles: Handles, targets: tuple[_Target, ...]) -> None:
    """Step 8.4: every written file equals its post bytes (ADR-0008 step 6)."""
    for t in targets:
        handles.on = t.canonical
        if handles.at(t.parent).read(t.base) != t.change.post:
            raise _Stop(Reason.READ_BACK, t.canonical)


def _remove_temps(handles: Handles, targets: tuple[_Target, ...]) -> None:
    """Step 8.4: each temp removed only while it holds bytes we hold (the pre bytes, or our post bytes) with one link;
    an outside program may have written into it through a descriptor it held from before the swap (S0060 red team
    finding 2): those bytes are moved at once to the file's kept name, and the save stops (§6.4)."""
    touched: dict[str, DirHandle] = {}
    for t in targets:
        if t.change.pre is None:
            continue
        handles.on = t.canonical
        h = handles.at(t.parent)
        if h.stat(t.temp) is None:
            continue  # gone: nothing of anyone's to remove
        _check_displaced(h, t.temp, t)
        h.remove(t.temp)
        touched[t.parent] = h
    handles.on = None
    for h in touched.values():
        h.sync()


def _why(e: Exception, handles: Handles) -> tuple[Reason, str | None]:
    if isinstance(e, _Stop):
        return e.reason, e.path
    if isinstance(e, PathMovedError):
        return Reason.PATH_MOVED, e.canonical
    return Reason.IO_ERROR, handles.on


def _where(e: Exception) -> tuple[str | None, str | None]:
    """The folder and the ACL entry of a `permission` refusal (`NotSaved.entry`, `detail`)."""
    return (e.entry, e.detail) if isinstance(e, _Stop) else (None, None)


def _rollback(fs: FileSystem, jd: DirHandle, record: Record, plan: _Plan, step: Step, progress: _Progress) -> bool:
    """§6.4: the restore (`ledger.restore.restore`) of this save's own record, at once; True when it resolved the
    journal, or when no `.open` (or `.closed`) record was ever made. Anything at a created path is left as it is (the
    restore then stops, `created_path_taken`); only the folders this save made may be removed; after a failed close
    the record may be `.closed`."""
    try:
        present = jd.stat(journal.file_name(record.name, State.OPEN)) is not None
        present = present or (step is Step.CLOSE and jd.stat(journal.file_name(record.name, State.CLOSED)) is not None)
    except OSError:
        present = True  # cannot tell: try the restore, which fails closed
    if not present:
        return True  # the rename to `.open` did not happen: the `.part` is recover-all's (§6.1 step 8.2)
    out = _restore.restore(
        fs,
        record,
        plan.journal_dir,
        closed=step is Step.CLOSE,
        made=frozenset(progress.made),
    )
    return isinstance(out, _restore.Resolved)


def durable_commit(fs: FileSystem, c: Commit) -> Outcome:
    """§6.1 step 8 for a save past its commit point, with §6.4's rollback (see the module's docstring)."""
    _check_plan(c)
    name = journal.record_name(c.session, c.job)
    handles = Handles(fs)
    targets: tuple[_Target, ...] = ()
    step = Step.BACKUP
    try:
        try:
            targets = tuple(_target(fs, handles, i, c, ch) for i, ch in enumerate(c.changes))
            plan = _prepare(fs, handles, c, targets)
            backups = _backups(handles, plan, name)
            record = _record(c, plan, name, backups)
            data = journal.encode(record)
            step = Step.JOURNAL
            handles.on = None
            jd = handles.at(plan.journal_dir)
            jd.create(journal.file_name(name, State.PART), data, JOURNAL_MODE)
        except (OSError, _Stop, PathMovedError) as e:
            reason, path = _why(e, handles)
            entry, detail = _where(e)
            return NotSaved(step, reason, _unchanged(handles, targets), path=path, entry=entry, detail=detail)
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
        except (OSError, _Stop, PathMovedError) as e:
            reason, path = _why(e, handles)
            if _rollback(fs, jd, record, plan, step, progress):
                return NotSaved(step, reason, _unchanged(handles, plan.targets), path=path)
            if not progress.replaced:
                return NotSaved(step, reason, _unchanged(handles, plan.targets), True, path)
            return Unfinished(step, reason, path)
        return Landed(record)
    finally:
        handles.close()
