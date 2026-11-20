"""Saves on a Windows network share, best effort (S0136, BL-0219; owner, 2026-11-20: "Best effort + open notice
(Recommended)"; `design/specs/engine-jobs.md` §6.8, §8.1, §15 "Limit (network share durability)"), over the in-memory
file system: `/w/L` on a share whose server refuses a folder's sync (`MemFS.sync_refused`; probed on the CI share,
`ERROR_INVALID_FUNCTION`), with the journal and backups on the machine's own disk.

What is tolerated is exactly one refusal: a ledger folder's sync answered `SyncRefused` (the adapter raises it only on
a remote volume), in the save, the rollback, recovery, Keep, the blocked screen's deletes and create
(`handles.ledger_sync`). Every other error still fails, the same refusal on a local volume fails, and a journal or
backups folder whose sync is refused fails the save. The oracle (`tests.ledger.worlds`, "On a network share") states
the accepted limit: after a power loss of the server a save, or an undo, may come back undone, in whole or in part,
never torn.
"""

from __future__ import annotations

import errno
from functools import cache

import pytest

from ledger_engine.ledger.core.journal import State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.create import Created
from ledger_engine.ledger.handles import DriveKind, drive_kind, ledger_sync
from ledger_engine.ledger.recover_all import Done, delete_kept, keep
from ledger_engine.ledger.restore import Failed, Resolved, Stopped, recover_journal
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Step, Unfinished, durable_commit, sha256
from tests.fakes.memfs import Crash, MemFS
from tests.ledger import test_create as tc
from tests.ledger.test_writer import check_told
from tests.ledger.worlds import (
    BD,
    JD,
    NAME,
    SHARE_DEV,
    check_disk,
    check_every_power_loss,
    check_recovered,
    journals,
    leftovers,
    on_a_share,
    recover,
    world,
)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

MAIN = "/w/L/main.bean"
FOREIGN = b"; saved by another program\n"


@cache
def clean_effects() -> tuple[str, ...]:
    w = world(volume="share")
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


def _refused(effects: tuple[str, ...] | list[str]) -> list[str]:
    return [e for e in effects if e.startswith(("sync /w/L:", "sync /w/L/inc:"))]


# -- the clean paths -----------------------------------------------------------------------------------------------


def test_a_save_on_a_share_lands_with_nothing_of_ours_left_and_its_folder_syncs_refused() -> None:
    """The save's every ledger-folder sync is refused by the server (`SyncRefused`) and counts as done; "saved" with
    the closed record durable on the machine's own disk; nothing of ours left; no swap asked."""
    w = world(volume="share")
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out
    for t in w.targets:
        assert w.fs.file(t.path) == t.post
        for name in t.scratch:
            assert w.fs.file(name) is None, name
    assert set(journals(w.fs)) == {State.CLOSED}
    assert len(_refused(w.fs.effects)) >= 3, "at least one refused sync per file placed"
    assert not any(x.startswith("swap ") for x in w.fs.effects)
    assert check_every_power_loss(w.fs, w) > 1, "the share keeps nothing durable for us: several states"


def test_the_order_on_a_share_is_the_best_effort_order_with_the_exclusive_rename() -> None:
    """§6.8 on a share: backups and the `.open` journal synced (on the machine's own disk) before the first replace;
    per file the staging name synced (a file's own flush works on a share), a plain rename onto the file (a created
    file: the exclusive rename the share offers), then the folder's sync, refused and taken as done."""
    e = clean_effects()
    jsync = e.index(f"sync {JD}:", e.index(f"rename {JD}:{NAME}.part,{NAME}.open"))
    for folder, base, verb in (
        ("/w/L/inc", "a.bean", "move"),
        ("/w/L", "prices.bean", "rename"),
        ("/w/L", "main.bean", "move"),
    ):
        fsync = e.index(f"fsync {folder}:.{base}.ft-s1-j7.new")
        put = e.index(f"{verb} {folder}:.{base}.ft-s1-j7.new,{base}")
        dsync = e.index(f"sync {folder}:", put)
        assert jsync < fsync < put < dsync, (base, e)
    assert e[-1] == f"sync {JD}:"


def test_the_rollback_on_a_share_puts_the_old_bytes_back_and_says_unchanged() -> None:
    """An I/O error after the first replace: the rollback puts every replaced file back from its backup (its folder
    syncs refused, taken as done) and resolves: "not saved", "unchanged", the journal `.resolved`."""
    e = clean_effects()
    at = e.index("move /w/L/inc:.a.bean.ft-s1-j7.new,a.bean") + 1  # a.bean replaced, the created file not yet
    w = world(volume="share")
    w.fs.inject(at, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.unchanged, out.open_journal) == (Reason.IO_ERROR, True, False), out
    assert set(journals(w.fs)) == {State.RESOLVED}
    for t in w.targets:
        if t.pre is not None:
            assert w.fs.file(t.path) == t.pre
    assert len(_refused(w.fs.effects[at:])) >= 2, "the rollback's syncs on the share were refused and taken as done"


def test_recovery_on_a_share_resolves_after_a_crash() -> None:
    w = world(volume="share")
    w.fs.inject(clean_effects().index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    out = recover(w.fs)
    assert isinstance(out, Stopped), out
    assert out.cause is Cause.CREATED_PATH_TAKEN, out  # our created file: asked once (owner, 2026-10-30)
    check_recovered(w.fs, w, out)


def test_keep_and_a_kept_copys_delete_work_on_a_share() -> None:
    """Keep syncs the ledger's folders (refused on a share, taken as done) and closes the journal; a kept copy's
    delete removes it and syncs its folder the same way."""
    w = world(volume="share")
    w.fs.inject(clean_effects().index("move /w/L:.main.bean.ft-s1-j7.new,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_write("/w/L/.main.bean.ft-s1-j7.tmp", FOREIGN)
    out = recover(w.fs)
    assert isinstance(out, Stopped), out
    kept = next(n for n in w.fs.names_in("/w/L") if ".kept-" in n)
    assert delete_kept(w.fs, JD, NAME, MAIN, kept, sha256(FOREIGN)) == Done()
    assert kept not in w.fs.names_in("/w/L")
    start = len(w.fs.effects)
    assert keep(w.fs, JD, NAME) == Done()
    assert set(journals(w.fs)) == {State.KEPT}
    assert _refused(w.fs.effects[start:]), "Keep's folder syncs on the share were refused and taken as done"


def test_a_create_on_a_share_is_created() -> None:
    fs = MemFS(windows=True)
    fs.outside_mkdirs(tc.U)
    on_a_share(fs, tc.U)
    out, _ = tc.go(fs)
    assert out == Created(tc.T)
    assert tc.created_whole(fs)
    assert any(e.startswith(f"sync {tc.T}:") for e in fs.effects)
    for state in fs.power_loss_states():
        for path, data in {**tc.OURS, **tc.MANIFEST}.items():
            assert state.file(path) in (None, data), f"{path} torn after a power loss"  # whole or not there (the limit)


def test_the_notice_kind_tells_a_share_from_a_drive_without_the_swap_and_a_regular_disk() -> None:
    share = world(volume="share")
    with share.fs.open_dir("/w/L") as d:
        assert drive_kind(d) is DriveKind.NETWORK_SHARE
    with share.fs.open_dir(JD) as d:
        assert drive_kind(d) is DriveKind.SWAP  # the machine's own disk beside it
    for volume in ("no_swap", "plain"):
        with world(volume=volume).fs.open_dir("/w/L") as d:
            assert drive_kind(d) is DriveKind.BEST_EFFORT, volume
    with world().fs.open_dir("/w/L") as d:
        assert drive_kind(d) is DriveKind.SWAP
    posix = world(volume="plain")  # a POSIX volume never answers remote(): a share there is not told apart (M11)
    posix.fs.remote.add(1)
    with posix.fs.open_dir("/w/L") as d:
        assert d.remote() is None
        assert drive_kind(d) is DriveKind.BEST_EFFORT


# -- what is never tolerated ---------------------------------------------------------------------------------------


def test_the_same_refusal_on_a_local_volume_fails_the_save_and_recovery() -> None:
    """The must-not-pass case: a drive whose folder sync is refused but which is not a network share (the adapter
    raises a plain `OSError` there): the save is never "saved", and recovery cannot resolve (fails closed)."""
    w = world(volume="share")
    w.fs.remote.discard(SHARE_DEV)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed), out
    check_told(w.fs, w, out)
    assert State.CLOSED not in journals(w.fs)
    again = recover(w.fs)
    assert isinstance(again, Failed), again
    assert again.errno == errno.EINVAL, again


def test_ledger_sync_takes_only_the_share_refusal_as_done() -> None:
    """`handles.ledger_sync`: `SyncRefused` is done; a plain `OSError` (any other error, or the code on a local
    volume) raises."""
    w = world(volume="share")
    with w.fs.open_dir("/w/L") as d:
        ledger_sync(d)
        w.fs.inject(len(w.fs.effects), crash=False, err=errno.EIO)
        with pytest.raises(OSError, match="injected") as e:
            ledger_sync(d)
        assert e.value.errno == errno.EIO
    w.fs.remote.discard(SHARE_DEV)
    with w.fs.open_dir("/w/L") as d, pytest.raises(OSError, match="refused") as e2:
        ledger_sync(d)
    assert e2.value.errno == errno.EINVAL


def test_a_journal_or_backups_folder_on_a_share_fails_the_save_before_any_ledger_write() -> None:
    """The journal and backups must be durable (INV-27, INV-30): their syncs never go through `ledger_sync`, so an
    app folder on a share (`--app-root` there) never saves, nothing written to the ledger."""
    w = world(volume="share")
    on_a_share(w.fs, "/w/local")
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.unchanged) == (Step.BACKUP, Reason.IO_ERROR, True), out
    for t in w.targets:
        assert w.fs.file(t.path) == t.pre


def test_a_files_own_flush_is_never_tolerated_on_a_share() -> None:
    """A file's data sync (`fsync` of the staging name) failing on a share fails the save as anywhere."""
    w = world(volume="share")
    w.fs.inject(clean_effects().index("fsync /w/L:.main.bean.ft-s1-j7.new"), crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed), out
    check_told(w.fs, w, out)


def test_on_a_share_the_rollback_flushes_an_untouched_file_it_would_skip_on_local_ntfs() -> None:
    """`restore._flush` (S0128 round 1's skip of a file the save never replaced, keyed by `FILE_ID_TYPES`): a share
    reports `ntfs` with a file ID its server gives, so the skip also needs a local folder (S0135 verifier F1). The
    save fails at its first file, before the root is replaced: on local NTFS the rollback does not flush the root, on
    the share it does."""
    flushed: dict[bool, bool] = {}
    for remote in (False, True):
        w = world(volume="share")
        if not remote:
            w.fs.remote.discard(SHARE_DEV)
            w.fs.sync_refused.discard(SHARE_DEV)
        e = clean_effects()
        at = e.index("move /w/L/inc:.a.bean.ft-s1-j7.new,a.bean") + 1  # the folder's sync after a.bean's replace
        w.fs.inject(at, crash=False, err=errno.EIO)
        out = durable_commit(w.fs, w.commit)
        assert isinstance(out, NotSaved), out
        assert out.unchanged, out
        flushed[remote] = "sync_file /w/L:main.bean" in w.fs.effects[at:]
    assert flushed == {False: False, True: True}, flushed


# -- sweeps --------------------------------------------------------------------------------------------------------


def _cuts(label: str) -> list[int | None]:
    return [None, 0, 7] if label.startswith("write ") else [None]


CRASH_POINTS = [(i, cut) for i, x in enumerate(clean_effects()) for cut in _cuts(x)]


@pytest.mark.parametrize(("at", "cut"), CRASH_POINTS)
def test_a_crash_at_every_effect_then_every_power_loss_leaves_a_disk_recovery_resolves(
    at: int, cut: int | None
) -> None:
    """A crash at each effect, every state a power loss of the server and the machine may leave, then recovery: every
    file whole, recovery resolves wherever the journal is `.open`, and a `.closed` save may come back undone (the
    accepted limit)."""
    w = world(volume="share")
    w.fs.inject(at, crash=True, cut=cut)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))


@pytest.mark.parametrize("at", range(len(clean_effects())))
def test_an_io_error_at_every_effect_is_rolled_back_and_told_truthfully(at: int) -> None:
    """Every single I/O error (a refused folder sync on the share included, when it answers anything but
    `SyncRefused`) rolls back; told truthfully; never "saved"; recovery resolves what is left."""
    w = world(volume="share")
    w.fs.inject(at, crash=False, err=errno.ENOSPC)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed), (at, w.fs.effects[at], out)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)
    if isinstance(out, Unfinished):
        check_recovered(w.fs, w, recover(w.fs))
    else:
        assert isinstance(out, NotSaved), out
        assert out.unchanged, (at, w.fs.effects[at], out)


@pytest.mark.parametrize("at", range(len(clean_effects())))
def test_an_effect_that_happens_but_answers_an_error_is_never_told_saved_and_recovery_resolves(at: int) -> None:
    w = world(volume="share")
    w.fs.inject(at, crash=False, err=errno.EIO, after=True)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed), (at, w.fs.effects[at], out)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)
    check_recovered(w.fs, w, recover(w.fs))


def _rollback_points() -> list[tuple[int, int]]:
    e = clean_effects()
    start = e.index(f"rename {JD}:{NAME}.part,{NAME}.open")
    out: list[tuple[int, int]] = []
    for at in range(start, len(e)):
        w = world(volume="share")
        w.fs.inject(at, crash=False)
        durable_commit(w.fs, w.commit)
        out += [(at, j) for j in range(at + 1, len(w.fs.effects))]
    return out


ROLLBACK_POINTS = _rollback_points()


@pytest.mark.parametrize(("at", "j"), ROLLBACK_POINTS)
def test_an_io_error_or_a_crash_in_the_rollback_is_told_truthfully_and_recovery_then_resolves(at: int, j: int) -> None:
    w = world(volume="share")
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed)
    check_told(w.fs, w, out)
    check_disk(w.fs, w)
    check_recovered(w.fs, w, recover(w.fs))
    w = world(volume="share")
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))


# -- the oracle's limit, stated so it stays known ------------------------------------------------------------------


def test_a_server_power_loss_right_after_saved_can_undo_the_save_whole_and_never_torn() -> None:
    """The accepted limit (owner, 2026-11-20): after "saved" a power loss of the file server may bring back the old
    bytes of any replaced file, the journal still `.closed`; never a torn or missing file; the backup holds the old
    bytes either way."""
    w = world(volume="share")
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    undone = 0
    for state in w.fs.power_loss_states():
        assert set(journals(state)) == {State.CLOSED}
        for t in w.targets:
            assert state.file(t.path) in (t.pre, t.post), t.path
        if state.file(MAIN) == w.targets[2].pre:
            undone += 1
        assert state.file(f"{BD}/{NAME}/2-main.bean") == w.targets[2].pre
    assert undone > 0, "the limit is reachable in the fake: the save can come back undone"


def test_without_a_power_loss_the_share_oracle_is_the_full_one() -> None:
    """The must-not-pass case of the oracle's relaxation: a `.closed` journal over a file at pre is refused unless a
    power loss left the state."""
    w = world(volume="share")
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    pre = w.targets[2].pre
    assert pre is not None
    w.fs.outside_write(MAIN, pre)
    with pytest.raises(AssertionError, match="not at post"):
        check_disk(w.fs, w)
    lost = next(s for s in w.fs.power_loss_states())
    lost.outside_write(MAIN, pre)
    check_disk(lost, w)  # the same disk after a power loss: the limit
    assert leftovers(lost, w) == [] or lost.lost_power


def test_recovery_after_a_power_loss_still_resolves_and_ends_at_pre() -> None:
    """A crash after the first replace, then a power loss: whatever the share kept, recovery resolves and the disk it
    leaves (before any further power loss) holds every file at pre."""
    w = world(volume="share")
    w.fs.inject(clean_effects().index("move /w/L:.main.bean.ft-s1-j7.new,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    n = 0
    for state in w.fs.power_loss_states():
        out = recover_journal(state, JD, NAME)
        if isinstance(out, Stopped):
            assert out.cause is Cause.CREATED_PATH_TAKEN, out
            continue
        assert isinstance(out, Resolved), out
        n += 1
        for t in w.targets:
            if t.pre is not None:
                assert state.file(t.path) == t.pre
    assert n > 0
