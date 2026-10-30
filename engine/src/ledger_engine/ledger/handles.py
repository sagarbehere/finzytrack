"""What the save (`ledger.writer`) and the restore (`ledger.restore`) share: one handle per folder with the path guard,
and the owner's refusal rules (`design/specs/engine-jobs.md` §6.3, §6.4, §8.1; ADR-0013 amendments of 2026-10-29
and 2026-10-30; S0060, S0061). Every write goes through these handles: the check of where a folder is and the write
that follows use one descriptor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ledger_engine.fs.port import AclEntry, DirHandle, EntryStat, FileSystem, Kind, Right, same_path
from ledger_engine.ledger.core.journal import MAX_KEPT, kept_name


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def join(parent: str, name: str) -> str:
    return "/" + name if parent == "/" else f"{parent}/{name}"


class PathMovedError(Exception):
    """A folder is no longer at its recorded canonical path (§6.3 path guard)."""

    def __init__(self, canonical: str) -> None:
        super().__init__("path_moved")
        self.canonical = canonical


@dataclass
class Handles:
    """One handle per folder, opened once and checked at every use (§6.3: the check and the write go through one
    descriptor). `at` raises `PathMovedError` when the folder's path now does not name the recorded canonical path,
    compared as the volume compares names (`fs.port.same_path`)."""

    fs: FileSystem
    open: dict[str, DirHandle] = field(default_factory=lambda: {})
    on: str | None = None  # the canonical path of the file being worked on, for the outcome
    entry: str | None = None  # the entry being checked or written when it is not that file (a name of ours, the
    # backup, a folder), for the outcome (S0061 verifier r6 F3)

    def at(self, canonical: str) -> DirHandle:
        h = self.open.get(canonical)
        if h is None:
            h = self.fs.open_dir(canonical)
            self.open[canonical] = h
        if not same_path(h.path(), canonical):
            raise PathMovedError(canonical)
        return h

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


def volume_without_swap(h: DirHandle) -> bool:
    """A folder on a volume without `RENAME_SWAP` and `RENAME_EXCL` (exFAT, FAT, HFS+, some network shares) is never
    written: a save refuses before anything is written. Owner, 2026-10-29: "Unsafe fallback on such drives", then
    "Agreed, fallback as its own slice after slice 4" (ADR-0013 amendment of 2026-10-29). The one place this rule
    lives. TODO(BL-0160): the fallback write path for such volumes replaces this refusal."""
    return not h.can_swap()


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


def keep(h: DirHandle, name: str, base: str, job: str) -> str | None:
    """Move the entry `name` (an outside save we met) at once to the first free kept name of the file `base`
    (`journal.kept_name`, exclusive), then sync the folder: nothing of ours ever deletes it, and the journal stops
    while it exists. None when every kept name is taken: the entry stays where it is, and the journal stops anyway
    (those kept names exist). Owner, 2026-10-29: "Yes" (ADR-0013 amendment)."""
    for n in range(1, MAX_KEPT + 1):
        try:
            h.rename_excl(name, kept_name(base, job, n))
        except FileExistsError:
            continue
        h.sync()
        return kept_name(base, job, n)
    return None
