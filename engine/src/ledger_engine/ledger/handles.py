"""What the save (`ledger.writer`) and the restore (`ledger.restore`) share: one handle per folder with the path guard,
and the owner's two refusal rules (`design/specs/engine-jobs.md` §6.3, §6.4, §8.1; ADR-0013 amendment of 2026-10-29;
S0060, S0061). Every write goes through these handles: the check of where a folder is and the write that follows use
one descriptor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem, Kind, same_path
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
