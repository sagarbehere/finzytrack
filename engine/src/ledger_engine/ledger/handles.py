"""What the save (`ledger.writer`) and the restore (`ledger.restore`) share: one handle per folder with the path guard,
and the owner's two refusal rules (`design/specs/engine-jobs.md` §6.3, §6.4, §8.1; ADR-0013 amendment of 2026-10-29;
S0060, S0061). Every write goes through these handles: the check of where a folder is and the write that follows use
one descriptor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem, same_path


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
