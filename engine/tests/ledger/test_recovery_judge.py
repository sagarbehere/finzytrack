"""The per-file judgement of recovery and the rollback (`ledger_engine.ledger.core.recovery`; engine-jobs §8.1
step 2, §6.4; §12 "the recovery check per file" is a pure function; ADR-0012's per-file rule, and the temp rule as
decided on 2026-10-30 (owner: "Staging name (Recommended)"); S0061), row by row, then exhaustively over a small set of
values.

The temp rule: what stands at a **staging name** (`classify_staging`) is our own write, deleted whatever bytes it holds
(a regular file; anything else is not ours); what stands at a **temp name** (`classify_temp`) is ours only when it
equals the pre or the post bytes; anything else is an outside save, kept, and the journal stops, whatever the file
beside it holds. The file (`judge`): at pre → at pre; at post → at post when the backup holds pre (a created file:
only while neither the save's temp nor its staging file is there, red team r2 H1); anything else → stop.
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
    Temps,
    TempState,
    Verdict,
    classify_staging,
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


def test_a_temp_name_holds_ours_only_when_it_equals_the_pre_or_the_post_bytes() -> None:
    """Owner, 2026-10-30: "Staging name (Recommended)": the temp name only ever holds our fully synced bytes or the
    displaced old object, so equality is the whole rule there."""
    assert classify_temp(None, None, HELD) is A
    for data in (PRE, POST):
        assert classify_temp(Kind.FILE, data, HELD) is OK
    assert classify_temp(Kind.FILE, POST, (None, sha256(POST))) is OK  # a created file: no pre bytes


def test_a_temp_name_holding_anything_else_is_an_outside_save_a_prefix_or_nothing_included() -> None:
    """S0061 r3: red team F2 (an editor truncating the displaced object in place: a prefix of pre, so of post when the
    save appends), verifier F3 (the same beside a file at pre). An empty file and a prefix are no longer ours there:
    no cut write of ours can stand at a temp name."""
    for data in (FOREIGN, b"", PRE[:3], POST[:3], POST[:-1], PRE + b"x", POST + b"more", bytes(len(POST))):
        assert classify_temp(Kind.FILE, data, HELD) is F, data
    for kind in (Kind.DIR, Kind.LINK, Kind.OTHER):
        assert classify_temp(kind, None, HELD) is F
    assert classify_temp(Kind.FILE, None, HELD) is F  # a file that could not be read (verifier r3: untested before)
    assert classify_temp(Kind.DIR, PRE, HELD) is F  # the kind decides, whatever bytes come with it


def test_a_staging_name_holds_our_own_write_whatever_bytes_it_holds() -> None:
    """No swap ever touches a staging name, so a regular file there is a write of ours, cut or torn in any shape (S0061
    red team r3 F3); anything that is not a regular file is not ours."""
    assert classify_staging(None) is A
    assert classify_staging(Kind.FILE) is OK
    for kind in (Kind.DIR, Kind.LINK, Kind.OTHER):
        assert classify_staging(kind) is F


def test_a_foreign_entry_at_any_name_of_ours_stops_the_journal_whatever_the_file_holds() -> None:
    """Owner, 2026-10-29: "Refine the rule" (it replaced review 6 P1's "otherwise recovery deletes it, whatever it
    holds"), kept by "Staging name (Recommended)" for every name of ours beside a file."""
    for file in (seen_file(PRE), seen_file(POST), seen_file(FOREIGN), ABSENT):
        for where in range(4):
            for other in (A, OK):
                states = [other] * 4
                states[where] = F
                temps = Temps(*states)
                assert judge(REPLACED, file, temps, GOOD_BACKUP) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)
                assert judge(REPLACED, file, temps, None, skip=True) == Stop(Cause.OUTSIDE_SAVE_IN_TEMP)


OURS_BESIDE = [Temps(*c) for c in itertools.product((A, OK), repeat=4)]


def test_a_file_at_pre_is_at_pre_beside_temps_of_ours() -> None:
    for temps in OURS_BESIDE:
        assert judge(REPLACED, seen_file(PRE), temps, None) is Verdict.AT_PRE
        assert judge(CREATED, ABSENT, temps, None) is Verdict.AT_PRE


def test_a_replaced_file_at_post_is_at_post_only_with_a_backup_at_pre() -> None:
    for temps in OURS_BESIDE:
        assert judge(REPLACED, seen_file(POST), temps, GOOD_BACKUP) is Verdict.AT_POST
    for backup in (None, ABSENT, seen_file(POST), OTHER):
        assert judge(REPLACED, seen_file(POST), Temps(), backup) == Stop(Cause.BACKUP_BAD)


def test_a_created_file_at_post_is_at_post_only_while_the_saves_temp_and_staging_file_are_gone() -> None:
    """Red team r2 H1: a temp still there means the exclusive rename never happened, so the file at the path is
    another program's, whatever it holds: never deleted. A staging file still there means the same (its rename to the
    temp name, synced, comes before the exclusive rename)."""
    assert judge(CREATED, seen_file(POST), Temps(), None) is Verdict.AT_POST
    assert judge(CREATED, seen_file(POST), Temps(rtemp=OK, rstaging=OK), None) is Verdict.AT_POST
    assert judge(CREATED, seen_file(POST), Temps(temp=OK), None) == Stop(Cause.FOREIGN)
    assert judge(CREATED, seen_file(POST), Temps(staging=OK), None) == Stop(Cause.FOREIGN)


def test_a_file_at_neither_pre_nor_post_stops() -> None:
    for file in (seen_file(FOREIGN), seen_file(POST[:2]), ABSENT, OTHER, DIR, LINK):
        assert judge(REPLACED, file, Temps(), GOOD_BACKUP) == Stop(Cause.FOREIGN)
    for file in (seen_file(FOREIGN), seen_file(PRE), OTHER, DIR, LINK):
        assert judge(CREATED, file, Temps(), None) == Stop(Cause.FOREIGN)


def test_the_rollbacks_skipped_file_is_kept_beside_temps_of_ours() -> None:
    for file in (seen_file(FOREIGN), seen_file(PRE), seen_file(POST), ABSENT):
        for temps in OURS_BESIDE:
            assert judge(CREATED, file, temps, None, skip=True) is Verdict.KEEP


FILES = (ABSENT, seen_file(PRE), seen_file(POST), seen_file(FOREIGN), OTHER, DIR, LINK)


@pytest.mark.parametrize("entry", [REPLACED, CREATED])
def test_exhaustively_a_verdict_never_loses_outside_bytes_and_never_blocks_for_temps_of_ours(entry: FileEntry) -> None:
    """Over every combination: (a) at post only with the file at post, no foreign entry at a name of ours, a good
    backup (a replaced file) or neither a save temp nor a staging file (a created file); (b) at pre only with the file
    at its pre state and nothing foreign beside it; (c) a stop only for a cause: never for a file at pre beside names
    of ours that hold our own writes (`CutTempNeverBlocks`)."""
    pre_state = ABSENT if entry.pre is None else seen_file(PRE)
    for file, states, backup in itertools.product(FILES, itertools.product((A, OK, F), repeat=4), (*FILES, None)):
        temps = Temps(*states)
        v = judge(entry, file, temps, backup)
        if v is Verdict.AT_POST:
            assert file == seen_file(POST)
            assert F not in states
            assert (entry.pre is None and temps.temp is A and temps.staging is A) or (
                entry.pre is not None and backup == seen_file(PRE)
            )
        elif v is Verdict.AT_PRE:
            assert file == pre_state
            assert F not in states
        else:
            assert isinstance(v, Stop)
            assert not (file == pre_state and F not in states)
