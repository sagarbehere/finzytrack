"""The per-file judgement of recovery and the rollback (`ledger_engine.ledger.core.recovery`; engine-jobs §8.1
step 2, §6.4; §12 "the recovery check per file" is a pure function; ADR-0012's per-file rule, and the temp rule as
refined on 2026-10-29 (owner: "Refine the rule"); S0061), row by row, then exhaustively over a small set of values.

The temp rule (`classify_temp`): a save temp or a restore temp is ours only when it is empty, a prefix of the bytes
written into it, or equal to the pre or the post bytes; anything else is an outside save, kept, and the journal stops,
whatever the file beside it holds. The file (`judge`): at pre → at pre; at post → at post when the backup holds pre
(a created file: only while the save's temp is gone, red team r2 H1); anything else → stop.
"""

from __future__ import annotations

import itertools

import pytest

from ledger_engine.fs.port import Kind
from ledger_engine.ledger.core.journal import FileEntry
from ledger_engine.ledger.core.recovery import (
    ABSENT,
    Cause,
    Seen,
    Stop,
    TempState,
    Verdict,
    classify_temp,
    judge,
    seen_file,
)
from ledger_engine.ledger.writer import sha256

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-32")]

PRE, POST, FOREIGN = b"pre bytes\n", b"post bytes\n", b"; an editor's save\n"
HELD = (sha256(PRE), sha256(POST))
REPLACED = FileEntry("/w/L/main.bean", "/w/l/main.bean", sha256(PRE), sha256(POST), "2-main.bean", ".m.ft-s1-j.tmp")
CREATED = FileEntry("/w/L/new.bean", "/w/l/new.bean", None, sha256(POST), None, ".n.ft-s1-j.tmp")
OTHER, DIR, LINK = Seen(Kind.OTHER), Seen(Kind.DIR), Seen(Kind.LINK)
GOOD_BACKUP = seen_file(PRE)
A, OK, F = TempState.ABSENT, TempState.OURS, TempState.FOREIGN


def test_a_temp_is_ours_when_empty_a_prefix_of_what_was_written_or_equal_to_pre_or_post() -> None:
    """The refined temp rule, its "ours" side: a write cut off at any point is ours and blocks nothing."""
    assert classify_temp(None, None, HELD, POST) is A
    for n in range(len(POST) + 1):
        assert classify_temp(Kind.FILE, POST[:n], HELD, POST) is OK, n  # a save temp cut at n
    for n in range(len(PRE) + 1):
        assert classify_temp(Kind.FILE, PRE[:n], HELD, PRE) is OK, n  # a restore temp cut at n
    for data in (PRE, POST, b""):
        assert classify_temp(Kind.FILE, data, HELD, None) is OK  # whole: known by its hash, even with no copy


def test_a_temp_holding_anything_else_is_an_outside_save() -> None:
    """Its "foreign" side (red team r2 M1, M2; verifier r2 F2): other bytes, or a prefix of the wrong bytes, or a
    prefix while the bytes it would be cut from are unknown, or not a regular file."""
    for data in (FOREIGN, PRE[:3] + b"x", POST + b"more"):
        assert classify_temp(Kind.FILE, data, HELD, POST) is F
    assert classify_temp(Kind.FILE, PRE[:3], HELD, POST) is F, "a cut of the pre bytes is not a cut of a save temp"
    assert classify_temp(Kind.FILE, POST[:3], HELD, None) is F, "a prefix cannot be known as ours without the bytes"
    for kind in (Kind.DIR, Kind.LINK, Kind.OTHER):
        assert classify_temp(kind, None, HELD, POST) is F


def test_a_foreign_temp_or_restore_temp_stops_the_journal_whatever_the_file_holds() -> None:
    """Owner, 2026-10-29: "Refine the rule": it replaced review 6 P1 ("otherwise recovery deletes it, whatever it
    holds"), which deleted an outside save sitting beside a file at pre."""
    for file in (seen_file(PRE), seen_file(POST), seen_file(FOREIGN), ABSENT):
        for temp, rtemp in ((F, A), (A, F), (F, F), (F, OK), (OK, F)):
            assert judge(REPLACED, file, temp, rtemp, GOOD_BACKUP) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)
            assert judge(REPLACED, file, temp, rtemp, None, skip=True) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)


def test_a_file_at_pre_is_at_pre_beside_temps_of_ours() -> None:
    for temp, rtemp in itertools.product((A, OK), repeat=2):
        assert judge(REPLACED, seen_file(PRE), temp, rtemp, None) is Verdict.AT_PRE
        assert judge(CREATED, ABSENT, temp, rtemp, None) is Verdict.AT_PRE


def test_a_replaced_file_at_post_is_at_post_only_with_a_backup_at_pre() -> None:
    for temp, rtemp in itertools.product((A, OK), repeat=2):
        assert judge(REPLACED, seen_file(POST), temp, rtemp, GOOD_BACKUP) is Verdict.AT_POST
    for backup in (None, ABSENT, seen_file(POST), OTHER):
        assert judge(REPLACED, seen_file(POST), A, A, backup) == Stop(Cause.BACKUP_BAD)


def test_a_created_file_at_post_is_at_post_only_while_the_saves_temp_is_gone() -> None:
    """Red team r2 H1: a temp still there means the exclusive rename never happened, so the file at the path is
    another program's, whatever it holds: never deleted."""
    assert judge(CREATED, seen_file(POST), A, A, None) is Verdict.AT_POST
    assert judge(CREATED, seen_file(POST), A, OK, None) is Verdict.AT_POST
    assert judge(CREATED, seen_file(POST), OK, A, None) == Stop(Cause.FOREIGN)


def test_a_file_at_neither_pre_nor_post_stops() -> None:
    for file in (seen_file(FOREIGN), seen_file(POST[:2]), ABSENT, OTHER, DIR, LINK):
        assert judge(REPLACED, file, A, A, GOOD_BACKUP) == Stop(Cause.FOREIGN)
    for file in (seen_file(FOREIGN), seen_file(PRE), OTHER, DIR, LINK):
        assert judge(CREATED, file, A, A, None) == Stop(Cause.FOREIGN)


def test_the_rollbacks_skipped_file_is_kept_beside_temps_of_ours() -> None:
    for file in (seen_file(FOREIGN), seen_file(PRE), seen_file(POST), ABSENT):
        for temp, rtemp in itertools.product((A, OK), repeat=2):
            assert judge(CREATED, file, temp, rtemp, None, skip=True) is Verdict.KEEP


FILES = (ABSENT, seen_file(PRE), seen_file(POST), seen_file(FOREIGN), OTHER, DIR, LINK)


@pytest.mark.parametrize("entry", [REPLACED, CREATED])
def test_exhaustively_a_verdict_never_loses_outside_bytes_and_never_blocks_for_temps_of_ours(entry: FileEntry) -> None:
    """Over every combination: (a) at post only with the file at post, no foreign temp, a good backup (a replaced
    file) or no save temp (a created file); (b) at pre only with the file at its pre state and no foreign temp; (c) a
    stop only for a cause: never for a file at pre beside temps of ours (`CutTempNeverBlocks`, as the refined rule
    keeps it for cut writes of ours)."""
    pre_state = ABSENT if entry.pre is None else seen_file(PRE)
    for file, temp, rtemp, backup in itertools.product(FILES, (A, OK, F), (A, OK, F), (*FILES, None)):
        v = judge(entry, file, temp, rtemp, backup)
        if v is Verdict.AT_POST:
            assert file == seen_file(POST)
            assert F not in (temp, rtemp)
            assert (entry.pre is None and temp is A) or (entry.pre is not None and backup == seen_file(PRE))
        elif v is Verdict.AT_PRE:
            assert file == pre_state
            assert F not in (temp, rtemp)
        else:
            assert isinstance(v, Stop)
            assert not (file == pre_state and F not in (temp, rtemp))
