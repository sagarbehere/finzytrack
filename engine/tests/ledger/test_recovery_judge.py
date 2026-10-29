"""The per-file judgement of recovery and the rollback (`ledger_engine.ledger.core.recovery.judge`;
`design/specs/engine-jobs.md` §8.1 step 2, §6.4 "The save's temp paths in recovery" (review 6 P1), §12 "the recovery
check per file" is a pure function; ADR-0012's per-file rule as amended by ADR-0064; S0061), row by row, then
exhaustively over a small set of values.

The rows (what the file holds; what the save's temp beside it holds; the backup, needed only for a restore):
1. the file at its pre bytes (a created file: absent): `at_pre`, whatever the temp holds, if it is a regular file or
   absent (a temp cut off at any point while written is ours and blocks nothing: `CutTempNeverBlocks`);
2. the file at its post bytes: `at_post` when the temp is absent or holds the pre or post bytes and the backup holds
   the pre bytes; a temp with other bytes is a displaced outside save: stop (`RecoveryNeverClobbersForeignBytes`;
   mutant `no_temp_check`); a backup not at pre: stop (ADR-0012 F-1);
3. anything else (other bytes, not a regular file): stop (ADR-0012: "anything else → stop ... show both versions");
4. the rollback's kept file (the outside save the save itself put back): kept, its temp only ours (absent or post).
"""

from __future__ import annotations

import itertools

import pytest

from ledger_engine.fs.port import Kind
from ledger_engine.ledger.core.journal import FileEntry
from ledger_engine.ledger.core.recovery import ABSENT, Cause, Seen, Stop, Verdict, judge, seen_file
from ledger_engine.ledger.writer import sha256

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-32")]

PRE, POST, FOREIGN = b"pre\n", b"post\n", b"; an editor's save\n"
REPLACED = FileEntry(
    "/w/L/main.bean", "/w/l/main.bean", sha256(PRE), sha256(POST), "2-main.bean", ".main.bean.ft-j.tmp"
)
CREATED = FileEntry("/w/L/new.bean", "/w/l/new.bean", None, sha256(POST), None, ".new.bean.ft-j.tmp")
OTHER = Seen(Kind.OTHER)
DIR = Seen(Kind.DIR)
LINK = Seen(Kind.LINK)
GOOD_BACKUP = seen_file(PRE)


def test_a_file_at_pre_is_at_pre_whatever_its_temp_holds_cut_temps_included() -> None:
    """Row 1, `CutTempNeverBlocks`: a temp cut off at any length beside a file at pre never blocks."""
    for temp in (ABSENT, seen_file(PRE), seen_file(POST), seen_file(FOREIGN)):
        assert judge(REPLACED, seen_file(PRE), temp, GOOD_BACKUP) is Verdict.AT_PRE
    for n in range(len(POST)):
        assert judge(REPLACED, seen_file(PRE), seen_file(POST[:n]), None) is Verdict.AT_PRE


def test_a_created_file_that_is_absent_is_at_pre() -> None:
    assert judge(CREATED, ABSENT, ABSENT, None) is Verdict.AT_PRE
    assert judge(CREATED, ABSENT, seen_file(POST[:3]), None) is Verdict.AT_PRE


def test_a_file_at_post_with_no_temp_or_a_temp_at_pre_or_post_is_at_post() -> None:
    for temp in (ABSENT, seen_file(PRE), seen_file(POST)):
        assert judge(REPLACED, seen_file(POST), temp, GOOD_BACKUP) is Verdict.AT_POST
    assert judge(CREATED, seen_file(POST), ABSENT, None) is Verdict.AT_POST


def test_a_temp_holding_other_bytes_beside_a_file_at_post_is_an_outside_save_and_stops() -> None:
    """Row 2, review 6 P1, model finding F2: the outside save the swap displaced into the temp is kept."""
    for temp in (seen_file(FOREIGN), seen_file(PRE[:2]), seen_file(b"")):
        assert judge(REPLACED, seen_file(POST), temp, GOOD_BACKUP) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)
    assert judge(CREATED, seen_file(POST), seen_file(FOREIGN), None) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)


def test_a_non_file_at_a_temp_name_stops_beside_any_file() -> None:
    """A folder, a link or a FIFO at a temp name is not a temp of ours (the save made its temps exclusively)."""
    for temp in (OTHER, DIR, LINK):
        assert judge(REPLACED, seen_file(PRE), temp, GOOD_BACKUP) == Stop(Cause.FOREIGN)
        assert judge(REPLACED, seen_file(POST), temp, GOOD_BACKUP) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)


def test_a_file_at_post_whose_backup_does_not_hold_the_pre_bytes_stops() -> None:
    """ADR-0012 F-1: a restore needs a backup that holds the pre bytes."""
    for backup in (None, ABSENT, seen_file(POST), seen_file(PRE[:1]), OTHER):
        assert judge(REPLACED, seen_file(POST), ABSENT, backup) == Stop(Cause.BACKUP_BAD)


def test_a_file_at_pre_needs_no_backup() -> None:
    assert judge(REPLACED, seen_file(PRE), ABSENT, None) is Verdict.AT_PRE
    assert judge(REPLACED, seen_file(PRE), ABSENT, seen_file(FOREIGN)) is Verdict.AT_PRE


def test_a_file_at_neither_pre_nor_post_stops() -> None:
    for file in (seen_file(FOREIGN), seen_file(POST[:2]), ABSENT, OTHER, DIR, LINK):
        assert judge(REPLACED, file, ABSENT, GOOD_BACKUP) == Stop(Cause.FOREIGN)
    for file in (seen_file(FOREIGN), seen_file(PRE), OTHER, DIR, LINK):
        assert judge(CREATED, file, ABSENT, None) == Stop(Cause.FOREIGN)


def test_the_rollbacks_kept_file_is_kept_with_its_temp_only_when_the_temp_is_ours() -> None:
    """§6.4: in a save, the file whose outside save the swap-back kept is left as it is; its temp holds our post bytes
    (or is gone); anything else there is a second outside save (S0060 red team r2 M1): stop."""
    for file in (seen_file(FOREIGN), seen_file(PRE), seen_file(POST), ABSENT):
        assert judge(REPLACED, file, seen_file(POST), None, skip=True) is Verdict.KEEP
        assert judge(REPLACED, file, ABSENT, None, skip=True) is Verdict.KEEP
        for temp in (seen_file(FOREIGN), seen_file(PRE), OTHER):
            assert judge(REPLACED, file, temp, None, skip=True) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)


VALUES = (ABSENT, seen_file(PRE), seen_file(POST), seen_file(FOREIGN), seen_file(b""), OTHER, DIR, LINK)


@pytest.mark.parametrize("entry", [REPLACED, CREATED])
def test_exhaustively_a_verdict_never_loses_outside_bytes_and_never_blocks_for_a_cut_temp(entry: FileEntry) -> None:
    """Over every combination: (a) `at_post` only with the file at post, a temp that holds nothing of anyone else's
    and, for a replaced file, a backup at pre; (b) `at_pre` only with the file at its pre state; (c) a stop only for a
    cause: never for a file at pre beside a regular temp (`CutTempNeverBlocks`), nor for a file at post beside an
    absent, pre or post temp with a good backup (`BlockedOnlyForCause`)."""
    pre_state = ABSENT if entry.pre is None else seen_file(PRE)
    ours = (ABSENT, seen_file(POST)) if entry.pre is None else (ABSENT, seen_file(PRE), seen_file(POST))
    for file, temp, backup in itertools.product(VALUES, VALUES, (*VALUES, None)):
        v = judge(entry, file, temp, backup)
        good_backup = entry.pre is None or backup == seen_file(PRE)
        if v is Verdict.AT_POST:
            assert file == seen_file(POST)
            assert temp in ours
            assert good_backup
        elif v is Verdict.AT_PRE:
            assert file == pre_state
            assert temp.kind in (None, Kind.FILE)
        else:
            assert isinstance(v, Stop)
            assert not (file == pre_state and temp.kind in (None, Kind.FILE))
            assert not (file == seen_file(POST) and temp in ours and good_backup)
