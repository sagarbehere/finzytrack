"""The per-file judgement of recovery and the rollback (`design/specs/engine-jobs.md` §8.1 step 2, §6.4 "The save's
temp paths in recovery"; §12: "the recovery check per file" is one of the engine's pure decisions; ADR-0012's per-file
rule as amended by ADR-0064, and the temp rule as refined on 2026-10-29; S0061). Pure.

**The temp rule** (owner, 2026-10-29: "Refine the rule"; it replaced review 6 P1's "otherwise recovery deletes it,
whatever it holds"): a save temp, or a restore temp, is **ours** only when it is empty, a prefix of the bytes being
written into it (the post bytes for a save temp, the pre bytes for a restore temp: a write cut off at any point), or
equal to the pre or the post bytes; anything else (other bytes, a folder, a link, a FIFO) is an outside save, moved to
its kept name, and the journal stops, whatever the file beside it holds. `classify_temp` is that rule's one place.

The file the temp sits beside, then:
- at its pre bytes (a created file: absent): **at pre**;
- at its post bytes: **at post** when, for a replaced file, the backup holds the pre bytes (ADR-0012 F-1), else stop;
  for a created file, only while the save's temp is gone: a temp still there means the exclusive rename never
  happened, so the file at the path is another program's, whatever it holds (S0061 red team r2 H1);
- anything else (other bytes, a folder, a link, a FIFO): stop.
The rollback's skipped file (`skip`: a created path an outside file took before the save's exclusive rename, §6.4) is
**kept** whatever it holds.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from ledger_engine.fs.port import Kind
from ledger_engine.ledger.core.journal import FileEntry


@dataclass(frozen=True, slots=True)
class Seen:
    """What stood at a name: its kind (None: no entry) and, for a regular file, the SHA-256 of its bytes."""

    kind: Kind | None
    sha: str | None = None


ABSENT = Seen(None)


def seen_file(data: bytes) -> Seen:
    return Seen(Kind.FILE, hashlib.sha256(data).hexdigest())


class TempState(StrEnum):
    ABSENT = "absent"
    OURS = "ours"  # deleting it loses nothing
    FOREIGN = "foreign"  # moved to its kept name, never deleted


def classify_temp(
    kind: Kind | None, data: bytes | None, held: tuple[str | None, ...], cut_of: bytes | None
) -> TempState:
    """The temp rule (the module's docstring). `kind`/`data`: what stands at the temp name (`data` for a regular
    file). `held`: the SHA-256 of the bytes we hold (pre and post). `cut_of`: the bytes a cut write would be a prefix
    of, None when they are not known (then only empty and equal-to-held count as ours)."""
    if kind is None:
        return TempState.ABSENT
    if kind is not Kind.FILE or data is None:
        return TempState.FOREIGN
    if not data or hashlib.sha256(data).hexdigest() in held:
        return TempState.OURS
    if cut_of is not None and cut_of.startswith(data):
        return TempState.OURS
    return TempState.FOREIGN


class Verdict(StrEnum):
    AT_PRE = "at_pre"  # nothing to restore
    AT_POST = "at_post"  # restore it from its backup, or remove it if the save created it
    KEEP = "keep"  # the rollback's skipped file (an outside file took a created path first): left as it is


class Cause(StrEnum):
    """Why a journal stops (`Stopped`, blocking reason `stopped`) or a try fails (`Failed`, reason `failed`)."""

    FOREIGN = "foreign"  # a file holds neither its pre nor its post bytes, is not a regular file, or is not ours
    OUTSIDE_SAVE_IN_TEMP = "outside_save_in_temp"  # a temp holds outside bytes (§6.4): moved to its kept name
    BACKUP_BAD = "backup_bad"  # the backup a restore needs does not hold the pre bytes (ADR-0012 F-1)
    PATH_MOVED = "path_moved"  # a folder no longer resolves to its recorded canonical path (§6.3)
    UNREACHABLE = "unreachable"  # a folder the journal names is not there (a moved folder, an unmounted volume)
    HARD_LINKED = "hard_linked"  # a file to restore has a second hard link (ADR-0013 amendment of 2026-10-29)
    LOCKED = "locked"  # a file to restore has a lock flag (`uchg`)
    PERMISSION = "permission"  # the system would not allow the restore swap or the removal (a mode, an ACL)
    VOLUME_NO_SWAP = "volume_no_swap"  # the folder's volume has no RENAME_SWAP/RENAME_EXCL
    DISPLACED = "displaced"  # an outside save met at a restore swap, a removal or a temp check: moved to a kept name
    KEPT_COPY = "kept_copy"  # a kept copy of this journal's (an outside save we moved aside) is still there
    UNREADABLE = "unreadable"  # the record cannot be read: torn, foreign, newer, or not this name's
    LOCK_HELD = "lock_held"  # a ledger-set lock is held by another process (`Failed`)
    IO_ERROR = "io_error"  # an operation failed while restoring (`Failed`)


@dataclass(frozen=True, slots=True)
class Stop:
    cause: Cause


def judge(
    entry: FileEntry,
    file: Seen,
    temp: TempState,
    rtemp: TempState,
    backup: Seen | None,
    *,
    skip: bool = False,
) -> Verdict | Stop:
    """The verdict for one file (the module's docstring). `temp`, `rtemp`: the save temp and the restore temp beside
    it, by `classify_temp`. `backup`: what the backup holds, None when not read."""
    if TempState.FOREIGN in (temp, rtemp):
        return Stop(Cause.OUTSIDE_SAVE_IN_TEMP)
    if skip:
        return Verdict.KEEP
    post = Seen(Kind.FILE, entry.post)
    pre = ABSENT if entry.pre is None else Seen(Kind.FILE, entry.pre)
    if file == pre:
        return Verdict.AT_PRE
    if file == post:
        if entry.pre is None and temp is not TempState.ABSENT:
            return Stop(Cause.FOREIGN)  # the exclusive rename never happened: the file is another program's
        if entry.pre is not None and backup != pre:
            return Stop(Cause.BACKUP_BAD)
        return Verdict.AT_POST
    return Stop(Cause.FOREIGN)
