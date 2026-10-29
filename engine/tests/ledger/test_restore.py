"""The restore shared by the rollback and recovery's per-journal try (`ledger_engine.ledger.restore`;
`design/specs/engine-jobs.md` §6.4, §8.1 steps 1-4, §10, §13 recovery properties, §14 "Recovery" and "Save, durable
order"; ADR-0012, ADR-0013, ADR-0063, ADR-0064; S0061), over the in-memory file system with its durable layer.

Row by row from §8.1: step 1 (locks without waiting), step 2 (every check before any write, each stop cause), step 3
(restore root first by the save's durable discipline, the displaced check at every swap and removal, temps), step 4
(`.resolved` only after every restore is synced); then the sweeps: a crash at every effect of the save followed by
recovery; a crash at every effect of recovery followed by recovery again; an I/O error at every effect of recovery
followed by a second try; an outside write before every effect of recovery. The oracle is `tests.ledger.worlds`.
"""

from __future__ import annotations

import errno
from collections.abc import Callable

import pytest

from ledger_engine.fs.port import Right
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.restore import Failed, Resolved, Stopped
from ledger_engine.ledger.writer import durable_commit
from tests.fakes.memfs import Crash, MemFS, MemLocks
from tests.ledger.worlds import (
    JD,
    NAME,
    SAVE_DIR,
    Target,
    World,
    check_disk,
    check_every_power_loss,
    check_recovered,
    journals,
    recover,
    world,
)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-32")]


def clean_effects(*, new_dir: bool = False) -> list[str]:
    w = world(new_dir=new_dir)
    durable_commit(w.fs, w.commit)
    return w.fs.effects


EFFECTS = clean_effects()
NEW_DIR_EFFECTS = clean_effects(new_dir=True)
CLOSE = EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed")  # every file at post, temps removed
FIRST_REMOVE = next(i for i, e in enumerate(EFFECTS) if e.startswith("remove "))  # at post, temps still there
NEW_DIR_CLOSE = NEW_DIR_EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed")


def crashed(at: int, *, cut: int | None = None, new_dir: bool = False) -> World:
    """The save crashed at effect `at`: the disk as the OS keeps it after the process died."""
    w = world(new_dir=new_dir)
    w.fs.inject(at, crash=True, cut=cut)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    return w


def _cuts(label: str) -> list[int | None]:
    if not label.startswith("write "):
        return [None]
    if ".ft-s1-j7.tmp" in label:
        return [None, *range(64)]
    return [None, 0, 1, 2, 7, 64, 200, 500]


SAVE_POINTS = [(i, cut, False) for i, label in enumerate(EFFECTS) for cut in _cuts(label)] + [
    (i, None, True) for i in range(len(NEW_DIR_EFFECTS))
]


@pytest.mark.parametrize(("at", "cut", "new_dir"), SAVE_POINTS)
def test_a_crash_at_every_effect_of_the_save_then_recovery_ends_with_every_file_at_pre_or_saved(
    at: int, cut: int | None, new_dir: bool
) -> None:
    """§10 every row, §14 "each followed by recover-all": the disk as the OS keeps it after the crash and every state
    a power loss may leave from there, each recovered: the journal resolved with every file at pre, or (only under a
    `.closed` journal) every file at post; nothing of ours left; the outcome told matches the disk."""
    w = crashed(at, cut=cut, new_dir=new_dir)
    for state in (w.fs, *w.fs.power_loss_states()):
        out = recover(state)
        check_recovered(state, w, out)


RECOVERY_POINTS = [(i, False) for i in range(CLOSE + 1)] + [(i, True) for i in range(NEW_DIR_CLOSE + 1)]


def _recovery_effects(at: int, new_dir: bool) -> list[str]:
    """The effects of a recovery of the save crashed at `at`, the listing's sync first."""
    w = crashed(at, new_dir=new_dir)
    n = len(w.fs.effects)
    recover(w.fs)
    return w.fs.effects[n:]


@pytest.mark.parametrize(("at", "new_dir"), RECOVERY_POINTS)
def test_a_crash_at_every_effect_of_recovery_then_recovery_again_resolves(at: int, new_dir: bool) -> None:
    """§10: "A crash during a recovery or a rollback leaves the journal `.open` with each file at pre or post, so the
    next recover-all finishes it"; §8.1 step 4: "A recovery cut before this step runs again from the start ... it is
    idempotent"; §14 "recovery cut at every step and run again". The restore temp's write is also cut part-way."""
    for k, label in enumerate(_recovery_effects(at, new_dir)):
        for cut in [None, 0, 3] if label.startswith("write ") else [None]:
            w = crashed(at, new_dir=new_dir)
            w.fs.inject(len(w.fs.effects) + k, crash=True, cut=cut)
            with pytest.raises(Crash):
                recover(w.fs)
            for state in (w.fs, *w.fs.power_loss_states()):
                check_disk(state, w)
                out = recover(state)
                check_recovered(state, w, out)


@pytest.mark.parametrize(("at", "new_dir"), [(CLOSE, False), (FIRST_REMOVE, False), (NEW_DIR_CLOSE, True)])
def test_an_io_error_at_every_effect_of_recovery_fails_with_every_file_whole_and_a_second_try_resolves(
    at: int, new_dir: bool
) -> None:
    """§8.1 step 3: "An I/O error during a restore stops that journal: it stays `.open`, with each file whole at pre
    or post, and counts as blocking (reason `failed`)"; Retry then undoes it. Also an effect that happened and then
    answered an error."""
    for k in range(len(_recovery_effects(at, new_dir)) - 1):  # the restore's effects, after the listing's sync
        for after in (False, True):
            w = crashed(at, new_dir=new_dir)
            with w.fs.open_dir(JD) as d:
                d.sync()  # the listing's (slice 3)
            n = len(w.fs.effects)
            w.fs.inject(n + k, crash=False, err=errno.EIO, after=after)
            out = recover(w.fs, listed=False)
            assert isinstance(out, Failed), (k, w.fs.effects[n + k], out)
            assert out.cause is Cause.IO_ERROR
            js = journals(w.fs)
            label = w.fs.effects[n + k]
            # `.resolved` only when the resolve's own rename took place (it answered the error after it) or its sync
            # failed: every file is then durably at pre already
            late = (label == f"rename {JD}:{NAME}.open,{NAME}.resolved" and after) or (
                label == f"sync {JD}:" and n + k == len(w.fs.effects) - 1
            )
            assert set(js) == ({State.RESOLVED} if late else {State.OPEN}), (k, label, js)
            check_disk(w.fs, w)
            check_every_power_loss(w.fs, w)
            check_recovered(w.fs, w, recover(w.fs))


def _hook(fs: MemFS, label: str, act: Callable[[], None]) -> None:
    done = False

    def hook(_: int, seen: str) -> None:
        nonlocal done
        if not done and seen == label:
            done = True
            act()

    fs.on_effect = hook


FOREIGN = b"; saved by an editor\n"


@pytest.mark.parametrize("at", [CLOSE, FIRST_REMOVE])
@pytest.mark.parametrize("victim", [0, 1, 2])
def test_an_outside_write_before_every_effect_of_recovery_is_never_deleted(at: int, victim: int) -> None:
    """`RecoveryNeverClobbersForeignBytes`, §6.4 "Every swap hashes what it displaced, in the save, the rollback and
    recovery alike": an editor's durable save into a ledger file right before any effect of the recovery: its bytes
    end in the file, or (the journal then stays `.open`) in a temp beside it, in every power-loss state too; a
    journal is resolved only with the outside save in its file."""
    for k in range(len(_recovery_effects(at, False))):
        w = crashed(at)
        t = w.targets[victim]
        n = len(w.fs.effects)

        def edit(i: int, _: str, w: World = w, at_effect: int = n + k, path: str = t.path) -> None:
            if i == at_effect:
                w.fs.outside_write(path, FOREIGN)

        w.fs.on_effect = edit
        out = recover(w.fs)
        w.fs.on_effect = None
        for state in (w.fs, *w.fs.power_loss_states()):
            where = {state.file(t.path), state.file(t.temp), state.file(t.rtemp), state.file(t.kept)}
            assert FOREIGN in where, (k, out, "the outside save was deleted")
            if state.file(t.path) != FOREIGN:
                assert State.OPEN in journals(state), (k, out)
        if isinstance(out, Resolved):
            assert w.fs.file(t.path) == FOREIGN
        else:
            assert isinstance(out, Stopped), (k, out)
            assert set(journals(w.fs)) == {State.OPEN}


def test_a_journal_at_post_is_restored_root_first_with_the_restore_temp_synced_before_its_swap() -> None:
    """§8.1 step 3 (ADR-0012 F-5, fidelity review 2 H1), step 4 (`resolve_before_restores_synced`)."""
    w = crashed(CLOSE)
    n = len(w.fs.effects)
    out = recover(w.fs)
    assert out == Resolved(NAME)
    e = w.fs.effects[n:]
    swaps = [x for x in e if x.startswith("swap ")]
    assert swaps == [
        "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean",
        "swap /w/L/inc:.a.bean.ft-restore-s1-j7.tmp,a.bean",
    ], "the root first"
    for folder, base in (("/w/L", "main.bean"), ("/w/L/inc", "a.bean")):
        rt = f".{base}.ft-restore-s1-j7.tmp"
        assert e.index(f"fsync {folder}:{rt}") < e.index(f"swap {folder}:{rt},{base}")
    assert e.index("rename /w/L:prices.bean,.prices.bean.ft-restore-s1-j7.tmp") < e.index(
        "remove /w/L:.prices.bean.ft-restore-s1-j7.tmp"
    )
    resolve = e.index(f"rename {JD}:{NAME}.open,{NAME}.resolved")
    last_ledger_sync = max(i for i, x in enumerate(e) if x.startswith("sync /w/L"))
    assert last_ledger_sync < resolve, "resolved only after every restore was synced"
    assert e[-1] == f"sync {JD}:"
    assert len(e) - 1 > resolve
    check_recovered(w.fs, w, out)


def test_a_power_loss_right_after_a_restore_swap_leaves_pre_or_post_never_torn() -> None:
    """§14 model finding H1."""
    probe = crashed(CLOSE)
    n = len(probe.fs.effects)
    recover(probe.fs)
    swap = n + probe.fs.effects[n:].index("swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean")
    w = crashed(CLOSE)
    w.fs.inject(swap + 1, crash=True)
    with pytest.raises(Crash):
        recover(w.fs)
    for state in w.fs.power_loss_states():
        assert state.file("/w/L/main.bean") in (w.targets[2].pre, w.targets[2].post)
        assert set(journals(state)) == {State.OPEN}


def test_the_restored_file_keeps_its_mode() -> None:
    w = world(mode=0o600)
    w.fs.inject(EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    assert isinstance(recover(w.fs), Resolved)
    assert w.fs.snapshot()["/w/L/main.bean"] == ("file", w.targets[2].pre, 0o600)


def test_a_created_folder_is_removed_when_empty_and_kept_when_it_holds_another_file() -> None:
    w = crashed(NEW_DIR_CLOSE, new_dir=True)
    assert isinstance(recover(w.fs), Resolved)
    assert "/w/L/y2026" not in w.fs.snapshot()
    w = crashed(NEW_DIR_CLOSE, new_dir=True)
    w.fs.outside_write("/w/L/y2026/.DS_Store", b"finder\n")
    assert isinstance(recover(w.fs), Resolved)
    assert w.fs.file("/w/L/y2026/.DS_Store") == b"finder\n"
    assert w.fs.file("/w/L/y2026/prices.bean") is None
    assert set(journals(w.fs)) == {State.RESOLVED}


def test_a_second_try_after_a_resolved_one_finds_nothing_to_do() -> None:
    w = crashed(CLOSE)
    assert isinstance(recover(w.fs), Resolved)
    n = len(w.fs.effects)
    assert recover(w.fs, listed=False) is None
    assert len(w.fs.effects) == n


def test_a_leftover_restore_temp_is_deleted_only_when_it_is_ours() -> None:
    """The refined temp rule (owner, 2026-10-29: "Refine the rule"): a restore temp cut off while it was copied from
    the backup (a prefix of the pre bytes) is deleted; anything else is kept and the journal stops."""
    w = crashed(CLOSE)
    t = w.targets[2]
    w.fs.outside_write(t.rtemp, (t.pre or b"")[:5])
    out = recover(w.fs)
    assert out == Resolved(NAME)
    check_recovered(w.fs, w, out)
    w = crashed(CLOSE)
    w.fs.outside_write(t.rtemp, FOREIGN)
    assert recover(w.fs) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert (w.fs.file(t.rtemp), w.fs.file(t.kept)) == (None, FOREIGN)


# ---- the displaced check at every restore swap and removal (§6.4; §14 "an outside write between a recovery's check
# and its restore swap, and during a created file's removal, is put back and the journal stops") -------------------


# ---- step 2: every cause stops the journal before anything is written ---------------------------------------------


def _stops_with_nothing_written(w: World, cause: Cause, path: str | None) -> None:
    n = len(w.fs.effects)
    before = w.fs.snapshot()
    out = recover(w.fs, listed=False)
    assert out == Stopped(cause, path), out
    assert w.fs.effects[n:] == [], "a stop at the checks writes nothing"
    assert w.fs.snapshot() == before
    assert set(journals(w.fs)) == {State.OPEN}


def test_a_file_holding_neither_pre_nor_post_stops_the_journal() -> None:
    w = crashed(CLOSE)
    w.fs.outside_write("/w/L/inc/a.bean", FOREIGN)
    _stops_with_nothing_written(w, Cause.FOREIGN, "/w/L/inc/a.bean")


def test_a_temp_cut_off_beside_a_file_at_pre_is_deleted_and_blocks_nothing() -> None:
    """`CutTempNeverBlocks`: §14 "a temp cut off at every offset while being written ... is deleted by recovery and
    blocks nothing"."""
    write = EFFECTS.index("write /w/L:.main.bean.ft-s1-j7.tmp")
    for cut in range(len(world().targets[2].post)):
        w = crashed(write, cut=cut)
        out = recover(w.fs)
        assert out == Resolved(NAME)
        check_recovered(w.fs, w, out)


def test_a_backup_that_does_not_hold_the_pre_bytes_stops_the_journal() -> None:
    w = crashed(CLOSE)
    w.fs.outside_write(f"{SAVE_DIR}/2-main.bean", b"bit rot\n")
    _stops_with_nothing_written(w, Cause.BACKUP_BAD, "/w/L/main.bean")


def test_a_folder_moved_away_or_replaced_by_a_link_stops_the_journal_and_nothing_is_written_through_it() -> None:
    """§6.3 path guard, INV-32, BL-0103: "a folder moved or replaced by a link after the crash makes the journal
    unrecoverable and nothing is written through the link"."""
    w = crashed(CLOSE)
    w.fs.outside_move("/w/L", "/w/L-moved")
    _stops_with_nothing_written(w, Cause.UNREACHABLE, "/w/L/inc/a.bean")
    w.fs.outside_mkdirs("/w/elsewhere/inc")
    w.fs.outside_write("/w/elsewhere/main.bean", b"not ours\n")
    w.fs.outside_symlink("/w/L", "/w/elsewhere")
    _stops_with_nothing_written(w, Cause.PATH_MOVED, "/w/L/inc/a.bean")
    assert w.fs.file("/w/elsewhere/main.bean") == b"not ours\n"


def test_a_folder_moved_between_the_checks_and_the_restore_stops_and_writes_nothing_through_the_link() -> None:
    w = crashed(CLOSE)
    w.fs.outside_mkdirs("/w/elsewhere/inc")

    def swap_folder() -> None:
        w.fs.outside_move("/w/L", "/w/L-moved")
        w.fs.outside_symlink("/w/L", "/w/elsewhere")

    _hook(w.fs, "create /w/L:.main.bean.ft-restore-s1-j7.tmp", swap_folder)
    out = recover(w.fs)
    assert isinstance(out, Stopped)
    assert out.cause is Cause.PATH_MOVED
    assert [p for p in w.fs.snapshot() if p.startswith("/w/elsewhere/") and ".ft-" in p] == []
    assert set(journals(w.fs)) == {State.OPEN}


def test_a_hard_linked_file_at_post_stops_the_journal() -> None:
    """ADR-0013 amendment (owner, 2026-10-29, "Refuse to save it"), applied to the restore swap: it would give the
    name the pre bytes while the other link keeps the post bytes, unseen (probed on APFS)."""
    w = crashed(CLOSE)
    w.fs.outside_mkdirs("/w/Backup")
    w.fs.outside_link("/w/L/main.bean", "/w/Backup/copy.bean")
    _stops_with_nothing_written(w, Cause.HARD_LINKED, "/w/L/main.bean")


def test_a_locked_file_at_post_stops_the_journal() -> None:
    w = crashed(CLOSE)
    w.fs.outside_lock("/w/L/main.bean")
    _stops_with_nothing_written(w, Cause.LOCKED, "/w/L/main.bean")


@pytest.mark.parametrize(
    ("where", "right", "path"),
    [
        ("/w/L", Right.CHANGE_ENTRIES, "/w/L/prices.bean"),
        ("/w/L/main.bean", Right.DELETE, "/w/L/main.bean"),
        ("/w/L", Right.DELETE, "/w/L/prices.bean"),
    ],
)
def test_permissions_that_would_block_the_restore_stop_the_journal_before_anything_is_written(
    where: str, right: Right, path: str
) -> None:
    w = crashed(CLOSE)
    w.fs.outside_deny(where, right)
    _stops_with_nothing_written(w, Cause.PERMISSION, path)


@pytest.mark.parametrize("bad", [b"", b"{}\n", b"garbage", "newer"])
def test_an_unreadable_or_newer_record_stops_the_journal(bad: bytes | str) -> None:
    """§8.1 "A journal whose file list cannot be read blocks every open"; ADR-0012: an older app refuses a newer
    journal (S0045 verifier round 3, F-2)."""
    w = crashed(CLOSE)
    path = f"{JD}/{NAME}.open"
    data = w.fs.file(path)
    assert data is not None
    w.fs.outside_write(path, data.replace(b'"v":1', b'"v":2') if bad == "newer" else bad)  # type: ignore[arg-type]
    _stops_with_nothing_written(w, Cause.UNREADABLE, None)


def test_a_lock_held_by_another_process_leaves_the_journal_untouched_with_reason_failed() -> None:
    """§8.1 step 1, §14 "a ledger-set lock held by a process outside the engine lock leaves its journal untouched and
    blocking with reason `failed`"."""
    w = crashed(CLOSE)
    locks = MemLocks()
    locks.outside_hold("/w/l/main.bean")
    n = len(w.fs.effects)
    out = recover(w.fs, locks, listed=False)
    assert out == Failed(Cause.LOCK_HELD, "/w/L/main.bean")
    assert w.fs.effects[n:] == []
    assert locks.held == set(), "the locks it took are released"
    locks.outside_release("/w/l/main.bean")
    assert isinstance(recover(w.fs, locks), Resolved)
    assert locks.held == set()


def test_the_locks_are_tried_in_path_key_order_and_released_after_the_try() -> None:
    w = crashed(CLOSE)
    locks = MemLocks()
    assert isinstance(recover(w.fs, locks), Resolved)
    assert locks.taken == sorted(f.lower() for f in ("/w/L/inc/a.bean", "/w/L/prices.bean", "/w/L/main.bean"))
    assert locks.held == set()


def test_a_journal_named_differently_from_its_record_stops() -> None:
    """The record's own name must be the file's: a record copied under another name is not that save's."""
    w = crashed(CLOSE)
    data = w.fs.file(f"{JD}/{NAME}.open")
    assert data is not None
    w.fs.outside_write(f"{JD}/s1-j8.open", data)
    n = len(w.fs.effects)
    out = journal.parse_file_name("s1-j8.open")
    assert out is not None
    from ledger_engine.ledger.restore import recover_journal

    assert recover_journal(w.fs, MemLocks(), JD, "s1-j8") == Stopped(Cause.UNREADABLE, None)
    assert w.fs.effects[n:] == []


# ---- names as the volume compares them (BL-0151 slice 2 note; S0060 red team r2 overflow 3) ----------------------


NFC_DIR = "/w/L/Caf\u00e9"


def _nfc_world() -> World:
    from tests.ledger.worlds import Target, commit_for

    w = world(stores_nfd=True)
    targets = (Target(f"{NFC_DIR}/x.bean", None, b"x\n"), w.targets[2])
    return World(w.fs, commit_for(targets), targets, w.fs.snapshot(), (NFC_DIR,))


def test_a_folder_the_save_makes_under_an_nfc_name_on_a_volume_storing_nfd_passes_the_path_guard() -> None:
    """HFS+ stores every name in NFD whatever it was given (probed), so the folder's handle answers NFD while the
    journal records the name as written (NFC): the guard compares as the volume does."""
    w = _nfc_world()
    out = durable_commit(w.fs, w.commit)
    assert type(out).__name__ == "Landed", out
    assert w.fs.names_in("/w/L") == sorted(["Cafe\u0301", "inc", "main.bean", "other.bean"])


def test_a_crash_at_every_effect_of_an_nfc_save_on_a_volume_storing_nfd_is_recovered() -> None:
    probe = _nfc_world()
    durable_commit(probe.fs, probe.commit)
    for at in range(len(probe.fs.effects)):
        w = _nfc_world()
        w.fs.inject(at, crash=True)
        with pytest.raises(Crash):
            durable_commit(w.fs, w.commit)
        for state in (w.fs, *w.fs.power_loss_states()):
            out = recover(state)
            assert not isinstance(out, Stopped | Failed), (at, out)
            assert state.file(f"{NFC_DIR}/x.bean") in (None, b"x\n")


# ---- S0061 mutants: the checks each get a test that must not pass without them ------------------------------------


def test_a_read_only_folder_beside_a_cut_temp_stops_before_anything_is_written() -> None:
    """The temp must be removed, so its folder must allow it: stopped at the checks, not failed half-way."""
    write = EFFECTS.index("write /w/L/inc:.a.bean.ft-s1-j7.tmp")  # the first file: every file still at pre
    w = crashed(write, cut=3)
    w.fs.outside_deny("/w/L/inc", Right.CHANGE_ENTRIES)
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/inc/a.bean")


def test_a_backup_folder_moved_or_replaced_by_a_link_stops_before_anything_is_written() -> None:
    w = crashed(FIRST_REMOVE)  # the temps are still there: nothing of them may go before the stop
    w.fs.outside_move("/w/local/backups", "/w/local/backups-moved")
    w.fs.outside_symlink("/w/local/backups", "/w/local/backups-moved")
    _stops_with_nothing_written(w, Cause.BACKUP_BAD, "/w/L/inc/a.bean")


def test_a_backup_changed_after_the_checks_is_hashed_again_and_stops_the_restore() -> None:
    """ADR-0012 F-1 at the restore itself: the bytes swapped in are the pre bytes, never a backup changed since."""
    w = crashed(CLOSE)
    first = True

    def rot(_: int, label: str) -> None:
        nonlocal first
        if first:
            first = False
            w.fs.outside_write(f"{SAVE_DIR}/0-a.bean", b"bit rot\n")  # after the checks, before a.bean's restore

    w.fs.on_effect = rot
    out = recover(w.fs, listed=False)
    assert out == Stopped(Cause.BACKUP_BAD, "/w/L/inc/a.bean")
    assert w.fs.file("/w/L/inc/a.bean") == w.targets[0].post
    assert w.fs.file("/w/L/main.bean") == w.targets[2].pre  # the root first: already back
    assert set(journals(w.fs)) == {State.OPEN}


def test_an_io_error_while_checking_stops_the_journal_with_nothing_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """§8.1 step 2: "Any failure ... the journal stays and counts as unrecoverable"."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    real = MemDir.stat

    def failing(self: MemDir, name: str) -> object:
        if name == "main.bean":
            raise OSError(errno.EIO, "injected stat error")
        return real(self, name)

    monkeypatch.setattr(MemDir, "stat", failing)
    n = len(w.fs.effects)
    out = recover(w.fs, listed=False)
    assert out == Stopped(Cause.IO_ERROR, "/w/L/main.bean")
    assert w.fs.effects[n:] == []
    assert set(journals(w.fs)) == {State.OPEN}


def test_a_folder_renamed_in_case_only_counts_as_moved() -> None:
    """The path guard compares as the volume does for normalization, exactly for case: fails closed."""
    w = crashed(CLOSE)
    w.fs.outside_rename_case("/w/L", "l")
    _stops_with_nothing_written(w, Cause.PATH_MOVED, "/w/L/inc/a.bean")


# ---- the kept name at the restore (owner, 2026-10-29: "Yes"; ADR-0013 amendment; S0061 round 1) -------------------


def _delete(w: World, path: str) -> None:
    """The user deletes a file (Finder, the shell)."""
    d, name = w.fs._parent_and_name(path)  # pyright: ignore[reportPrivateUsage]
    del d.entries[name.lower()]


@pytest.mark.parametrize("by_rename", [False, True])
def test_an_outside_write_between_the_check_and_the_restore_swap_is_moved_to_the_kept_name(by_rename: bool) -> None:
    """§6.4 as decided: never swapped back; the outside save goes at once to `main.bean.kept-s1-j7`, the folder is
    synced, the journal stops; every later try stops on the kept copy until the user deletes it."""
    w = crashed(CLOSE)
    t = w.targets[2]
    _hook(
        w.fs,
        "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean",
        lambda: w.fs.outside_write(t.path, FOREIGN, new_inode=by_rename),
    )
    out = recover(w.fs)
    assert out == Stopped(Cause.DISPLACED, t.path)
    assert (w.fs.file(t.path), w.fs.file(t.kept), w.fs.file(t.rtemp)) == (t.pre, FOREIGN, None)
    assert w.fs.effects[-2:] == ["rename /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean.kept-s1-j7", "sync /w/L:"]
    assert w.fs.effects.count("swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean") == 1, "never swapped back"
    assert set(journals(w.fs)) == {State.OPEN}
    for state in w.fs.power_loss_states():
        assert FOREIGN in {state.file(t.path), state.file(t.rtemp), state.file(t.kept)}
        assert set(journals(state)) == {State.OPEN}
    assert recover(w.fs) == Stopped(Cause.KEPT_COPY, t.path)
    _delete(w, t.kept)
    check_recovered(w.fs, w, recover(w.fs))


def test_an_outside_write_during_a_created_files_removal_is_moved_to_the_kept_name() -> None:
    w = crashed(CLOSE)
    t = w.targets[1]
    _hook(
        w.fs, "rename /w/L:prices.bean,.prices.bean.ft-restore-s1-j7.tmp", lambda: w.fs.outside_write(t.path, FOREIGN)
    )
    out = recover(w.fs)
    assert out == Stopped(Cause.DISPLACED, t.path)
    assert (w.fs.file(t.path), w.fs.file(t.kept)) == (None, FOREIGN)
    assert w.fs.effects[-1] == "sync /w/L:"
    for state in w.fs.power_loss_states():
        assert FOREIGN in (state.file(t.path), state.file(t.rtemp), state.file(t.kept))
        assert set(journals(state)) == {State.OPEN}


def test_A1_an_outside_save_with_our_post_bytes_at_the_restore_swap_is_ours_and_a_later_one_is_never_deleted() -> None:
    """S0061 red team H2: outside save 1 by rename holds our post bytes (a sync tool writing the same content): the
    swap displaces it and it is ours by bytes (deleting it loses nothing); outside save 2 replaces main.bean right
    after: it is an edit made after the restore, kept in the file."""
    w = crashed(CLOSE)
    t = w.targets[2]
    second = b"; outside 2: the user's real edit\n"

    def on(_: int, label: str) -> None:
        if label == "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean":
            w.fs.outside_write(t.path, t.post, new_inode=True)
        if label == "remove /w/L:.main.bean.ft-restore-s1-j7.tmp":
            w.fs.on_effect = None
            w.fs.outside_write(t.path, second, new_inode=True)

    w.fs.on_effect = on
    out = recover(w.fs)
    assert out == Resolved(NAME)
    assert w.fs.file(t.path) == second
    for state in w.fs.power_loss_states():
        assert state.file(t.path) == second


def test_a_temp_holding_an_outside_save_beside_a_file_at_post_is_moved_to_the_kept_name_and_stops() -> None:
    """§6.4 review 6 P1 (the temp rule's stop), model finding F2; S0061 red team M1: moved out of the temp rule's
    reach at once, so a later revert of the file cannot get it deleted."""
    w = crashed(FIRST_REMOVE)
    t = w.targets[2]
    w.fs.outside_write(t.temp, FOREIGN)
    n = len(w.fs.effects)
    out = recover(w.fs, listed=False)
    assert out == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.effects[n:] == ["rename /w/L:.main.bean.ft-s1-j7.tmp,main.bean.kept-s1-j7", "sync /w/L:"]
    assert (w.fs.file(t.temp), w.fs.file(t.kept)) == (None, FOREIGN)
    w.fs.outside_write(t.path, t.pre or b"")  # the user puts the file back to its old version
    assert recover(w.fs) == Stopped(Cause.KEPT_COPY, t.path)
    assert w.fs.file(t.kept) == FOREIGN


def test_an_outside_write_into_a_save_temp_after_the_checks_is_moved_to_the_kept_name() -> None:
    """S0060 red team finding 2 for recovery: re-checked right before its removal."""
    w = crashed(FIRST_REMOVE)
    first, t = w.targets[0], w.targets[2]
    _hook(w.fs, f"remove {first.folder}:.{first.base}.ft-s1-j7.tmp", lambda: w.fs.outside_write(t.temp, FOREIGN))
    out = recover(w.fs, listed=False)
    assert out == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert (w.fs.file(t.temp), w.fs.file(t.kept)) == (None, FOREIGN)
    assert set(journals(w.fs)) == {State.OPEN}


def test_an_outside_save_by_rename_with_our_post_bytes_at_the_restore_swap_is_ours() -> None:
    """Owner, 2026-10-29 (step 2 of the kept-name design): byte-identical to bytes we hold, so deleting it loses
    nothing; the restore goes on and resolves."""
    w = crashed(CLOSE)
    t = w.targets[2]
    _hook(
        w.fs,
        "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean",
        lambda: w.fs.outside_write(t.path, t.post, new_inode=True),
    )
    out = recover(w.fs)
    assert out == Resolved(NAME)
    check_recovered(w.fs, w, out)


def test_a_kept_name_already_taken_uses_the_next_number_and_still_stops() -> None:
    from ledger_engine.ledger.handles import keep

    w = crashed(CLOSE)
    w.fs.outside_write("/w/L/x.tmp", FOREIGN)
    w.fs.outside_write("/w/L/main.bean.kept-s1-j7", b"an earlier kept copy\n")
    with w.fs.open_dir("/w/L") as h:
        assert keep(h, "x.tmp", "main.bean", "s1-j7") == "main.bean.kept-s1-j7-2"
    assert w.fs.file("/w/L/main.bean.kept-s1-j7-2") == FOREIGN
    assert recover(w.fs) == Stopped(Cause.KEPT_COPY, "/w/L/main.bean")


# ---- S0061 round 1: the verifier's V1, V2, L3, L4 and the red team's L1, L3, L4 ---------------------------------


def test_a_folder_the_save_made_that_cannot_be_removed_stops_before_anything_is_written() -> None:
    """Verifier V1: the folder's own deletion denied (an ACL), or entry changes denied in its parent: stopped at the
    checks with `permission`, not failed for ever after the files were restored."""
    from tests.ledger.worlds import commit_for

    base = world(new_dir=True)
    only = (base.targets[1],)  # the created file in the created folder: nothing else in /w/L is written
    w = World(base.fs, commit_for(only), only, base.before, base.new_dirs)
    probe = World(world(new_dir=True).fs, w.commit, only, base.before, base.new_dirs)
    durable_commit(probe.fs, probe.commit)
    w.fs.inject(probe.fs.effects.index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_deny("/w/L", Right.CHANGE_ENTRIES)  # the parent made read-only (chmod 0555), as in V1's repro
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/y2026")


def test_a_non_file_at_the_restore_temp_name_is_kept_and_stops_before_anything_else_is_written() -> None:
    """Verifier V2: a folder where a leftover restore temp would be is not ours: moved to the kept name, stop."""
    w = crashed(CLOSE)
    t = w.targets[2]
    w.fs.outside_mkdirs(t.rtemp)
    n = len(w.fs.effects)
    assert recover(w.fs, listed=False) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.effects[n:] == ["rename /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean.kept-s1-j7", "sync /w/L:"]
    assert w.fs.snapshot()[t.kept] == ("dir",)


def test_a_leftover_restore_temp_beside_a_file_at_pre_in_a_read_only_folder_stops_before_anything_is_written() -> None:
    """Red team L4: its removal needs the folder's entry-change right."""
    w = crashed(EFFECTS.index("create /w/L/inc:.a.bean.ft-s1-j7.tmp"))  # no temp of the save there
    w.fs.outside_write("/w/L/inc/.a.bean.ft-restore-s1-j7.tmp", b"2026-01")  # a cut copy of the pre bytes: ours
    w.fs.outside_deny("/w/L/inc", Right.CHANGE_ENTRIES)
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/inc/a.bean")


def test_a_file_at_post_whose_extended_attributes_cannot_be_read_stops_before_anything_is_written() -> None:
    """Red team L1: the restore temp copies them, so it is checked at step 2."""
    w = crashed(FIRST_REMOVE)
    w.fs.outside_deny("/w/L/main.bean", Right.READ_XATTRS)
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/main.bean")


def test_the_restored_file_keeps_its_extended_attributes() -> None:
    """Verifier L3 (mutant m09): the restore temp is made `like` the file it replaces."""
    w = world()
    w.fs.outside_setxattr("/w/L/main.bean", "com.apple.metadata:_kMDItemUserTags", b"tag")
    w.fs.inject(CLOSE, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    assert isinstance(recover(w.fs), Resolved)
    assert w.fs.xattrs("/w/L/main.bean") == {"com.apple.metadata:_kMDItemUserTags": b"tag"}


def test_a_file_at_post_on_a_volume_without_the_swap_stops_before_anything_is_written() -> None:
    """Verifier L4 (mutant m21): the owner's no-swap rule holds for the restore swap too."""
    w = crashed(CLOSE)
    w.fs.outside_no_swap("/w/L")
    _stops_with_nothing_written(w, Cause.VOLUME_NO_SWAP, "/w/L/prices.bean")


def test_a_file_that_vanishes_between_its_stat_and_its_read_is_foreign(monkeypatch: pytest.MonkeyPatch) -> None:
    """Red team L3: one `stat` for the kind, then the bytes; a read that finds nothing is a change, never a crash of
    the job."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    real = MemDir.read

    def vanishing(self: MemDir, name: str) -> bytes | None:
        return None if name == "main.bean" else real(self, name)

    monkeypatch.setattr(MemDir, "read", vanishing)
    n = len(w.fs.effects)
    assert recover(w.fs, listed=False) == Stopped(Cause.FOREIGN, "/w/L/main.bean")
    assert w.fs.effects[n:] == []


def test_a_folder_the_save_made_whose_own_deletion_is_denied_stops_before_anything_is_written() -> None:
    """Verifier V1, second half: an ACL denying the folder's deletion (the created file not written yet)."""
    w = crashed(NEW_DIR_EFFECTS.index("create /w/L/y2026:.prices.bean.ft-s1-j7.tmp"), new_dir=True)
    assert "/w/L/y2026" in w.fs.snapshot()
    w.fs.outside_deny("/w/L/y2026", Right.DELETE)
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/y2026")


def test_a_created_file_that_vanishes_between_its_stat_and_its_read_is_foreign_not_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red team L3 for a created file: "absent" would be its pre state, and the try would resolve over a file that is
    there."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    real = MemDir.read

    def vanishing(self: MemDir, name: str) -> bytes | None:
        return None if name == "prices.bean" else real(self, name)

    monkeypatch.setattr(MemDir, "read", vanishing)
    assert recover(w.fs, listed=False) == Stopped(Cause.FOREIGN, "/w/L/prices.bean")


# ---- S0061 round 2 (owner, 2026-10-29: "Refine the rule"; the red team's High; verifier F1, F3) ------------------


def _all_bytes(fs: MemFS) -> set[bytes]:
    return {e[1] for e in fs.snapshot().values() if e[0] == "file"}  # type: ignore[misc]


def test_F2_an_outside_file_at_a_created_path_with_our_post_bytes_is_never_deleted() -> None:
    """Red team r2 H1: another program creates prices.bean first, with exactly our post bytes; the exclusive rename
    answers EEXIST; the process dies at every later effect (the rollback included); recovery sees our temp still
    there, so the file is not ours: it stops (`foreign`) and deletes nothing, in every power-loss state too."""
    probe = world()
    durable_commit(probe.fs, probe.commit)
    rename = probe.fs.effects.index("rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean")
    t = probe.targets[1]
    w0 = world()
    w0.fs.on_effect = lambda i, _: w0.fs.outside_write(t.path, t.post) if i == rename else None
    durable_commit(w0.fs, w0.commit)
    total = len(w0.fs.effects)
    for crash_at in range(rename + 1, total + 1):
        w = world()
        w.fs.on_effect = lambda i, _, w=w: w.fs.outside_write(t.path, t.post) if i == rename else None
        if crash_at < total:
            w.fs.inject(crash_at, crash=True)
            with pytest.raises(Crash):
                durable_commit(w.fs, w.commit)
        else:
            durable_commit(w.fs, w.commit)
        w.fs.on_effect = None
        for state in (w.fs, *w.fs.power_loss_states()):
            for _ in range(2):
                recover(state)
                assert state.file(t.path) == t.post, (crash_at, "the other program's prices.bean was deleted")


def test_the_rollback_of_a_skipped_created_path_resolves_and_keeps_the_outside_file() -> None:
    """With no failure in the rollback, the journal resolves, the other program's file stays, and our temp goes
    only after `.resolved` is durable."""
    probe = world()
    durable_commit(probe.fs, probe.commit)
    rename = probe.fs.effects.index("rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean")
    w = world()
    t = w.targets[1]
    w.fs.on_effect = lambda i, _: w.fs.outside_write(t.path, t.post) if i == rename else None
    out = durable_commit(w.fs, w.commit)
    assert type(out).__name__ == "NotSaved"
    assert w.fs.file(t.path) == t.post
    assert w.fs.file(t.temp) is None
    e = w.fs.effects
    assert e.index(f"rename {JD}:{NAME}.open,{NAME}.resolved") < e.index("remove /w/L:.prices.bean.ft-s1-j7.tmp")


def test_F4_an_outside_save_displaced_by_a_crashed_save_survives_a_revert_before_the_next_start() -> None:
    """Red team r2 M2: the editor's in-place save is displaced into our temp; the process dies before the keep; the
    user puts main.bean back to its old version; recovery keeps the temp (not a cut write of ours), never deletes it."""
    probe = world()
    durable_commit(probe.fs, probe.commit)
    swap = probe.fs.effects.index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    w = world()
    t = w.targets[2]
    w.fs.on_effect = lambda i, _: w.fs.outside_write(t.path, FOREIGN) if i == swap else None
    w.fs.inject(swap + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.on_effect = None
    w.fs.outside_write(t.path, t.pre or b"", new_inode=True)  # git checkout while Finzytrack is closed
    assert recover(w.fs) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.file(t.kept) == FOREIGN
    assert recover(w.fs) == Stopped(Cause.KEPT_COPY, t.path)


def test_F5_an_in_place_write_through_a_name_our_unsynced_swap_made_survives_every_power_loss() -> None:
    """Red team r2 M1, verifier r2 F2: an editor writes a.bean in place while our swap is unsynced; a power loss drops
    the swap: the editor's bytes sit at our temp beside a file at pre; recovery keeps them."""
    swap = EFFECTS.index("swap /w/L/inc:.a.bean.ft-s1-j7.tmp,a.bean")
    w = crashed(EFFECTS.index("sync /w/L/inc:", swap))  # dies at the folder sync right after the swap
    t = w.targets[0]
    w.fs.outside_write(t.path, FOREIGN)
    seen = 0
    for state in w.fs.power_loss_states():
        if state.file(t.temp) != FOREIGN:
            continue
        seen += 1
    for state in w.fs.power_loss_states():
        assert FOREIGN in _all_bytes(state), "the editor's bytes lost their only name (our temp's entry not durable)"
        recover(state)
        assert FOREIGN in _all_bytes(state), "the editor's bytes were deleted"
    assert seen >= 1, "the power-loss state the attack needs was not reached"


def test_a_kept_copy_of_an_earlier_session_never_stops_a_later_sessions_save() -> None:
    """Verifier r2 F1: jobs restart at 1 each session and a kept copy outlives its journal (the user chose Keep). A
    save of session s0, job j7, keeps an editor's save beside main.bean; a later session's save of that file with the
    same job number j7 is not refused because of it."""
    from dataclasses import replace

    from tests.ledger.worlds import commit_for

    w = world()
    first = replace(w.commit, session="s0")
    _hook(w.fs, "swap /w/L:.main.bean.ft-s0-j7.tmp,main.bean", lambda: w.fs.outside_write("/w/L/main.bean", FOREIGN))
    durable_commit(w.fs, first)
    w.fs.on_effect = None
    assert w.fs.file("/w/L/main.bean.kept-s0-j7") == FOREIGN
    for name in w.fs.names_in(JD):  # the user chose "Keep the files as they are now": the journal is no longer open
        d, n = w.fs._parent_and_name(f"{JD}/{name}")  # pyright: ignore[reportPrivateUsage]
        del d.entries[n.lower()]
    now = tuple(Target(t.path, w.fs.file(t.path), t.post + b"; again\n") for t in w.targets)
    out = durable_commit(w.fs, commit_for(now))  # session s1, job j7
    assert type(out).__name__ == "Landed", out
    assert w.fs.file("/w/L/main.bean.kept-s0-j7") == FOREIGN


def test_a_displaced_file_holding_the_pre_bytes_at_the_restore_swap_is_ours() -> None:
    """@regression-guard (S0061 verifier r2 F3: the code already held; it kills the mutant
    settle_post_only): an outside program wrote the old bytes into main.bean in place just
    before our restore swap: equal to bytes we hold, so deleting it loses nothing; the try resolves."""
    w = crashed(CLOSE)
    t = w.targets[2]
    _hook(w.fs, "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean", lambda: w.fs.outside_write(t.path, t.pre or b""))
    out = recover(w.fs)
    assert out == Resolved(NAME)
    check_recovered(w.fs, w, out)


def test_a_created_folder_holding_another_entry_is_not_checked_for_removal() -> None:
    """Red team r2 L5: no rmdir would run there, so a denied deletion does not stop the try."""
    w = crashed(NEW_DIR_CLOSE, new_dir=True)
    w.fs.outside_write("/w/L/y2026/notes.txt", b"the user's\n")
    w.fs.outside_deny("/w/L/y2026", Right.DELETE)
    w.fs.outside_deny("/w/L/y2026/prices.bean", Right.DELETE)
    # the created file itself cannot be removed either: that is a real stop, at the file
    assert recover(w.fs) == Stopped(Cause.PERMISSION, "/w/L/y2026/prices.bean")
    w = crashed(NEW_DIR_EFFECTS.index("create /w/L/y2026:.prices.bean.ft-s1-j7.tmp"), new_dir=True)
    w.fs.outside_write("/w/L/y2026/notes.txt", b"the user's\n")
    w.fs.outside_deny("/w/L/y2026", Right.DELETE)
    out = recover(w.fs)
    assert out == Resolved(NAME)
    assert w.fs.file("/w/L/y2026/notes.txt") == b"the user's\n"


def test_an_outside_file_at_a_created_path_made_before_our_temp_is_never_deleted_after_a_power_loss() -> None:
    """Red team r2 H1, the other order: the other program's file (our post bytes) appears before our temp's create;
    the save makes the temp's entry durable before its exclusive rename, so no power loss can leave the file
    without the temp that says it is not ours."""
    probe = world()
    durable_commit(probe.fs, probe.commit)
    create = probe.fs.effects.index("create /w/L:.prices.bean.ft-s1-j7.tmp")
    rename = probe.fs.effects.index("rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean")
    t = probe.targets[1]
    w = world()
    w.fs.on_effect = lambda i, _: w.fs.outside_write(t.path, t.post) if i == create else None
    w.fs.inject(rename + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.on_effect = None
    for state in (w.fs, *w.fs.power_loss_states()):
        recover(state)
        assert state.file(t.path) == t.post, "the other program's prices.bean was deleted"


def test_a_post_copy_that_does_not_hash_to_the_post_bytes_is_never_used_for_the_temp_rule() -> None:
    """A damaged post copy cannot make an outside save look like a cut write of ours."""
    w = crashed(EFFECTS.index("create /w/L/inc:.a.bean.ft-s1-j7.tmp"))  # a.bean at pre, no temp
    t = w.targets[0]
    w.fs.outside_write(f"{SAVE_DIR}/0.post-a.bean", FOREIGN + b"and more\n")  # damaged
    w.fs.outside_write(t.temp, FOREIGN)  # a prefix of the damaged copy, not of the post bytes
    assert recover(w.fs) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.file(t.kept) == FOREIGN
