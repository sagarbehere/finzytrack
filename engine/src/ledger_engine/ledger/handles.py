"""What the save (`ledger.writer`) and the restore (`ledger.restore`) share: one handle per folder with the path guard,
the write mode per volume (the swap, or the best-effort mode: ADR-0068 D2), and the owner's refusal rules
(`design/specs/engine-jobs.md` §6.3, §6.4, §8.1; ADR-0013 amendments of 2026-10-29 and 2026-10-30; S0060, S0061).
Every write goes through these handles: the check of where a folder is and the write
that follows use one descriptor.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal

from ledger_engine.fs.port import AclEntry, DirHandle, EntryStat, FileSystem, FolderMark, Kind, Right, same_path
from ledger_engine.ledger.core.journal import MAX_KEPT, FolderEntry, kept_name


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def join(parent: str, name: str) -> str:
    return "/" + name if parent == "/" else f"{parent}/{name}"


class PathMovedError(Exception):
    """A folder is no longer at its recorded canonical path (§6.3 path guard)."""

    def __init__(self, canonical: str) -> None:
        super().__init__("path_moved")
        self.canonical = canonical


UUID_UNSTABLE_ON: Final = frozenset({"msdos", "exfat"})
"""File systems whose drive UUID macOS derives from a 32-bit format serial (a version 3 UUID); a drive whose serial
clashes with one already mounted gets a random version 4 UUID instead, which changes at every mount (S0089 red team
M1, probed on FAT): such a UUID names no drive."""
ZONE_STEP: Final = 15 * 60 * 1_000_000_000
ZONE_MAX: Final = 14 * 3600 * 1_000_000_000


def drive_of(mark: FolderMark) -> str | None:
    """The UUID that names `mark`'s drive, or None when it has none Finzytrack can rely on: none reported, or a
    version 4 UUID on FAT or exFAT (`UUID_UNSTABLE_ON`). The one place this rule lives (S0089)."""
    if mark.volume is None:
        return None
    if mark.fs_type in UUID_UNSTABLE_ON and uuid.UUID(mark.volume).version == 4:
        return None
    return mark.volume


def birth_known(mark: FolderMark) -> bool:
    """Whether `mark`'s creation time tells this folder from one at the same path on another drive: not on FAT or
    exFAT when it is 0, which is what those drives answer for their top level (they keep no time for it; probed), and
    what a device that never sets creation times leaves. Two such drives formatted with one serial share their UUID
    (red team H1), so nothing would tell them apart (S0089 round 1). The one place this rule lives."""
    return not (mark.fs_type in UUID_UNSTABLE_ON and mark.born == 0)


def same_birth(recorded: int, now: int, fs_type: str) -> bool:
    """A folder's creation time now is the recorded one. FAT keeps local time without a zone, so macOS reads it in the
    Mac's time zone of the moment: on FAT (`msdos`) a shift by a whole time zone (a multiple of 15 minutes, at most 14
    hours) is the same time (S0089 round 1; the Mac's zone may change between the save and recovery: travel)."""
    if fs_type != "msdos":
        return now == recorded
    d = now - recorded
    return d % ZONE_STEP == 0 and abs(d) <= ZONE_MAX


type FolderVerdict = Literal["same", "wrong_drive", "unknown_drive"]


def folder_verdict(recorded: FolderEntry, mark: FolderMark) -> FolderVerdict:
    """Whether the folder found at a recorded path is the one the save wrote in (owner, 2026-11-08: "Volume ID in
    journal (Recommended)", then "Drive ID + folder birth (Recommended)"): a drive Finzytrack cannot identify is
    `unknown_drive`; another drive UUID, another file-system type, or another creation time (for a folder that was there
    before the save) is `wrong_drive`. The one place this rule lives."""
    found = drive_of(mark)
    if found is None:
        return "unknown_drive"
    if found != recorded.volume or mark.fs_type != recorded.fs_type:
        return "wrong_drive"
    if recorded.born is not None and not same_birth(recorded.born, mark.born, mark.fs_type):
        return "wrong_drive"
    return "same"


class WrongDriveError(Exception):
    """A folder the undo would write in is not the folder the journal names: another drive (or the same drive UUID
    with another file system or another folder creation time) is `wrong_drive`, a drive with no reliable UUID
    `unknown_drive` (S0089, BL-0188). Nothing was written through it."""

    def __init__(self, canonical: str, verdict: FolderVerdict) -> None:
        super().__init__(verdict)
        self.canonical = canonical
        self.verdict = verdict


@dataclass
class Handles:
    """One handle per folder, opened once and checked at every use (§6.3: the check and the write go through one
    descriptor). `at` raises `PathMovedError` when the folder's path now does not name the recorded canonical path,
    compared as the volume compares names (`fs.port.same_path`).

    **The drive** (S0089, BL-0188; owner, 2026-11-08: "Volume ID in journal (Recommended)", then "Drive ID + folder
    birth (Recommended)"): with `folders` (the journal's `journal.folders`, by canonical path), a folder named there is
    checked when its handle is opened, after the path guard (so a link in its place is `path_moved`, S0089 red team L1):
    its `FolderMark`, asked through the same descriptor every later write uses (a descriptor never changes drive), must
    be the recorded folder's (`folder_verdict`), else `WrongDriveError` and the handle is not kept; a mark that cannot
    be read raises the `OSError`. Folders not named there (the app's journal and backups folders) are not checked."""

    fs: FileSystem
    open: dict[str, DirHandle] = field(default_factory=lambda: {})
    on: str | None = None  # the canonical path of the file being worked on, for the outcome
    entry: str | None = None  # the entry being checked or written when it is not that file (a name of ours, the
    # backup, a folder), for the outcome (S0061 verifier r6 F3)
    folders: Mapping[str, FolderEntry] = field(default_factory=lambda: {})

    def at(self, canonical: str) -> DirHandle:
        h = self.open.get(canonical)
        if h is None:
            h = self.fs.open_dir(canonical)
            try:
                self._check_new(h, canonical)
            except BaseException:
                h.close()
                raise
            self.open[canonical] = h
            return h
        if not same_path(h.path(), canonical):
            raise PathMovedError(canonical)
        return h

    def _check_new(self, h: DirHandle, canonical: str) -> None:
        """A handle just opened: the path guard first, then, for a recorded folder, `folder_verdict`."""
        if not same_path(h.path(), canonical):
            raise PathMovedError(canonical)
        recorded = self.folders.get(canonical)
        if recorded is not None:
            verdict = folder_verdict(recorded, h.mark())
            if verdict != "same":
                raise WrongDriveError(canonical, verdict)

    def release(self, canonical: str) -> None:
        """Close the handle of `canonical` if one is open (a folder the caller is done with; the next `at` opens and
        checks it again). Keeps the descriptors of a walk over many folders bounded (S0074 verifier F3)."""
        h = self.open.pop(canonical, None)
        if h is not None:
            h.close()

    def close(self) -> None:
        for h in self.open.values():
            h.close()
        self.open.clear()


def hard_links(st: EntryStat) -> bool:
    """A ledger file with more than one hard link is never swapped: the save refuses it before anything is written
    (owner, 2026-10-29: "Refuse to save it (Recommended)"; ADR-0013 amendment of 2026-10-29), and a restore stops on
    it (the swap would give the name the pre bytes and leave the other link at post, unseen). Reads are not
    affected. The one place this rule lives."""
    return st.nlink > 1


class Mode(StrEnum):
    """How the save and the undo write in one folder, chosen from its volume before any effect (`write_mode`;
    ADR-0013, ADR-0068 D2)."""

    SWAP = "swap"  # RENAME_SWAP and RENAME_EXCL (APFS): the atomic swap, ADR-0013 as decided
    BEST_EFFORT = "best_effort"  # without them (exFAT, FAT, HFS+, network shares): a plain rename (spec §6.8)


def write_mode(h: DirHandle) -> Mode:
    """The write path for the folder `h` (spec §6.1 step 8, §6.8): `SWAP` only where the volume offers both the swap
    and the exclusive rename; otherwise the best-effort mode (owner, 2026-11-01, ADR-0068 D2). The one place this rule
    lives."""
    c = h.caps()
    return Mode.SWAP if c.swap and c.excl else Mode.BEST_EFFORT


def move(h: DirHandle, mode: Mode, src: str, dst: str) -> None:
    """Rename `src` to `dst`: `RENAME_EXCL` (`FileExistsError` when an entry stands at `dst`, `dst` untouched) on
    `SWAP`, and on `BEST_EFFORT` where the volume has it (HFS+); else a plain rename, which replaces an entry another
    program put at `dst` in that instant (ADR-0068 D2's accepted loss, spec §6.8)."""
    if mode is Mode.SWAP or h.caps().excl:
        h.rename_excl(src, dst)
    else:
        h.rename(src, dst)


FOLDER_SELF: Final = frozenset({"delete", "writeattr", "writeextattr", "writesecurity", "chown"})
"""What a deny entry may deny on a folder the save writes in: only what protects the folder itself (its own
deletion, its attributes, its ACL, its owner), never anything about the entries in it (S0061 r6)."""
NOT_INHERITED: Final = frozenset({"inherited", "limit_inherit"})
"""The flags a deny entry may carry there: none that passes it on to new entries (`file_inherit`,
`directory_inherit`, `only_inherit`, or a flag the adapter cannot name)."""


UNREADABLE_ACL: Final = "an ACL we may not read"


def blocking_entry(entries: tuple[AclEntry, ...]) -> AclEntry | None:
    """The first ACL entry that refuses a folder the save or the undo writes in, or makes or removes a folder in
    (owner, 2026-10-30: "Allowlist (Recommended)", then "Any subject (Recommended)"; ADR-0013 amendments of that
    date): **any deny entry, whoever it names** (a user, a group, everyone, macOS's `owner` and `group` subjects,
    another user, a subject the system cannot resolve), unless new entries do not inherit it and it denies only what
    protects the folder itself (`FOLDER_SELF`: macOS puts `group:everyone deny delete` and `deny writeextattr` on the
    home folder, Desktop, Documents and Downloads). An inheritable one of any bit and an unknown bit or flag included;
    allow entries are not weighed (an allow before a deny still refuses: fails closed). Whom an entry names is never
    asked: S0061 r7 found the membership lookup answering "not a member" for subjects the kernel applies to us. The
    one place this rule lives."""
    for e in entries:
        if e.deny and (not e.flags <= NOT_INHERITED or not e.perms <= FOLDER_SELF):
            return e
    return None


def acl_denial(h: DirHandle) -> str | None:
    """`blocking_entry` of the folder `h`, told in `chmod`'s words without its subject (`deny
    delete_child,directory_inherit`), for the `permission` refusal's text (§11); None when nothing blocks it. An ACL
    the system will not let us read (`EACCES`: a `deny readsecurity` or `deny readattr` on the folder itself, probed
    on APFS) cannot be judged, so it blocks too (fails closed)."""
    try:
        entries = h.acl()
    except PermissionError:
        return UNREADABLE_ACL
    e = blocking_entry(entries)
    if e is None:
        return None
    return "deny " + ",".join([*sorted(e.perms), *sorted(e.flags - {"inherited"})])


@dataclass(frozen=True, slots=True)
class Blocked:
    """A folder the save or the undo may not write in (`folder_blocked`); `detail`: the ACL entry that blocks it, in
    `chmod`'s words, when an entry does (None: the system's own answer, a mode, a lock flag, an ACL the access check
    met first)."""

    detail: str | None


def folder_blocked(h: DirHandle) -> Blocked | None:
    """The folder rule for a folder the save writes in or makes a folder in, and for the journal and backups folders
    (S0061 r6, r7): entries may be added and removed there (`access(2)`: write and search, `_RMFILE_OK`), and no ACL
    entry blocks it (`acl_denial`, whoever it names). None when it may be written. The one place this rule lives."""
    if not h.allowed(".", Right.CHANGE_ENTRIES) or not h.allowed(".", Right.REMOVE_ENTRIES):
        return Blocked(None)
    detail = acl_denial(h)
    return None if detail is None else Blocked(detail)


class Displaced(StrEnum):
    """What a swap or a temp's clean-up found at a name of ours (§6.4)."""

    OURS = "ours"  # a regular file with one link, byte-identical to bytes we hold (deleting it loses nothing)
    LINKED = "linked"  # the same, but with a second hard link (the owner's hard-link rule: the save stops)
    FOREIGN = "foreign"  # anything else: an outside save, moved to a kept name, never deleted
    GONE = "gone"  # nothing there any more


def displaced(h: DirHandle, name: str, ours: tuple[str | None, ...]) -> Displaced:
    """Classify the entry `name` by its bytes (owner, 2026-10-29, the kept-name design, step 2: "If it is ours, or
    byte-identical to something we already hold (the pre bytes, which the backup has; or the post bytes we wrote),
    treat it as ours (deleting it loses nothing)"). `ours`: the SHA-256 of the bytes we hold. Anything that is not a
    regular file (by its `stat`) is foreign. A regular file whose bytes cannot be read is neither ours nor foreign:
    the read's `OSError` is raised, so the caller fails with nothing moved and the next try reads it again (owner,
    2026-10-30: "Yes to both (Recommended)"; S0061 r5, red team F3, verifier F3: it was taken for an outside save, and
    one transient error made a kept copy of our own bytes that stopped every later try)."""
    st = h.stat(name)
    if st is None:
        return Displaced.GONE
    if st.kind is not Kind.FILE:
        return Displaced.FOREIGN
    data = h.read(name)  # an OSError here is raised, never taken for an outside save
    if data is None:
        return Displaced.GONE
    if sha256(data) not in ours:
        return Displaced.FOREIGN
    return Displaced.LINKED if hard_links(st) else Displaced.OURS


def keep(h: DirHandle, name: str, base: str, job: str, mode: Mode) -> str | None:
    """Move the entry `name` (an outside save we met) at once to the first free kept name of the file `base`
    (`journal.kept_name`, exclusive), then sync the folder: nothing of ours ever deletes it, and the journal stops
    while it exists. None when every kept name is taken: the entry stays where it is, and the journal stops anyway
    (those kept names exist). `mode`: the folder's write mode (`move`). Owner, 2026-10-29: "Yes" (ADR-0013
    amendment). Where the volume has no exclusive rename (exFAT, FAT, network shares) the free name is chosen by a
    check, then the plain rename: else a second outside save kept in the same try would replace the first (S0071 red
    team F1); another program writing that kept name in the same instant is ADR-0068 D2's accepted loss."""
    checked = mode is Mode.BEST_EFFORT and not h.caps().excl
    for n in range(1, MAX_KEPT + 1):
        if checked and h.stat(kept_name(base, job, n)) is not None:
            continue
        try:
            move(h, mode, name, kept_name(base, job, n))
        except FileExistsError:
            continue
        h.sync()
        return kept_name(base, job, n)
    return None
