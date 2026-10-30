"""The per-file judgement of recovery and the rollback (`design/specs/engine-jobs.md` §8.1 step 2, §6.4 "The save's
temp paths in recovery"; §12: "the recovery check per file" is one of the engine's pure decisions; ADR-0012's per-file
rule as amended by ADR-0064, and the temp rule as decided on 2026-10-30; S0061). Pure.

**The temp rule** (owner, 2026-10-30: "Staging name (Recommended)"; it replaced the refined rule of 2026-10-29,
"empty, a prefix of what we wrote, or equal to the old or new bytes is ours"). Every write of ours goes first to a
**staging name** (`journal.staging_name`, `journal.restore_staging_name`), is synced, and only then renamed
exclusively to the temp name, the rename synced before any swap. So:
- a staging name is touched by no swap: a regular file there is our own write, cut or torn in any shape, and is
  **ours** whatever bytes it holds (`classify_staging`); anything else there is not ours;
- a temp name (the save's temp, the restore temp) only ever holds our fully synced bytes or the old object a swap
  displaced, into which a program holding it open may have written: it is **ours** only when equal to the pre or the
  post bytes (`classify_temp`).
Anything not ours (other bytes, a folder, a link, a FIFO) is an outside save, moved to its kept name, and the journal
stops, whatever the file beside it holds. A regular file at a temp name whose bytes could not be read is neither
(`unreadable`): the try fails with nothing written, and the next one reads it again (S0061 red team r4 O1: a transient
read error made a kept copy of our own bytes). These two functions are the rule's one place.

The file the temps sit beside, then:
- at its pre bytes (a created file: absent): **at pre**;
- at its post bytes: **at post** when, for a replaced file, the backup holds the pre bytes (ADR-0012 F-1), else stop;
  for a created file, only while none of its four names of ours is there (the **created-path proof**): the save's
  temp or staging file means the exclusive rename never happened (S0061 red team r2 H1), a restore temp means a try
  already renamed our file aside (red team r4 F1), and a restore staging file is never written beside a created file;
  so the file at the path is another program's, whatever it holds. The restore keeps those names until the journal is
  durably resolved (`ledger.restore`);
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
    UNREADABLE = "unreadable"  # a regular file whose bytes could not be read: the try fails, nothing written


def classify_temp(kind: Kind | None, data: bytes | None, held: tuple[str | None, ...]) -> TempState:
    """The temp rule at a temp name (the module's docstring). `kind`/`data`: what stands there (`data` for a regular
    file, None when it could not be read). `held`: the SHA-256 of the bytes we hold (pre and post)."""
    if kind is None:
        return TempState.ABSENT
    if kind is not Kind.FILE:
        return TempState.FOREIGN
    if data is None:
        return TempState.UNREADABLE
    return TempState.OURS if hashlib.sha256(data).hexdigest() in held else TempState.FOREIGN


def classify_staging(kind: Kind | None) -> TempState:
    """The temp rule at a staging name (the module's docstring): a regular file there is ours whatever it holds (its
    bytes are never read, so it is never `unreadable`)."""
    if kind is None:
        return TempState.ABSENT
    return TempState.OURS if kind is Kind.FILE else TempState.FOREIGN


@dataclass(frozen=True, slots=True)
class Temps:
    """What the temp rule found at each name of ours beside one file."""

    temp: TempState = TempState.ABSENT  # the save's temp
    staging: TempState = TempState.ABSENT  # the save's staging name
    rtemp: TempState = TempState.ABSENT  # the restore temp
    rstaging: TempState = TempState.ABSENT  # the restore's staging name

    def states(self) -> tuple[TempState, ...]:
        return self.temp, self.staging, self.rtemp, self.rstaging


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


def judge(entry: FileEntry, file: Seen, temps: Temps, backup: Seen | None, *, skip: bool = False) -> Verdict | Stop:
    """The verdict for one file (the module's docstring). `temps`: the names of ours beside it, by the temp rule.
    `backup`: what the backup holds, None when not read. `Stop(io_error)`: a name of ours could not be read, which the
    restore tells as `Failed` and writes nothing for."""
    if TempState.UNREADABLE in temps.states():
        return Stop(Cause.IO_ERROR)
    if TempState.FOREIGN in temps.states():
        return Stop(Cause.OUTSIDE_SAVE_IN_TEMP)
    if skip:
        return Verdict.KEEP
    post = Seen(Kind.FILE, entry.post)
    pre = ABSENT if entry.pre is None else Seen(Kind.FILE, entry.pre)
    if file == pre:
        return Verdict.AT_PRE
    if file == post:
        if entry.pre is None and any(t is not TempState.ABSENT for t in temps.states()):
            return Stop(Cause.FOREIGN)  # the created-path proof: the file is another program's
        if entry.pre is not None and backup != pre:
            return Stop(Cause.BACKUP_BAD)
        return Verdict.AT_POST
    return Stop(Cause.FOREIGN)
