"""The save's durable order (`design/specs/engine-jobs.md` §6.1 step 8; ADR-0008 steps 5-6, ADR-0012, ADR-0013;
S0060): what a save does after its commit point (§6.1 step 7), through the file-system port only.

0. **Before the first effect** (`_prepare`; every refusal here writes nothing): every path resolved (§6.3), inside the
   test copy's folder, at its recorded path with its recorded identity, when the plan names one (`Commit.confine`,
   `_check_confined`: `outside_folder`; S0117), and
   checked to be the file the plan meant: a replaced file a regular file (never a link, a FIFO or a folder), not
   locked (`uchg`), not hard-linked (`handles.hard_links`, the owner's decision point), the same object its
   canonical path names (`st_dev`, `st_ino`); a created path free of any entry (a dangling link included) under an
   existing folder; every folder written in (or a folder made in) allowing entries to be added and removed
   (`access(2)`: write and search, `_RMFILE_OK`), and with no ACL deny entry, whoever it names, other than one
   that new entries do not inherit and that protects only the folder itself (`handles.acl_denial`; owner,
   2026-10-30: "Allowlist (Recommended)", "Any subject (Recommended)"), and its **write mode** chosen from its
   volume (`handles.write_mode`: the swap, or the best-effort mode without it; ADR-0068 D2). The plan as a whole: one
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
   Each folder the save creates is made once, before the first file in it. **On a volume without the swap**, the
   best-effort mode (owner, 2026-11-01, ADR-0068 D2; spec §6.8): the new bytes written and synced at the staging
   name, then a **plain rename** of it onto the file (a created file: `RENAME_EXCL` where the volume has it, else a
   plain rename too; `handles.move`), the folder synced. No temp name, no swap, no displaced check and no re-hash
   before the rename: another program's save to the same file in that instant is overwritten (the backup holds the
   bytes before our save), the accepted loss of D2.
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
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final

from ledger_engine.fs.port import (
    DirHandle,
    EntryStat,
    FileSystem,
    FolderMark,
    FoldRule,
    Kind,
    Right,
    check_name,
    fold,
    split_absolute,
)
from ledger_engine.ledger import restore as _restore
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import BadRecordError, DirEntry, FileEntry, FolderEntry, Record, State
from ledger_engine.ledger.handles import (
    Displaced,
    Handles,
    LinkedPathError,
    Mode,
    PathMovedError,
    displaced,
    drive_of,
    folder_blocked,
    folder_verdict,
    hard_links,
    keep,
    known_birth,
    move,
    sha256,
    write_mode,
)
from ledger_engine.ledger.handles import join as _join
from ledger_engine.tla import tla_action

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
    resolved first). The engine build, registry key and lineage id are the closed record's fields (§6.1 step 8.2)."""

    session: str
    job: str
    workspace_key: str
    lineage: str
    engine_build: str
    changes: tuple[Change, ...]
    journal_dir: str
    backups_dir: str
    anchor: Anchor | None = None
    confine: FolderEntry | None = None
    """The test copy's folder as its mark recorded it, path and identity, that every file the save writes or creates
    must lie in once resolved (`_check_confined`), or None."""


@dataclass(frozen=True, slots=True)
class Anchor:
    """Where a save's journal and backups must be (S0102 round 1, red team F1): the app folder of the sidecar's start
    and the journal and backups folders step 8 made in it, each a path and the identity `(st_dev, st_ino)` it had
    then. The writer checks every one by path before the backups, before the `.part` and before the rename to `.open`
    (`anchored`): another folder put in place of any of them stops the save before its first swap,
    `app_folder_missing` naming `app_local_data`. None (the writer called outside a save: tests staging a journal):
    no check."""

    app_local_data: str
    folders: tuple[tuple[str, tuple[int, int]], ...]


def anchored(fs: FileSystem, anchor: Anchor) -> bool:
    """Every folder of `anchor` is at its path with its identity now (links followed, as the writer follows them)."""
    for path, identity in anchor.folders:
        try:
            r = fs.resolve(path)
        except (OSError, ValueError):
            return False
        if r.rest or r.identity != identity:
            return False
    return True


def _check_anchor(fs: FileSystem, anchor: Anchor | None) -> None:
    if anchor is not None and not anchored(fs, anchor):
        raise _Stop(Reason.APP_FOLDER_MISSING, None, anchor.app_local_data)


ENCRYPTED_SUFFIXES: Final = (".gpg", ".asc")
"""ADR-0059: never written. The overlay's candidate rule (`overlay.ENCRYPTED_SUFFIXES`, pinned equal by
`tests/ledger/test_save.py`); here for a path that resolves to such a file only once the save is past its step 0
(S0070 red team r1 F1: a link swapped in after the gate). The writer imports nothing that loads Beancount."""


class Reason(StrEnum):
    IO_ERROR = "io_error"  # an operation failed (a full disk, an I/O error)
    CHANGED = "changed"  # a file is not at its pre bytes, appeared, or an outside save was displaced by a swap
    PATH_MOVED = "path_moved"  # a folder is no longer at its recorded canonical path (§6.3 path guard)
    READ_BACK = "read_back"  # a written file does not hold its post bytes
    NOT_A_FILE = "not_a_file"  # a replaced path is not a regular file (a folder, a FIFO, a device)
    LOCKED = "locked"  # a replaced file is immutable or append-only (`chflags uchg`)
    HARD_LINKED = "hard_linked"  # a replaced file has more than one hard link (`_hard_links`)
    PERMISSION = "permission"  # the system would not allow the swap or its clean-up (a mode, an ACL)
    NAME_NOT_UTF8 = "name_not_utf8"  # a path is not valid UTF-8 (the journal cannot name it)
    LINKED_FOLDER = "linked_folder"  # a link stands in place of the workspace's key, journal or backups folder (the
    # save's folder step, `save._ensure_dir`; S0077, BL-0193): recovery reads no record through it
    NO_FOLDER_ID = "no_folder_id"  # a folder the save writes in has no creation time: the top level of a FAT or
    # exFAT drive, or a drive that keeps none at all; it could not be told from a folder at the same path on another
    # drive (S0089 rounds 1 and 2; `handles.known_birth`)
    NO_DRIVE_ID = "no_drive_id"  # a folder the save writes in is on a drive Finzytrack cannot identify (no reliable
    # volume UUID, or a file-system type name the record cannot hold: `handles.drive_of`): its journal could never be
    # undone (S0089, BL-0188; owner: "one without it is refused the same way"; S0090)
    CASE_TWINS = "case_twins"  # two files of the plan whose paths differ only in case (a case-sensitive volume): their
    # names of ours fold to one under the journal's widest rule (`journal.case_twin`; S0100, BL-0162)
    FOLDER_APPEARED = "folder_appeared"  # a folder stood where the save makes one (`mkdir` answered `EEXIST`): on a
    # network share possibly our own retried `mkdir`, so not "the ledger changed" (S0100, BL-0187)
    APP_FOLDER_MISSING = "app_folder_missing"  # the app's own folder is gone: the sidecar makes it at start, the save
    # never makes it or writes above it (S0100, BL-0163 (a); owner, 2026-11-10: "Sidecar makes it once (Recommended)")
    OUTSIDE_FOLDER = "outside_folder"  # a file of a test copy's save whose path is not under the copy's folder (S0117)
    LINK_IN_COPY = "link_in_copy"  # a link met in a test copy (S0117 round 3; owner, 2026-11-14: "2"): its save
    # follows no link at all
    COPY_FOLDER_MOVED = "copy_folder_moved"  # the test copy's folder is not at its recorded path with its recorded
    # identity: a link at it or above it, or another folder there (S0117 rounds 2 and 3; owner: "3")
    COPY_FOLDER_MISSING = "copy_folder_missing"  # nothing at the test copy's recorded folder path


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
    if any (the message names it: a hard-linked file, a locked one, the file an I/O error happened at). `entry`: the
    folder a `permission` refusal is about, when it is the folder's (its mode, a lock flag or an ACL entry: §11 names
    it), the folder that appeared (`folder_appeared`), and `detail` the ACL entry that blocks it in `chmod`'s words
    (`handles.acl_denial`; S0061 r6). `changed`: the first file the re-hash found not at its pre bytes (never with
    `unchanged`; None also when the re-hash could not read a file), apart from `path` (S0100, BL-0163 (b))."""

    step: Step
    reason: Reason
    unchanged: bool
    open_journal: bool = False
    path: str | None = None
    entry: str | None = None
    detail: str | None = None
    changed: str | None = None


@dataclass(frozen=True, slots=True)
class Unfinished:
    """The save stopped after a ledger file may have been replaced and its rollback (§6.4) could not resolve the
    journal: the journal is `.open` (or `.resolved` when only the rollback's last sync failed, or `.closed` when the
    close's own sync and then the record's reopening failed: never "saved"), the files are each whole at pre or post,
    or an outside save is kept in the file or in our temp, and recovery must resolve it. `path`: the canonical path of
    the file it stopped at, if known. `cause` and `cause_path`: why the rollback did not resolve the journal, and the
    file it stopped at (`restore.Stopped`, `restore.Failed`; None when it was not tried): after `created_path_taken`
    the undo will ask the user about that file, so what the user is told must not promise an undo (S0063; spec §11)."""

    step: Step
    reason: Reason
    path: str | None
    cause: str | None = None
    cause_path: str | None = None


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
    mode: Mode = Mode.SWAP  # its folder's write mode, chosen before the first effect (`_prepare`, `handles.write_mode`)
    seen: FolderEntry | None = None  # its folder (or the folder a folder the save makes is made in) as `_prepare` saw
    # it: drive, file system, creation time (S0089)

    def kept_free(self, h: DirHandle) -> bool:
        """No entry in its folder is one of this file's kept names (a stranger there would stop recovery for ever)."""
        return not any(journal.is_kept_name(n, self.base, self.tag) for n in h.names())


@dataclass(frozen=True, slots=True)
class _Plan:
    targets: tuple[_Target, ...]
    dirs: tuple[tuple[str, str], ...]  # every folder the save makes, once, parents first
    journal_dir: str
    backups_dir: str
    folders: tuple[FolderEntry, ...] = ()  # every folder the undo may write in, as the save saw it (S0089)


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
    if canonical.lower().endswith(ENCRYPTED_SUFFIXES):
        raise _Stop(Reason.CHANGED, ch.path)  # the path leads to an encrypted file since the save's step 0 (a link)
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


def _open_confined(fs: FileSystem, handles: Handles, confine: FolderEntry) -> str:
    """A test copy's folder, opened once and checked (S0117 rounds 2 and 3; owner, 2026-11-14: "3", then "2":
    `design/inbox/answered/20261114-test-copy-folder-identity.md`, `20261114-test-copy-no-links.md`): its handle is
    opened at the recorded path (the path guard refuses a descriptor whose real path is not that path: a link at the
    folder or above it), its `FolderMark` asked through that descriptor must be the recorded identity
    (`handles.folder_verdict`: another drive, file system or creation time), and neither the record nor the folder may
    lack a creation time Finzytrack can rely on (`handles.known_birth`: a FAT or exFAT drive's top level keeps none).
    Its key comes from resolving the path once, accepted only when that names the same object the handle holds. The
    folder's path is returned and set as `handles.confined`: below it nothing is opened by path again.
    Refusals, each naming the folder (none writes anything): missing `copy_folder_missing`; a link at it or above it,
    or another folder at the path, `copy_folder_moved`; not allowed `permission`; a drive with no UUID Finzytrack can
    rely on `no_drive_id`, no creation time `no_folder_id`; a read error `io_error`; the root or a relative path
    `outside_folder`."""
    path = confine.canonical_path
    try:
        if not split_absolute(path):
            raise _Stop(Reason.OUTSIDE_FOLDER, None, path)
    except ValueError:
        raise _Stop(Reason.OUTSIDE_FOLDER, None, path) from None
    try:
        h = handles.at(path)
        mark = h.mark()
        r = fs.resolve(path)
    except (FileNotFoundError, NotADirectoryError):
        raise _Stop(Reason.COPY_FOLDER_MISSING, None, path) from None
    except PathMovedError:
        raise _Stop(Reason.COPY_FOLDER_MOVED, None, path) from None
    except PermissionError:
        raise _Stop(Reason.PERMISSION, None, path) from None
    except OSError:
        raise _Stop(Reason.IO_ERROR, path, path) from None
    if drive_of(mark) is None:
        raise _Stop(Reason.NO_DRIVE_ID, path, path)
    recorded = FolderMark(confine.volume, confine.fs_type, confine.born)
    if known_birth(mark) is None or known_birth(recorded) is None:
        raise _Stop(Reason.NO_FOLDER_ID, path, path)
    if folder_verdict(confine, mark) != "same" or r.rest or r.identity != h.identity():
        raise _Stop(Reason.COPY_FOLDER_MOVED, None, path)
    handles.confined = path
    return core_paths.path_key(r)


def _stored(h: DirHandle, name: str, rule: FoldRule) -> str | None:
    """The stored spelling of the entry `name` in the folder `h` as its volume compares names, or None: none, or more
    than one (fails closed: the walk cannot tell which the plan meant)."""
    names = h.names()
    if name in names:
        return name
    hits = [n for n in names if fold(n, rule) == fold(name, rule)]
    return hits[0] if len(hits) == 1 else None


def _confined_target(handles: Handles, root: str, root_key: str, index: int, c: Commit, ch: Change) -> _Target:
    """A test copy's file, reached without following any link and without looking anything up by path (S0117 round 3;
    owner, 2026-11-14: "2"): its path must be the copy's folder's recorded path followed by names (else
    `outside_folder`); each name is looked up in the handle of the folder above it (`DirHandle.names`, `stat`, which
    follows no link) and each folder is opened from its parent's handle (`Handles.confined`); a link anywhere is
    `link_in_copy`, a file where a folder should be `changed`. Names not there yet are the folders and file the save
    creates. The canonical path is the folder's path and the stored names; the key folds each name by the rule of the
    folder that holds it, asked through that folder's handle."""
    parts, top = split_absolute(ch.path), split_absolute(root)
    if len(parts) <= len(top) or parts[: len(top)] != top:
        raise _Stop(Reason.OUTSIDE_FOLDER, ch.path, root)
    names = parts[len(top) :]
    h = handles.at(root)
    canonical, key, depth = [root], [root_key], 0
    rule = h.rule()
    identity: tuple[int, int] | None = None
    for i, name in enumerate(names):
        rule = h.rule()
        stored = _stored(h, check_name(name), rule)
        st = None if stored is None else h.stat(stored)
        if stored is None or st is None:
            break
        canonical.append(stored)
        key.append(fold(stored, rule))
        depth = i + 1
        if st.kind is Kind.LINK:
            raise _Stop(Reason.LINK_IN_COPY, "/".join(canonical), root)
        if i == len(names) - 1:
            identity = st.identity
        elif st.kind is not Kind.DIR:
            raise _Stop(Reason.CHANGED, "/".join(canonical))
        else:
            h = handles.at("/".join(canonical))
    rest = names[depth:]
    for name in rest:
        canonical.append(name)
        key.append(fold(name, rule))
    full, full_key = "/".join(canonical), "/".join(key)
    if full.lower().endswith(ENCRYPTED_SUFFIXES):
        raise _Stop(Reason.CHANGED, ch.path)
    try:
        full.encode()
    except UnicodeEncodeError:
        raise _Stop(Reason.NAME_NOT_UTF8, full.encode(errors="backslashreplace").decode()) from None
    cparts, kparts = split_absolute(full), split_absolute(full_key)
    base = check_name(cparts[-1])
    parent, parent_key = "/" + "/".join(cparts[:-1]), "/" + "/".join(kparts[:-1])
    tag = journal.record_name(c.session, c.job)
    scratch = journal.scratch_names(base, tag)
    keys = tuple(_join(parent_key, fold(n, rule)) for n in scratch)
    names4 = (scratch[0], scratch[1], scratch, keys)
    if not rest:
        return _Target(index, ch, full, full_key, parent, parent_key, base, *names4, identity, (), (), tag)
    n = len(top) + depth
    new_dirs = tuple(("/" + "/".join(cparts[:i]), "/" + "/".join(kparts[:i])) for i in range(n + 1, len(cparts)))
    return _Target(index, ch, full, full_key, parent, parent_key, base, *names4, None, new_dirs, tuple(rest), tag)


def _check_folder(h: DirHandle, canonical: str, folder: str) -> Mode:
    """The folder `folder` the save writes in (or makes a folder in), for the file `canonical`; its write mode
    (`handles.write_mode`: the swap, or the best-effort mode without it; ADR-0068 D2). The save may add entries there
    and remove them (`deny delete_child` answers yes to write and search: S0061
    red team r6 F1, probed); and no ACL deny entry, whoever it names, blocks it (`handles.acl_denial`: owner,
    2026-10-30, "Allowlist (Recommended)", "Any subject (Recommended)"; what new entries inherit, a folder the save
    makes included, is then never a deny: red team r5 F2, r6 F1-F3, r7 F1)."""
    blocked = folder_blocked(h)  # a mode, a lock flag (probed: `uchg`), an ACL entry
    if blocked is not None:
        raise _Stop(Reason.PERMISSION, canonical, folder, blocked.detail)
    return write_mode(h)


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


def _seen(h: DirHandle, folder: str, canonical: str) -> FolderEntry:
    """The folder `folder` as the journal records it (S0089, BL-0188; owner, 2026-11-08: "Volume ID in journal
    (Recommended)", "Drive ID + folder birth (Recommended)"): its drive's UUID, its file system and its creation time,
    asked through the descriptor the save's writes in it use; the undo writes there only when all three still match.
    A drive Finzytrack cannot identify (`handles.drive_of`: no UUID, a version 4 UUID on FAT, or a file-system type
    name the record cannot hold) refuses the save before its first effect (`no_drive_id`), since its journal could
    never be undone (owner: "one without it is refused the same way"; confirmed for network shares, 2026-11-09). A
    folder with no creation time (a drive that keeps none, or the top level of a FAT or exFAT drive) refuses it too
    (`no_folder_id`, `handles.known_birth`). An error asking raises (`io_error`)."""
    mark = h.mark()
    volume = drive_of(mark)
    if volume is None:
        raise _Stop(Reason.NO_DRIVE_ID, canonical)
    born = known_birth(mark)
    if born is None:
        raise _Stop(Reason.NO_FOLDER_ID, canonical)  # no creation time, or a FAT or exFAT drive's top level (H1)
    entry = FolderEntry(folder, volume, mark.fs_type, born)
    try:
        journal.check_folder(entry)
    except BadRecordError:  # verifier F3: never an exception after the backups. `drive_of` already refuses a type
        # name the record cannot hold; left is a UUID spelled otherwise than the record takes: still no drive ID
        raise _Stop(Reason.NO_DRIVE_ID, canonical) from None
    return entry


def _check_target(handles: Handles, t: _Target) -> tuple[Mode, FolderEntry]:
    """One target against the disk, before the first effect; its folder's write mode and its folder as seen."""
    handles.on = t.canonical
    if not t.rest:
        if t.change.pre is None:
            raise _Stop(Reason.CHANGED, t.canonical)  # a file the save creates already exists
        h = handles.at(t.parent)
        _check_replaced(h.stat(t.base), t.identity, t.canonical)
        mode = _check_folder(h, t.canonical, t.parent)
        if not h.allowed(t.base, Right.DELETE):
            raise _Stop(Reason.PERMISSION, t.canonical)  # the swap moves it away; an ACL denies that (deny delete)
        if not h.allowed(t.base, Right.READ_XATTRS):
            raise _Stop(Reason.PERMISSION, t.canonical)  # its extended attributes could not be copied to the temp
        if any(h.stat(n) is not None for n in t.scratch) or not t.kept_free(h):
            raise _Stop(Reason.CHANGED, t.canonical)  # an entry stands at our temp name (S0060 red team r2 L2)
        return mode, _seen(h, t.parent, t.canonical)
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
    mode = _check_folder(holder, t.canonical, existing)
    if len(t.rest) == 1 and (any(holder.stat(n) is not None for n in t.scratch) or not t.kept_free(holder)):
        raise _Stop(Reason.CHANGED, t.canonical)  # an entry stands at our temp name
    return mode, _seen(holder, existing, t.canonical)  # a folder the save makes is on its parent's drive


def _prepare(fs: FileSystem, handles: Handles, c: Commit, targets: tuple[_Target, ...]) -> _Plan:
    """Step 0: check each target and the plan as a whole before the first effect (the module's docstring)."""
    targets = tuple(replace(t, mode=m, seen=v) for t in targets for m, v in (_check_target(handles, t),))
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
    seen: dict[str, FolderEntry] = {}
    for t in targets:
        assert t.seen is not None  # noqa: S101 (`_check_target` set it)
        seen.setdefault(t.seen.canonical_path, t.seen)
        for canonical, key in t.new_dirs:
            if dirs.setdefault(key, canonical) != canonical:
                raise BadPlanError("created_folders_differ_in_case")
            seen.setdefault(canonical, FolderEntry(canonical, t.seen.volume, t.seen.fs_type, None))  # made later
    journal_dir, journal_key = _app_folder(fs, c.journal_dir)
    backups_dir, backups_key = _app_folder(fs, c.backups_dir)
    for folder in (journal_key, backups_key):
        for t in targets:
            if _within(t.key, folder) or _within(folder, t.parent_key):
                raise BadPlanError("app_folder_and_ledger_folder_overlap")  # INV-27, INV-30
    for folder in (journal_dir, backups_dir):
        _check_app_folder(handles.at(folder), folder)
    folders = tuple(seen[p] for p in sorted(seen))
    return _Plan(targets, tuple((cn, k) for k, cn in dirs.items()), journal_dir, backups_dir, folders)


def _rehash(handles: Handles, targets: tuple[_Target, ...]) -> tuple[bool, str | None]:
    """Every target hashed again: whether each equals its pre bytes (absent for a created file), and the first one
    found not at them (S0100, BL-0163 (b)). A target that cannot be read is neither: the answer is then not unchanged,
    and names a changed file only when another one was read and differs. Targets that could not be resolved say
    (False, None)."""
    if not targets:
        return False, None
    unread = False
    for t in targets:
        try:
            try:
                folder = handles.at(t.parent)
            except FileNotFoundError:
                if t.new_dirs:
                    continue  # its folder was never made: absent, as planned
                raise
            data = folder.read(t.base)
        except (OSError, _Stop, PathMovedError):
            unread = True
            continue
        if (None if data is None else sha256(data)) != t.change.pre:
            return False, t.canonical
    return not unread, None


def _backup_names(plan: _Plan) -> dict[str, str]:
    """The backup name of each file the save replaces, by its key (`journal.backup_name`)."""
    return {t.key: journal.backup_name(t.index, t.base) for t in plan.targets if t.change.pre is not None}


@tla_action("S_Backup", "S_BackupSync")
def _backups(handles: Handles, plan: _Plan, name: str, names: dict[str, str]) -> None:
    """Step 8.1, writing each replaced file's backup under its name in `names`."""
    parent = handles.at(plan.backups_dir)
    parent.mkdir(name)
    parent.sync()
    folder = handles.at(_join(plan.backups_dir, name))
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
        bname = names[t.key]
        folder.create(bname, data, BACKUP_MODE)
        back = folder.read(bname)
        if back is None or sha256(back) != t.change.pre:
            raise _Stop(Reason.IO_ERROR, t.canonical)  # ADR-0012 F-1
    handles.on = None
    folder.sync()


def _record(c: Commit, plan: _Plan, name: str, backups: dict[str, str]) -> Record:
    return Record(
        session=c.session,
        job=c.job,
        workspace_key=c.workspace_key,
        lineage=c.lineage,
        engine_build=c.engine_build,
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
        folders=plan.folders,
    )


@dataclass
class _Progress:
    open_journal: bool = False
    replaced: bool = False  # a ledger file's swap or exclusive rename was asked for, and may have happened
    made: set[str] = field(default_factory=lambda: set())  # keys of the folders made so far (or that may have been)


@tla_action("S_Compare")
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
        keep(h, name, t.base, t.tag, t.mode)  # none free: it stays at the temp name beside a file at post, so it blocks
    raise _Stop(Reason.CHANGED, t.canonical)


@tla_action("S_TempStart", "S_TempDone", "S_TempSync")
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


def _best_effort(h: DirHandle, t: _Target, mode: int, like: str | None, progress: _Progress) -> None:
    """A file on a volume without the swap (ADR-0068 D2, spec §6.8): its new bytes written and synced at the staging
    name, then moved onto its name (`handles.move`: a plain rename; for a created file `RENAME_EXCL` where the volume
    has it, an entry there then a stop, `changed`, never touched), the folder synced."""
    h.create(t.staging, t.change.post, mode, like=like)
    before = progress.replaced
    progress.replaced = True  # a rename that answers an error may still have happened...
    try:
        if t.change.pre is None:
            move(h, t.mode, t.staging, t.base)  # RENAME_EXCL where the volume has it
        else:
            h.rename(t.staging, t.base)  # a plain rename over the file
    except FileExistsError:
        progress.replaced = before  # ...except this one: a file stood at a created path, nothing was replaced
        raise _Stop(Reason.CHANGED, t.canonical) from None
    h.sync()


@tla_action("S_Swap", "S_DirSync")
def _replace(handles: Handles, t: _Target, progress: _Progress) -> None:
    """Step 8.3 for one file: the swap, or on a volume without it the best-effort mode (`t.mode`, ADR-0068 D2)."""
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
            progress.made.discard(key)  # ...except this one: a folder stood there (another program's, or on a network
            # share our own retried `mkdir`), never removed; not "the ledger changed" (S0100, BL-0187)
            raise _Stop(Reason.FOLDER_APPEARED, t.canonical, canonical) from None
        h.sync()
    h = handles.at(t.parent)
    if t.mode is Mode.BEST_EFFORT:
        if t.change.pre is None:
            _best_effort(h, t, CREATED_MODE, None, progress)
            return
        st = _check_replaced(h.stat(t.base), t.identity, t.canonical)
        _best_effort(h, t, st.mode, t.base, progress)
        return
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


@tla_action("S_ReadBack")
def _read_back(handles: Handles, targets: tuple[_Target, ...]) -> None:
    """Step 8.4: every written file equals its post bytes (ADR-0008 step 6)."""
    for t in targets:
        handles.on = t.canonical
        if handles.at(t.parent).read(t.base) != t.change.post:
            raise _Stop(Reason.READ_BACK, t.canonical)


@tla_action("S_RemoveTemps")
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
    if isinstance(e, LinkedPathError):
        return Reason.LINK_IN_COPY, e.canonical
    return Reason.IO_ERROR, handles.on


def _where(e: Exception) -> tuple[str | None, str | None]:
    """The folder and the ACL entry of a `permission` refusal (`NotSaved.entry`, `detail`)."""
    return (e.entry, e.detail) if isinstance(e, _Stop) else (None, None)


def _rollback(
    fs: FileSystem, jd: DirHandle, record: Record, plan: _Plan, step: Step, progress: _Progress
) -> _restore.RestoreOutcome | None:
    """§6.4: the restore (`ledger.restore.restore`) of this save's own record, at once; its outcome, or None when no
    `.open` (or `.closed`) record was ever made (nothing to undo). Anything at a created path is left as it is (the
    restore then stops, `created_path_taken`); only the folders this save made may be removed; after a failed close
    the record may be `.closed`."""
    try:
        present = jd.stat(journal.file_name(record.name, State.OPEN)) is not None
        present = present or (step is Step.CLOSE and jd.stat(journal.file_name(record.name, State.CLOSED)) is not None)
    except OSError:
        present = True  # cannot tell: try the restore, which fails closed
    if not present:
        return None  # the rename to `.open` did not happen: the `.part` is recover-all's (§6.1 step 8.2)
    return _restore.restore(
        fs,
        record,
        plan.journal_dir,
        closed=step is Step.CLOSE,
        made=frozenset(progress.made),
    )


def _encoded(record: Record) -> bytes:
    """The record's bytes, before the first effect (S0100, BL-0162): a plan whose files' names of ours fold to one under
    the journal's widest rule (two paths that differ only in case, on a case-sensitive volume) stops as `case_twins`,
    naming the second file; any other rule of the record's own check raises before anything is written."""
    twin = journal.case_twin(record)
    if twin is not None:
        raise _Stop(Reason.CASE_TWINS, twin.canonical_path)
    return journal.encode(record)


@tla_action("S_JPart", "S_JPartSync", "S_JOpen", "S_JDirSync", "S_Close", "S_CloseSync")
def durable_commit(fs: FileSystem, c: Commit) -> Outcome:
    """§6.1 step 8 for a save past its commit point, with §6.4's rollback (see the module's docstring)."""
    _check_plan(c)
    name = journal.record_name(c.session, c.job)
    handles = Handles(fs)
    targets: tuple[_Target, ...] = ()
    step = Step.BACKUP
    try:
        try:
            if c.confine is None:
                targets = tuple(_target(fs, handles, i, c, ch) for i, ch in enumerate(c.changes))
            else:  # a test copy's save follows no link and looks nothing up by path below its folder (S0117 round 3)
                root, root_key = c.confine.canonical_path, _open_confined(fs, handles, c.confine)
                targets = tuple(_confined_target(handles, root, root_key, i, c, ch) for i, ch in enumerate(c.changes))
            plan = _prepare(fs, handles, c, targets)
            backups = _backup_names(plan)
            record = _record(c, plan, name, backups)
            data = _encoded(record)
            _check_anchor(fs, c.anchor)  # S0102 round 1 (red team F1): the folders of step 8, before each write
            _backups(handles, plan, name, backups)
            step = Step.JOURNAL
            handles.on = None
            _check_anchor(fs, c.anchor)
            jd = handles.at(plan.journal_dir)
            jd.create(journal.file_name(name, State.PART), data, JOURNAL_MODE)
            _check_anchor(fs, c.anchor)  # last, before the record is `.open` and the first swap
        except (OSError, _Stop, PathMovedError, LinkedPathError) as e:
            reason, path = _why(e, handles)
            entry, detail = _where(e)
            unchanged, changed = _rehash(handles, targets)
            return NotSaved(step, reason, unchanged, path=path, entry=entry, detail=detail, changed=changed)
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
            entry, detail = _where(e)
            undo = _rollback(fs, jd, record, plan, step, progress)
            if undo is None or isinstance(undo, _restore.Resolved) or not progress.replaced:
                unchanged, changed = _rehash(handles, plan.targets)
                return NotSaved(
                    step,
                    reason,
                    unchanged,
                    open_journal=not (undo is None or isinstance(undo, _restore.Resolved)),
                    path=path,
                    entry=entry,
                    detail=detail,
                    changed=changed,
                )
            return Unfinished(step, reason, path, undo.cause.value, undo.path)
        return Landed(record)
    finally:
        handles.close()
