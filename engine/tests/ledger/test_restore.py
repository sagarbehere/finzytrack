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
    if ".ft-j7.tmp" in label:
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
            where = {state.file(t.path), state.file(t.temp), state.file(t.rtemp)}
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
        "swap /w/L:.main.bean.ft-restore-j7.tmp,main.bean",
        "swap /w/L/inc:.a.bean.ft-restore-j7.tmp,a.bean",
    ], "the root first"
    for folder, base in (("/w/L", "main.bean"), ("/w/L/inc", "a.bean")):
        rt = f".{base}.ft-restore-j7.tmp"
        assert e.index(f"fsync {folder}:{rt}") < e.index(f"swap {folder}:{rt},{base}")
    assert e.index("rename /w/L:prices.bean,.prices.bean.ft-restore-j7.tmp") < e.index(
        "remove /w/L:.prices.bean.ft-restore-j7.tmp"
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
    swap = n + probe.fs.effects[n:].index("swap /w/L:.main.bean.ft-restore-j7.tmp,main.bean")
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


def test_a_leftover_restore_temp_is_deleted_without_a_check() -> None:
    """§6.4: rollback and recovery "delete leftover restore temps without a check"."""
    w = crashed(CLOSE)
    w.fs.outside_write("/w/L/.main.bean.ft-restore-j7.tmp", b"cut off\n")
    assert isinstance(recover(w.fs), Resolved)
    assert w.fs.file("/w/L/.main.bean.ft-restore-j7.tmp") is None
    check_recovered(w.fs, w, Resolved(NAME))


# ---- the displaced check at every restore swap and removal (§6.4; §14 "an outside write between a recovery's check
# and its restore swap, and during a created file's removal, is put back and the journal stops") -------------------


@pytest.mark.parametrize("by_rename", [False, True])
def test_an_outside_write_between_the_check_and_the_restore_swap_is_put_back_synced_and_the_journal_stops(
    by_rename: bool,
) -> None:
    w = crashed(CLOSE)
    _hook(
        w.fs,
        "swap /w/L:.main.bean.ft-restore-j7.tmp,main.bean",
        lambda: w.fs.outside_write("/w/L/main.bean", FOREIGN, new_inode=by_rename),
    )
    out = recover(w.fs)
    assert out == Stopped(Cause.DISPLACED, "/w/L/main.bean")
    assert w.fs.file("/w/L/main.bean") == FOREIGN
    assert w.fs.effects[-1] == "sync /w/L:", "the swap-back synced at once (model finding F3)"
    assert set(journals(w.fs)) == {State.OPEN}
    for state in w.fs.power_loss_states():
        assert state.file("/w/L/main.bean") == FOREIGN
        assert set(journals(state)) == {State.OPEN}
    assert recover(w.fs) == Stopped(Cause.FOREIGN, "/w/L/main.bean"), "the next try stops too, both versions kept"


def test_an_outside_write_during_a_created_files_removal_is_put_back_and_the_journal_stops() -> None:
    w = crashed(CLOSE)
    _hook(
        w.fs,
        "rename /w/L:prices.bean,.prices.bean.ft-restore-j7.tmp",
        lambda: w.fs.outside_write("/w/L/prices.bean", FOREIGN),
    )
    out = recover(w.fs)
    assert out == Stopped(Cause.DISPLACED, "/w/L/prices.bean")
    assert w.fs.file("/w/L/prices.bean") == FOREIGN
    assert w.fs.effects[-1] == "sync /w/L:"
    for state in w.fs.power_loss_states():
        assert FOREIGN in (state.file("/w/L/prices.bean"), state.file("/w/L/.prices.bean.ft-restore-j7.tmp"))
        assert set(journals(state)) == {State.OPEN}


def test_a_second_outside_save_before_the_restore_swap_back_is_kept_and_nothing_is_swapped_again() -> None:
    """S0060 red team r2 M1 for the restore: both outside saves are kept, one in the file, one in the restore temp."""
    w = crashed(CLOSE)
    first, second = b"; outside 1\n", b"; outside 2\n"
    swaps = 0

    def on(_: int, label: str) -> None:
        nonlocal swaps
        if label == "swap /w/L:.main.bean.ft-restore-j7.tmp,main.bean":
            swaps += 1
            w.fs.outside_write("/w/L/main.bean", first if swaps == 1 else second, new_inode=True)

    w.fs.on_effect = on
    out = recover(w.fs)
    assert out == Stopped(Cause.DISPLACED, "/w/L/main.bean")
    assert swaps == 2
    assert {w.fs.file("/w/L/main.bean"), w.fs.file("/w/L/.main.bean.ft-restore-j7.tmp")} == {first, second}
    for state in w.fs.power_loss_states():
        assert {state.file("/w/L/main.bean"), state.file("/w/L/.main.bean.ft-restore-j7.tmp")} == {first, second}


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


def test_a_temp_holding_an_outside_save_beside_a_file_at_post_stops_the_journal() -> None:
    """§6.4 review 6 P1, model finding F2."""
    w = crashed(FIRST_REMOVE)
    w.fs.outside_write(w.targets[2].temp, FOREIGN)
    _stops_with_nothing_written(w, Cause.OUTSIDE_SAVE_IN_TEMP, "/w/L/main.bean")
    assert w.fs.file(w.targets[2].temp) == FOREIGN


def test_a_temp_cut_off_beside_a_file_at_pre_is_deleted_and_blocks_nothing() -> None:
    """`CutTempNeverBlocks`: §14 "a temp cut off at every offset while being written ... is deleted by recovery and
    blocks nothing"."""
    write = EFFECTS.index("write /w/L:.main.bean.ft-j7.tmp")
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

    _hook(w.fs, "create /w/L:.main.bean.ft-restore-j7.tmp", swap_folder)
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
    write = EFFECTS.index("write /w/L/inc:.a.bean.ft-j7.tmp")  # the first file: every file still at pre
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


def test_an_outside_write_into_a_save_temp_after_the_checks_keeps_it_and_stops() -> None:
    """S0060 red team finding 2 for recovery: the temp beside a file at post is checked again right before its
    removal; an editor that held the displaced file open may have written into it."""
    w = crashed(FIRST_REMOVE)
    first, t = w.targets[0], w.targets[2]  # written into main.bean's temp while a.bean's is being removed
    _hook(w.fs, f"remove {first.folder}:.{first.base}.ft-j7.tmp", lambda: w.fs.outside_write(t.temp, FOREIGN))
    out = recover(w.fs, listed=False)
    assert out == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.file(t.temp) == FOREIGN
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


def test_an_outside_save_by_rename_with_our_post_bytes_at_the_restore_swap_is_kept_and_stops() -> None:
    """The displaced entry must be the object step 2 saw (`st_dev`, `st_ino`), not only the same bytes: a new file
    an editor put under the name is never deleted, whatever it holds."""
    w = crashed(CLOSE)
    post = w.targets[2].post
    _hook(
        w.fs,
        "swap /w/L:.main.bean.ft-restore-j7.tmp,main.bean",
        lambda: w.fs.outside_write("/w/L/main.bean", post, new_inode=True),
    )
    assert recover(w.fs, listed=False) == Stopped(Cause.DISPLACED, "/w/L/main.bean")
    assert set(journals(w.fs)) == {State.OPEN}
