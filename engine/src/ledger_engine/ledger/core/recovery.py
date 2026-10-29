"""The per-file judgement of recovery and the rollback (`design/specs/engine-jobs.md` §8.1 step 2, §6.4 "The save's
temp paths in recovery" (review 6 P1); §12: "the recovery check per file" is one of the engine's pure decisions;
ADR-0012's per-file rule as amended by ADR-0064; S0061). Pure.

From what was seen at a file the journal names, at the save's temp beside it and at its backup:
- the file at its pre bytes (a created file: absent): **at pre**, whatever the temp holds, if the temp is a regular
  file or absent (a temp cut off at any point while it was written is ours and blocks nothing);
- the file at its post bytes: **at post** when the temp is absent or holds the pre or the post bytes and, for a
  replaced file, the backup holds the pre bytes; a temp with other bytes is a displaced outside save and stops the
  journal; a backup not at pre stops it (ADR-0012 F-1);
- anything else (other bytes, a folder, a link, a FIFO) stops it.
The rollback's kept file (`skip`: the outside save the save itself swapped back, §6.4) is **kept** whatever it holds,
its temp only when it is absent or holds our post bytes (anything else there is a second outside save).
This module is the one place the temp rule lives.
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


class Verdict(StrEnum):
    AT_PRE = "at_pre"  # nothing to restore
    AT_POST = "at_post"  # restore it from its backup, or remove it if the save created it
    KEEP = "keep"  # the rollback's kept file: left as it is


class Cause(StrEnum):
    """Why a journal stops (`Stopped`, blocking reason `stopped`) or a try fails (`Failed`, reason `failed`)."""

    FOREIGN = "foreign"  # a file holds neither its pre nor its post bytes, or is not a regular file
    OUTSIDE_SAVE_IN_TEMP = "outside_save_in_temp"  # a temp beside a file at post holds other bytes (§6.4)
    BACKUP_BAD = "backup_bad"  # the backup a restore needs does not hold the pre bytes (ADR-0012 F-1)
    PATH_MOVED = "path_moved"  # a folder no longer resolves to its recorded canonical path (§6.3)
    UNREACHABLE = "unreachable"  # a folder the journal names is not there (a moved folder, an unmounted volume)
    HARD_LINKED = "hard_linked"  # a file to restore has a second hard link (ADR-0013 amendment of 2026-10-29)
    LOCKED = "locked"  # a file to restore has a lock flag (`uchg`)
    PERMISSION = "permission"  # the system would not allow the restore swap or the removal (a mode, an ACL)
    VOLUME_NO_SWAP = "volume_no_swap"  # the folder's volume has no RENAME_SWAP/RENAME_EXCL
    DISPLACED = "displaced"  # an outside write met at a restore swap or a created file's removal, put back
    UNREADABLE = "unreadable"  # the record cannot be read: torn, foreign, newer, or not this name's
    LOCK_HELD = "lock_held"  # a ledger-set lock is held by another process (`Failed`)
    IO_ERROR = "io_error"  # an operation failed while restoring (`Failed`)


@dataclass(frozen=True, slots=True)
class Stop:
    cause: Cause


def judge(entry: FileEntry, file: Seen, temp: Seen, backup: Seen | None, *, skip: bool = False) -> Verdict | Stop:
    """The verdict for one file (the module's docstring). `backup` is what the backup holds, None when not read."""
    post = Seen(Kind.FILE, entry.post)
    if skip:
        return Verdict.KEEP if temp in (ABSENT, post) else Stop(Cause.OUTSIDE_SAVE_IN_TEMP)
    pre = ABSENT if entry.pre is None else Seen(Kind.FILE, entry.pre)
    regular_or_none = temp.kind in (None, Kind.FILE)
    if file == pre:
        return Verdict.AT_PRE if regular_or_none else Stop(Cause.FOREIGN)
    if file == post:
        if temp not in (ABSENT, pre, post) or temp.kind not in (None, Kind.FILE):
            return Stop(Cause.OUTSIDE_SAVE_IN_TEMP)
        if entry.pre is not None and backup != pre:
            return Stop(Cause.BACKUP_BAD)
        return Verdict.AT_POST
    return Stop(Cause.FOREIGN)
