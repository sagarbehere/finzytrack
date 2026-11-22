"""A ledger file another program holds (S0148, BL-0284; owner, 2026-11-21: "I'm accepting the inbox note as written",
one clear "in use, try again" text naming the file, no retry; `design/specs/engine-jobs.md` §6.8, §11), over the fake
in its Windows mode: `/w/L` on a volume without the swap, with the exclusive rename, as every Windows volume is
(ADR-0069), and a hold told by the adapter's `InUseError` (`fs.port`).

The save tells `in_use` only on a "not saved" outcome whose rollback (if any) resolved, naming a file of the save, and
"unchanged" only when every file is at its old bytes (the oracle `test_writer.check_told`); a rollback or recovery that
meets a held file keeps today's outcome (unfinished, writes paused; recovery fails with the error kept, `EBUSY`). A
plain `OSError` of any errno never becomes `in_use`: macOS's and Linux's own `EBUSY` (a mount point) included.
"""

from __future__ import annotations

import errno
from dataclasses import replace
from functools import cache

import pytest

from ledger_engine.ledger.core.journal import State
from ledger_engine.ledger.restore import Failed, Resolved
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Step, Unfinished, durable_commit
from tests.fakes.memfs import Crash
from tests.ledger.test_writer import check_told
from tests.ledger.worlds import World, check_disk, commit_for, journals, recover, world

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

MAIN = "/w/L/main.bean"
A = "/w/L/inc/a.bean"


def _windows(*, replaced_only: bool = False) -> World:
    """The save's world (`worlds.world`) on a Windows-like volume; `replaced_only`: without the created file, so a
    rollback has no created path to stop at."""
    w = world(volume="no_swap")
    w.fs.windows = True
    if replaced_only:
        targets = tuple(t for t in w.targets if t.pre is not None)
        w = replace(w, commit=commit_for(targets), targets=targets)
    return w


@cache
def clean_effects() -> tuple[str, ...]:
    w = _windows()
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


def _paths(w: World) -> set[str]:
    return {t.path for t in w.targets}


def test_a_file_held_at_the_saves_check_is_in_use_unchanged_named_and_nothing_is_written() -> None:
    """The save's pre-write check asks for delete access to each file it replaces (`DirHandle.allowed`): a file another
    program holds without sharing deletion (an editor's usual open) stops it there, `in_use` naming the file, "not
    saved", unchanged, no journal, nothing written."""
    w = _windows()
    w.fs.outside_hold(MAIN)
    before = w.fs.snapshot()
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.path, out.unchanged, out.open_journal) == (
        Step.BACKUP,
        Reason.IN_USE,
        MAIN,
        True,
        False,
    ), out
    assert journals(w.fs) == {}
    assert w.fs.snapshot() == before, "nothing written"


@pytest.mark.parametrize("at", range(len(clean_effects())))
def test_a_hold_at_every_effect_is_in_use_only_when_not_saved_and_names_a_file_of_the_save(at: int) -> None:
    """A hold met at every effect of the save: never "saved"; the outcome agrees with the disk in every power-loss state
    (`check_told`: "not saved" only with every file at its old bytes, "unchanged" only after the re-hash); a "not saved"
    outcome is `in_use` exactly when the save was working on one of its files (the file named), else `io_error` (its own
    record, its backups folder: no ledger file to name)."""
    w = _windows()
    w.fs.inject(at, crash=False, in_use=True)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_told(w.fs, w, out)
    assert not isinstance(out, Landed), out
    if isinstance(out, NotSaved) and not out.open_journal:
        assert out.reason in (Reason.IN_USE, Reason.IO_ERROR), out
        assert (out.reason is Reason.IN_USE) == (out.path is not None), out
        assert out.path is None or out.path in _paths(w), out
    if isinstance(out, NotSaved) and out.unchanged:
        for t in w.targets:
            assert w.fs.file(t.path) == t.pre, t.path


def test_the_sweep_reaches_in_use_at_the_backups_and_the_replace_and_io_error_at_the_record() -> None:
    """The sweep above is not vacuous: `in_use` is told for a hold at a backed-up file's flush and at its replace
    (a.bean placed, the save rolled back), and `io_error` for one at the save's own record (no ledger file to name).
    The pre-write check makes no effect in the fake: its own test is the first one above."""
    told: dict[str, Reason] = {}
    for at, label in enumerate(clean_effects()):
        w = _windows()
        w.fs.inject(at, crash=False, in_use=True)
        out = durable_commit(w.fs, w.commit)
        if isinstance(out, NotSaved) and not out.open_journal:
            told[label] = out.reason
    assert told["sync_file /w/L/inc:a.bean"] is Reason.IN_USE, told
    assert told["move /w/L/inc:.a.bean.ft-s1-j7.new,a.bean"] is Reason.IN_USE, told
    assert told["create /w/local/journal:s1-j7.part"] is Reason.IO_ERROR, told


@pytest.mark.parametrize("err", [errno.EBUSY, errno.EACCES, errno.EIO, errno.ENOSPC])
@pytest.mark.parametrize("at", range(len(clean_effects())))
def test_must_not_pass_a_plain_error_of_any_errno_is_never_in_use(at: int, err: int) -> None:
    """Must not pass: an `OSError` that is not the adapter's `InUseError` (POSIX's own `EBUSY`, a mount point; an access
    refusal; an I/O error) is never told "in use", whatever effect it meets."""
    w = _windows()
    w.fs.inject(at, crash=False, err=err)
    out = durable_commit(w.fs, w.commit)
    check_told(w.fs, w, out)
    reasons = (out.reason,) if isinstance(out, NotSaved | Unfinished) else ()
    assert Reason.IN_USE not in reasons, (at, err, out)


def test_the_same_world_without_a_hold_saves() -> None:
    """@regression-guard (holds on the base): the control of the tests above, the world saves when nothing holds a
    file (the check asks delete access of a file nobody holds)."""
    w = _windows()
    assert isinstance(durable_commit(w.fs, w.commit), Landed)


def test_a_rollback_that_meets_a_held_file_stays_unfinished_and_recovery_fails_on_it_until_it_is_released() -> None:
    """main.bean's replace is refused by a hold, and by then another program holds a.bean, which the save had already
    replaced: the rollback cannot put a.bean back, so the save is unfinished (writes paused: the sidecar's Reopen text,
    never "not saved"), the journal `.open`, a.bean whole at its new bytes. Recovery's try while it is held fails with
    the error kept (`EBUSY`, naming a.bean), writing nothing; once released, it resolves with every file at its old
    bytes."""
    w = _windows(replaced_only=True)
    clean = _windows(replaced_only=True)
    assert isinstance(durable_commit(clean.fs, clean.commit), Landed)
    main_move = next(i for i, e in enumerate(clean.fs.effects) if e.startswith("move /w/L:"))
    w.fs.inject(main_move, crash=False, in_use=True)

    def hold_a(i: int, label: str) -> None:
        if i == main_move:
            w.fs.outside_hold(A)

    w.fs.on_effect = hold_a
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished), out
    assert set(journals(w.fs)) == {State.OPEN}
    a = next(t for t in w.targets if t.path == A)
    assert w.fs.file(A) == a.post
    w.fs.on_effect = None
    tried = recover(w.fs)
    assert isinstance(tried, Failed), tried
    assert (tried.errno, tried.path) == (errno.EBUSY, A), tried
    assert set(journals(w.fs)) == {State.OPEN}
    w.fs.outside_release(A)
    assert isinstance(recover(w.fs), Resolved)
    for t in w.targets:
        assert w.fs.file(t.path) == t.pre, t.path


# -- the replace refused `ACCESS_DENIED` (owner, 2026-11-22: "opetion 2") ---------------------------------------------

REPLACES = ("move /w/L/inc:.a.bean.ft-s1-j7.new,a.bean", "move /w/L:.main.bean.ft-s1-j7.new,main.bean")


@cache
def replaced_only_effects() -> tuple[str, ...]:
    """The clean run of the world without its created file (`_windows(replaced_only=True)`), whose rollback a
    replace's failure always resolves."""
    w = _windows(replaced_only=True)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


@pytest.mark.parametrize("label", REPLACES)
def test_a_replace_refused_access_denied_after_the_check_is_in_use_rolled_back_and_unchanged(label: str) -> None:
    """Windows refuses a plain rename over a file another handle holds `ACCESS_DENIED` (`ReplaceDenied`), also when the
    holder shares deletion (antivirus, a share's look), which the save's check lets through. The file passed that check
    just before (delete access granted, not read-only), so the save tells `in_use`, naming it; the rollback resolves:
    "not saved", unchanged after the re-hash."""
    w = _windows(replaced_only=True)
    w.fs.inject(replaced_only_effects().index(label), crash=False, denied=True)
    out = durable_commit(w.fs, w.commit)
    check_told(w.fs, w, out)
    assert isinstance(out, NotSaved), out
    path = label.rpartition(",")[2]
    assert (out.step, out.reason, out.unchanged, out.open_journal) == (Step.REPLACE, Reason.IN_USE, True, False), out
    assert out.path is not None, out
    assert out.path.endswith("/" + path), out


def test_a_program_that_opens_the_file_after_the_check_gets_in_use_at_the_replace() -> None:
    """The fake's own hold, taken just before main.bean's replace: the rename over it is refused (`ReplaceDenied`, as
    Windows refuses it), told `in_use` naming main.bean; a.bean put back, unchanged."""
    w = _windows(replaced_only=True)
    at = replaced_only_effects().index(REPLACES[1])

    def hold(i: int, label: str) -> None:
        if i == at:
            w.fs.outside_hold(MAIN)

    w.fs.on_effect = hold
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path, out.unchanged, out.open_journal) == (Reason.IN_USE, MAIN, True, False), out


@pytest.mark.parametrize("at", range(len(clean_effects())))
def test_must_not_pass_replace_denied_anywhere_but_a_checked_files_replace_is_never_in_use(at: int) -> None:
    """Must not pass: `ReplaceDenied` met anywhere but the plain rename over a file the save checked (its record, its
    backups, a created file's placement, a folder's sync) is never told "in use"."""
    if clean_effects()[at] in REPLACES:
        pytest.skip("the replace itself: the test above")
    w = _windows()
    w.fs.inject(at, crash=False, denied=True)
    out = durable_commit(w.fs, w.commit)
    check_told(w.fs, w, out)
    reasons = (out.reason,) if isinstance(out, NotSaved | Unfinished) else ()
    assert Reason.IN_USE not in reasons, (at, clean_effects()[at], out)


@pytest.mark.parametrize("err", [errno.EACCES, errno.EPERM])
@pytest.mark.parametrize("label", REPLACES)
def test_must_not_pass_a_plain_permission_error_at_the_replace_is_not_in_use(label: str, err: int) -> None:
    """Must not pass: a plain `PermissionError` at the replace (macOS's or Linux's `EACCES`, `EPERM`: a lock flag set
    since the check) is not Windows' `ACCESS_DENIED` and stays `io_error`."""
    w = _windows(replaced_only=True)
    w.fs.inject(replaced_only_effects().index(label), crash=False, err=err)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.IO_ERROR, out


def test_recovery_meeting_replace_denied_keeps_the_error_eacces() -> None:
    """Recovery's try keeps today's error for `ReplaceDenied` (`EACCES`, told with its permission text): only a sharing
    violation (`EBUSY`) is told in use there (S0148; owner, 2026-11-22: "opetion 2")."""
    main_move = replaced_only_effects().index(REPLACES[1])

    def crashed() -> World:
        w = _windows(replaced_only=True)
        w.fs.inject(main_move, crash=True)
        with pytest.raises(Crash):
            durable_commit(w.fs, w.commit)
        return w

    probe = crashed()
    n = len(probe.fs.effects)
    assert isinstance(recover(probe.fs), Resolved)
    put_back = next(i for i, e in enumerate(probe.fs.effects) if i >= n and e.startswith("move /w/L/inc:"))
    w = crashed()
    w.fs.inject(put_back, crash=False, denied=True)
    tried = recover(w.fs)
    assert isinstance(tried, Failed), tried
    assert tried.errno == errno.EACCES, tried
