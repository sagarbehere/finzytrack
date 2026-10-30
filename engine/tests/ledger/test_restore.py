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

import contextlib
import errno
from collections.abc import Callable

import pytest

from ledger_engine.fs.port import AclEntry, Right
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
    check_taken,
    journals,
    recover,
    undo,
    world,
)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-32")]

# Something at a created path is never removed (owner, 2026-10-30: "Never remove it (Recommended)"): the world's
# `prices.bean` is created, so a try after the save placed it undoes the rest and stops here.
TAKEN = Stopped(Cause.CREATED_PATH_TAKEN, "/w/L/prices.bean")


def touches(label: str, path: str) -> bool:
    """The effect `label` renames, swaps, removes or writes the entry `path` itself (not a name derived from it)."""
    folder, _, base = path.rpartition("/")
    if not label.startswith(("rename ", "swap ", "remove ", "create ", "write ", "rmdir ")):
        return False
    where, _, names = label.partition(" ")[2].partition(":")
    return where == folder and base in names.split(",")


def clean_effects(*, new_dir: bool = False) -> list[str]:
    w = world(new_dir=new_dir)
    durable_commit(w.fs, w.commit)
    return w.fs.effects


EFFECTS = clean_effects()
NEW_DIR_EFFECTS = clean_effects(new_dir=True)
CLOSE = EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed")  # every file at post, temps removed
FIRST_REMOVE = next(i for i, e in enumerate(EFFECTS) if e.startswith("remove "))  # at post, temps still there
NEW_DIR_CLOSE = NEW_DIR_EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed")
NEW_DIR_EXCL = NEW_DIR_EFFECTS.index("rename /w/L/y2026:.prices.bean.ft-s1-j7.tmp,prices.bean")  # not yet done


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
    if ".ft-s1-j7.new" in label:  # the staging write (owner, 2026-10-30: "Staging name")
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


@pytest.mark.parametrize(
    ("at", "new_dir"), [(CLOSE, False), (FIRST_REMOVE, False), (NEW_DIR_CLOSE, True), (NEW_DIR_EXCL, True)]
)
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
            label = w.fs.effects[n + k]
            resolved = f"rename {JD}:{NAME}.open,{NAME}.resolved"
            resolve = next((i for i in range(n, len(w.fs.effects)) if w.fs.effects[i] == resolved), len(w.fs.effects))
            js = journals(w.fs)
            assert n + k <= resolve + 1, "nothing of ours is done after the resolve (S0061 r5)"
            assert isinstance(out, Failed), (k, label, out)
            assert out.cause is Cause.IO_ERROR
            # `.resolved` only when the resolve's own rename took place (it answered the error after it) or its sync
            # failed: every file is then durably at pre already
            late = (n + k == resolve and after) or n + k == resolve + 1
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


def test_a_journal_at_post_is_restored_root_first_with_the_restore_temp_staged_and_synced_before_its_swap() -> None:
    """§8.1 step 3 (ADR-0012 F-5, fidelity review 2 H1; the staging name, owner, 2026-10-30), step 4
    (`resolve_before_restores_synced`). The created file at post is never touched (owner, 2026-10-30: "Never remove
    it (Recommended)"): the try stops there with every other file durably at pre; once the user has deleted it, the
    next try resolves."""
    w = crashed(CLOSE)
    n = len(w.fs.effects)
    out = recover(w.fs)
    assert out == TAKEN
    e = w.fs.effects[n:]
    swaps = [x for x in e if x.startswith("swap ")]
    assert swaps == [
        "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean",
        "swap /w/L/inc:.a.bean.ft-restore-s1-j7.tmp,a.bean",
    ], "the root first"
    for folder, base in (("/w/L", "main.bean"), ("/w/L/inc", "a.bean")):
        rt, rn = f".{base}.ft-restore-s1-j7.tmp", f".{base}.ft-restore-s1-j7.new"
        named = e.index(f"rename {folder}:{rn},{rt}")
        swap = e.index(f"swap {folder}:{rt},{base}")
        # S0061 r3 (verifier F1, red team F1): staged, synced, renamed to the restore temp name, that rename synced,
        # then swapped; an editor's in-place write after the swap then always keeps a durable name
        assert e.index(f"fsync {folder}:{rn}") < named < e.index(f"sync {folder}:", named) < swap
        assert f"create {folder}:{rt}" not in e
    assert not [x for x in e if touches(x, "/w/L/prices.bean")], "the created file was touched"
    assert not any(x.startswith(f"rename {JD}:") for x in e), "a journal stopped is never resolved"
    last_swap = max(i for i, x in enumerate(e) if x.startswith(("swap ", "rename /w/L")))
    assert any(x.startswith("sync /w/L:") for x in e[last_swap:]), "stopped only after every restore was synced"
    assert any(x.startswith("sync /w/L/inc:") for x in e[last_swap:])
    check_taken(w.fs, w, out)
    w.fs.outside_remove("/w/L/prices.bean")  # the user deletes it and presses Retry
    n = len(w.fs.effects)
    out = recover(w.fs)
    assert out == Resolved(NAME)
    e = w.fs.effects[n:]
    assert e[-2:] == [f"rename {JD}:{NAME}.open,{NAME}.resolved", f"sync {JD}:"]
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
    assert isinstance(undo(w.fs, w), Resolved)
    assert w.fs.snapshot()["/w/L/main.bean"] == ("file", w.targets[2].pre, 0o600)


def test_a_created_folder_is_removed_when_empty_and_kept_when_it_holds_another_file() -> None:
    w = crashed(NEW_DIR_EXCL, new_dir=True)
    assert isinstance(recover(w.fs), Resolved)
    assert "/w/L/y2026" not in w.fs.snapshot()
    w = crashed(NEW_DIR_EXCL, new_dir=True)
    w.fs.outside_write("/w/L/y2026/.DS_Store", b"finder\n")
    assert isinstance(recover(w.fs), Resolved)
    assert w.fs.file("/w/L/y2026/.DS_Store") == b"finder\n"
    assert w.fs.file("/w/L/y2026/prices.bean") is None
    assert set(journals(w.fs)) == {State.RESOLVED}


def test_a_second_try_after_a_resolved_one_finds_nothing_to_do() -> None:
    w = crashed(CLOSE)
    assert isinstance(undo(w.fs, w), Resolved)
    n = len(w.fs.effects)
    assert recover(w.fs, listed=False) is None
    assert len(w.fs.effects) == n


def test_a_leftover_restore_temp_is_deleted_only_when_it_holds_the_pre_or_the_post_bytes() -> None:
    """The temp rule at a temp name (owner, 2026-10-30: "Staging name (Recommended)"): a restore temp only ever
    holds our synced copy of the pre bytes or the displaced post object, so those are deleted; anything else, a cut
    copy of the pre bytes included (no cut write of ours stands there any more), is kept and the journal stops."""
    for ours in ("pre", "post"):
        w = crashed(CLOSE)
        t = w.targets[2]
        w.fs.outside_write(t.rtemp, (t.pre or b"") if ours == "pre" else t.post)
        out = recover(w.fs)
        assert out == TAKEN, ours
        assert w.fs.file(t.rtemp) is None
        check_recovered(w.fs, w, out)
    for other in ((w.targets[2].pre or b"")[:5], b"", FOREIGN):
        w = crashed(CLOSE)
        t = w.targets[2]
        w.fs.outside_write(t.rtemp, other)
        assert recover(w.fs) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path), other
        assert (w.fs.file(t.rtemp), w.fs.file(t.kept)) == (None, other)


# ---- the displaced check at every restore swap and removal (§6.4; §14 "an outside write between a recovery's check
# and its restore swap, and during a created file's removal, is put back and the journal stops") -------------------


# ---- step 2: every cause stops the journal before anything is written ---------------------------------------------


def _stops_with_nothing_written(
    w: World, cause: Cause, path: str | None, entry: str | None = None, detail: str | None = None
) -> None:
    n = len(w.fs.effects)
    before = w.fs.snapshot()
    out = recover(w.fs, listed=False)
    assert out == Stopped(cause, path, entry, detail), out
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
    write = EFFECTS.index("write /w/L:.main.bean.ft-s1-j7.new")
    for cut in range(len(world().targets[2].post)):
        w = crashed(write, cut=cut)
        out = recover(w.fs)
        assert out == TAKEN  # prices.bean was placed before main.bean's staging write: the cut blocks nothing else
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

    _hook(w.fs, "create /w/L:.main.bean.ft-restore-s1-j7.new", swap_folder)
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
    ("where", "right", "path", "entry"),
    [
        ("/w/L", Right.CHANGE_ENTRIES, "/w/L/main.bean", "/w/L"),  # prices.bean, created, is never written
        ("/w/L/main.bean", Right.DELETE, "/w/L/main.bean", None),
        ("/w/L", Right.DELETE, "/w/L/main.bean", "/w/L"),  # the folder's `delete_child`: `_RMFILE_OK` says no
    ],
)
def test_permissions_that_would_block_the_restore_stop_the_journal_before_anything_is_written(
    where: str, right: Right, path: str, entry: str | None
) -> None:
    w = crashed(CLOSE)
    w.fs.outside_deny(where, right)
    _stops_with_nothing_written(w, Cause.PERMISSION, path, entry)


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
    assert isinstance(undo(w.fs, w, locks), Resolved)
    assert locks.held == set()


def test_the_locks_are_tried_in_path_key_order_and_released_after_the_try() -> None:
    w = crashed(CLOSE)
    locks = MemLocks()
    assert recover(w.fs, locks) == TAKEN
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
            if out == Stopped(Cause.CREATED_PATH_TAKEN, f"{NFC_DIR}/x.bean"):
                assert state.file(f"{NFC_DIR}/x.bean") == b"x\n"  # our own file, placed: left for the user
                continue
            assert not isinstance(out, Stopped | Failed), (at, out)
            assert state.file(f"{NFC_DIR}/x.bean") in (None, b"x\n")


# ---- S0061 mutants: the checks each get a test that must not pass without them ------------------------------------


def test_a_read_only_folder_beside_a_cut_temp_stops_before_anything_is_written() -> None:
    """The temp must be removed, so its folder must allow it: stopped at the checks, not failed half-way."""
    write = EFFECTS.index("write /w/L/inc:.a.bean.ft-s1-j7.new")  # the first file: every file still at pre
    w = crashed(write, cut=3)
    w.fs.outside_deny("/w/L/inc", Right.CHANGE_ENTRIES)
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/inc/a.bean", "/w/L/inc")


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


# The round-5 follow-up (orchestrator, applying the owner's "Yes to both (Recommended)": "A read error during an
# undo's checks ends that try as 'failed' with nothing moved ... wherever it happens"): a `stat`, a listing, a
# folder's open or the volume's capabilities failing at the checks is `Failed(io_error, path, errno)` too.
STEP2_SITES = {
    # site: (method of MemDir or MemFS, the name or path it fails on, the file the outcome names, and the entry it
    # names when that is not the file: verifier r6 F3)
    "file_stat": ("stat", "main.bean", "/w/L/main.bean", None),
    "temp_stat": ("stat", ".main.bean.ft-s1-j7.tmp", "/w/L/main.bean", "/w/L/.main.bean.ft-s1-j7.tmp"),
    "staging_stat": ("stat", ".main.bean.ft-s1-j7.new", "/w/L/main.bean", "/w/L/.main.bean.ft-s1-j7.new"),
    "rtemp_stat": (
        "stat",
        ".main.bean.ft-restore-s1-j7.tmp",
        "/w/L/main.bean",
        "/w/L/.main.bean.ft-restore-s1-j7.tmp",
    ),
    "backup_stat": ("stat", "2-main.bean", "/w/L/main.bean", "/w/local/backups/s1-j7/2-main.bean"),
    "kept_listing": ("names", "/w/L/inc", "/w/L/inc/a.bean", "/w/L/inc"),
    "folder_open": ("open_dir", "/w/L/inc", "/w/L/inc/a.bean", "/w/L/inc"),
    "volume_caps": ("can_swap", "/w/L/inc", "/w/L/inc/a.bean", "/w/L/inc"),
}


@pytest.mark.parametrize("site", sorted(STEP2_SITES))
def test_R5b_an_io_error_at_any_check_fails_the_try_with_nothing_written(
    monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """Every check of step 2 that answers an error (not "no such entry", which is absent) ends the try `failed`,
    naming the file and the error, with nothing written (it was `Stopped(io_error)`: §8.1 step 2's "Any failure ...
    counts as unrecoverable", replaced); once the error is gone, the next try goes on."""
    from tests.fakes.memfs import MemDir

    w = crashed(FIRST_REMOVE)  # every file at post, the save's temps still there
    t = w.targets[2]
    w.fs.outside_write(t.rtemp, t.post)  # a leftover restore temp of ours, so its name is checked too
    with w.fs.open_dir(JD) as d:
        d.sync()
    method, key, path, entry = STEP2_SITES[site]
    fired = [False]
    if method == "open_dir":
        real_open = MemFS.open_dir

        def open_dir(self: MemFS, p: str) -> MemDir:
            if p == key and not fired[0]:
                fired[0] = True
                raise OSError(errno.EIO, "injected")
            return real_open(self, p)

        monkeypatch.setattr(MemFS, "open_dir", open_dir)
    else:
        real = getattr(MemDir, method)

        def failing(self: MemDir, *args: str) -> object:
            here = args[0] if args else self.path()
            if here == key and not fired[0]:
                fired[0] = True
                raise OSError(errno.EIO, "injected")
            return real(self, *args)

        monkeypatch.setattr(MemDir, method, failing)
    n = len(w.fs.effects)
    before = w.fs.snapshot()
    out = recover(w.fs, listed=False)
    monkeypatch.undo()
    assert fired[0], site
    assert out == Failed(Cause.IO_ERROR, path, errno.EIO, entry), (site, out)
    assert w.fs.effects[n:] == [], "a failed check wrote something"
    assert w.fs.snapshot() == before
    out = recover(w.fs)
    assert out == TAKEN, out
    check_recovered(w.fs, w, out)


def test_R5b_an_io_error_at_a_created_folders_removal_check_fails_the_try_with_nothing_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_check_folders`: the folder the save made holds only our temp (the save died before its exclusive rename),
    so it would be removed; its `stat` in its parent answers an error: `failed`, nothing written."""
    from tests.fakes.memfs import MemDir

    w = crashed(NEW_DIR_EXCL, new_dir=True)
    real = MemDir.stat
    fired = [False]

    def failing(self: MemDir, name: str) -> object:
        if name == "y2026" and not fired[0]:
            fired[0] = True
            raise OSError(errno.EIO, "injected")
        return real(self, name)

    with w.fs.open_dir(JD) as d:
        d.sync()
    monkeypatch.setattr(MemDir, "stat", failing)
    n = len(w.fs.effects)
    out = recover(w.fs, listed=False)
    monkeypatch.undo()
    assert out == Failed(Cause.IO_ERROR, "/w/L/y2026", errno.EIO), out
    assert w.fs.effects[n:] == []
    out = recover(w.fs)
    assert out == Resolved(NAME)
    check_recovered(w.fs, w, out)


def test_R5b_an_io_error_at_a_restore_swaps_displaced_stat_fails_the_try_and_moves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """@regression-guard (it already held at d6c04734: an `OSError` from step 3 on is `Failed`): what main.bean's
    restore swap displaced cannot be `stat`ed: `failed` with the errno, nothing moved to a kept name."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    t = w.targets[2]
    name = t.rtemp.rpartition("/")[2]
    real = MemDir.stat
    fired = [False]

    def failing(self: MemDir, n: str) -> object:
        if n == name and self.path() == "/w/L" and w.fs.file(t.path) == t.pre and not fired[0]:
            fired[0] = True
            raise OSError(errno.EIO, "injected")
        return real(self, n)

    monkeypatch.setattr(MemDir, "stat", failing)
    out = recover(w.fs)
    monkeypatch.undo()
    assert fired[0]
    assert out == Failed(Cause.IO_ERROR, t.path, errno.EIO, t.rtemp), out
    assert w.fs.file(t.kept) is None
    assert recover(w.fs) == TAKEN


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


def test_an_outside_write_into_a_created_file_during_a_try_is_never_touched() -> None:
    """It replaced the created file's rename aside and its displaced check (owner, 2026-10-30: "Never remove it
    (Recommended)"): whatever stands at a created path, whenever it got there, stays as it is."""
    w = crashed(CLOSE)
    t = w.targets[1]
    _hook(w.fs, "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean", lambda: w.fs.outside_write(t.path, FOREIGN))
    n = len(w.fs.effects)
    out = recover(w.fs)
    assert out == TAKEN
    assert (w.fs.file(t.path), w.fs.file(t.kept)) == (FOREIGN, None)
    assert not [x for x in w.fs.effects[n:] if touches(x, t.path)]
    for state in w.fs.power_loss_states():
        assert state.file(t.path) == FOREIGN
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
    assert out == TAKEN
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
    t = w.targets[2]
    # after the checks, before main.bean's temp (its old object) is removed, which comes after its pre bytes are back
    _hook(w.fs, "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean", lambda: w.fs.outside_write(t.temp, FOREIGN))
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
    assert out == TAKEN
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
    # before the created file's exclusive rename: the folder holds only names of ours, so the try would remove it
    w.fs.inject(probe.fs.effects.index("rename /w/L/y2026:.prices.bean.ft-s1-j7.tmp,prices.bean"), crash=True)
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
    w = crashed(EFFECTS.index("create /w/L/inc:.a.bean.ft-s1-j7.new"))  # no temp of the save there
    w.fs.outside_write("/w/L/inc/.a.bean.ft-restore-s1-j7.new", b"2026-01")  # a cut copy at its staging name: ours
    w.fs.outside_deny("/w/L/inc", Right.CHANGE_ENTRIES)
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/inc/a.bean", "/w/L/inc")


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
    assert isinstance(undo(w.fs, w), Resolved)
    assert w.fs.xattrs("/w/L/main.bean") == {"com.apple.metadata:_kMDItemUserTags": b"tag"}


def test_a_file_at_post_on_a_volume_without_the_swap_stops_before_anything_is_written() -> None:
    """Verifier L4 (mutant m21): the owner's no-swap rule holds for the restore swap too."""
    w = crashed(CLOSE)
    w.fs.outside_no_swap("/w/L")
    _stops_with_nothing_written(w, Cause.VOLUME_NO_SWAP, "/w/L/main.bean")  # prices.bean is never written


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


def test_R6_a_backup_that_vanishes_between_its_stat_and_its_read_is_a_bad_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant `restore_look_none_ok` (re-run in round 6): an entry gone between its `stat` and its read is absent
    ("no such entry keeps its meaning", verifier r6 F5): for the backup that is a missing backup (`backup_bad`, it
    was told `foreign`), nothing written."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    real = MemDir.read

    def vanishing(self: MemDir, name: str) -> bytes | None:
        return None if name == "2-main.bean" else real(self, name)

    monkeypatch.setattr(MemDir, "read", vanishing)
    _stops_with_nothing_written(w, Cause.BACKUP_BAD, "/w/L/main.bean")


def test_a_folder_the_save_made_whose_own_deletion_is_denied_stops_before_anything_is_written() -> None:
    """Verifier V1, second half: an ACL denying the folder's deletion (the created file not written yet)."""
    w = crashed(NEW_DIR_EFFECTS.index("create /w/L/y2026:.prices.bean.ft-s1-j7.new"), new_dir=True)
    assert "/w/L/y2026" in w.fs.snapshot()
    w.fs.outside_deny("/w/L/y2026", Right.DELETE)
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/y2026")


def test_V6_F4_an_unreadable_file_at_a_created_path_is_taken_and_its_bytes_are_never_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifier r6 F4 (owner, 2026-10-30, option 1: anything at a created path is `taken` "whatever it holds"): the
    bytes there never decide anything, so they are never read. An outside file we may not read (mode 000) made every
    try `failed`, and the user never saw the created-path row of §11; a huge one was read whole."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    real = MemDir.read
    read: list[str] = []

    def denied(self: MemDir, n: str) -> bytes | None:
        read.append(n)
        if n == "prices.bean":
            raise PermissionError(errno.EACCES, "Permission denied")
        return real(self, n)

    monkeypatch.setattr(MemDir, "read", denied)
    out = recover(w.fs)
    assert out == TAKEN, out
    assert "prices.bean" not in read


def test_V6_F5_a_created_file_deleted_while_the_try_runs_is_absent_and_the_try_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifier r6 F5 ("no such entry keeps its meaning", owner, 2026-10-30, batched point): the user deletes the
    file at the created path after its `stat` and before the end of step 2 (here: at the `stat` of its first name
    of ours). Nothing stands there now: the try checks again and resolves; it never stops as `foreign` or `taken`
    over an empty path. (It replaced round 1's red team L3 test for a created file, which expected `foreign`: the
    bytes there are no longer read, verifier r6 F4.)"""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    t = w.targets[1]
    real = MemDir.stat
    fired = [False]

    def vanish(self: MemDir, n: str) -> object:
        if n == f".{t.base}.ft-s1-j7.tmp" and not fired[0]:
            fired[0] = True
            w.fs.outside_remove(t.path)
        return real(self, n)

    monkeypatch.setattr(MemDir, "stat", vanish)
    out = recover(w.fs)
    monkeypatch.undo()
    assert fired[0]
    assert out == Resolved(NAME), out
    check_recovered(w.fs, w, out)


def test_V6_F5_a_created_file_that_stays_is_still_taken_after_the_check_again() -> None:
    """@regression-guard (it already held at e6ab8627): the check again (F5) happens once and changes nothing when the
    file is still there: `taken`, never touched."""
    w = crashed(CLOSE)
    t = w.targets[1]
    n = len(w.fs.effects)
    assert recover(w.fs) == TAKEN
    assert not [x for x in w.fs.effects[n:] if touches(x, t.path)]
    assert w.fs.file(t.path) == t.post


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


def test_the_rollback_never_touches_a_file_another_program_put_at_a_created_path() -> None:
    """Owner, 2026-10-30: "Never remove it (Recommended)": the exclusive rename answers EEXIST; the rollback undoes
    the other files, removes our own names beside the path by the temp rule, leaves that file alone and stops; the
    save is told unfinished (a.bean was replaced first), and every recovery stops the same way until the user acts."""
    probe = world()
    durable_commit(probe.fs, probe.commit)
    rename = probe.fs.effects.index("rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean")
    w = world()
    t = w.targets[1]
    w.fs.on_effect = lambda i, _: w.fs.outside_write(t.path, t.post) if i == rename else None
    out = durable_commit(w.fs, w.commit)
    w.fs.on_effect = None
    assert type(out).__name__ == "Unfinished", out
    assert w.fs.file(t.path) == t.post
    assert w.fs.file(t.temp) is None, "our own temp beside it is removed by the temp rule"
    assert not [x for x in w.fs.effects[rename + 1 :] if touches(x, t.path)], "the other program's file was touched"
    n = len(w.fs.effects)
    out2 = recover(w.fs)
    assert out2 == TAKEN
    assert [x for x in w.fs.effects[n:] if not x.startswith("sync ")] == [], "a second stop writes nothing"
    check_taken(w.fs, w, out2)


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
    assert out == TAKEN
    check_recovered(w.fs, w, out)


def test_a_created_folder_holding_another_entry_is_not_checked_for_removal() -> None:
    """Red team r2 L5: no rmdir would run there, so a denied deletion does not stop the try."""
    w = crashed(NEW_DIR_CLOSE, new_dir=True)
    w.fs.outside_write("/w/L/y2026/notes.txt", b"the user's\n")
    w.fs.outside_deny("/w/L/y2026", Right.DELETE)
    w.fs.outside_deny("/w/L/y2026/prices.bean", Right.DELETE)
    # the created file is never removed, so its own denied deletion stops nothing (owner, 2026-10-30)
    assert recover(w.fs) == Stopped(Cause.CREATED_PATH_TAKEN, "/w/L/y2026/prices.bean")
    w = crashed(NEW_DIR_EFFECTS.index("create /w/L/y2026:.prices.bean.ft-s1-j7.new"), new_dir=True)
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
    create = probe.fs.effects.index("create /w/L:.prices.bean.ft-s1-j7.new")
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


# ---- S0061 round 3 (owner, 2026-10-30: "Staging name (Recommended)"; verifier r3 F1-F3, red team r3 F1-F3) -------


def _first(effects: list[str], prefix: str, start: int = 0) -> int:
    """The first effect at or after `start` whose label starts with `prefix` (found by prefix, so the same test runs
    against the code before the staging name, for the base check)."""
    return next(i for i in range(start, len(effects)) if effects[i].startswith(prefix))


def _restore_swap(at: int) -> int:
    """The effect index of the restore swap of main.bean in a recovery of the save crashed at `at`."""
    probe = crashed(at)
    n = len(probe.fs.effects)
    recover(probe.fs)
    return n + probe.fs.effects[n:].index("swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean")


def test_R3_F1_an_in_place_write_right_after_the_restore_swap_survives_every_power_loss() -> None:
    """Verifier r3 F1 (High), red team r3 F1: an editor writes main.bean in place right after our restore swap (into
    the object our restore temp held); a power loss then drops whatever was not synced. The restore temp's name is
    durable before its swap, so the editor's bytes keep a name in every state, and recovery never resolves past them."""
    swap = _restore_swap(CLOSE)
    w = crashed(CLOSE)
    w.fs.inject(swap + 1, crash=True)  # recovery dies at the effect right after the restore swap
    with pytest.raises(Crash):
        recover(w.fs)
    t = w.targets[2]
    assert w.fs.file(t.path) == t.pre
    w.fs.outside_write(t.path, FOREIGN)  # the editor's durable in-place save through the name
    for state in w.fs.power_loss_states():
        assert FOREIGN in _all_bytes(state), "the editor's bytes lost their only name"
        out = recover(state)
        assert FOREIGN in _all_bytes(state), f"the editor's bytes were deleted ({out})"
        if isinstance(out, Resolved):
            assert state.file(t.path) == FOREIGN


def test_R3_F2a_an_outside_truncation_of_the_displaced_pre_object_beside_a_file_at_post_is_kept() -> None:
    """Red team r3 F2a (reproduced on APFS): a program holding a descriptor on main.bean from before the save
    shortens it in place after the save died past its swaps: its bytes land in the displaced pre object at our temp
    name, a prefix of pre (and of post, since the save appended). Not ours: kept, the journal stops."""
    w = crashed(FIRST_REMOVE)
    t = w.targets[2]
    assert t.pre is not None
    assert (w.fs.file(t.path), w.fs.file(t.temp)) == (t.post, t.pre)
    assert t.post.startswith(t.pre)
    shortened = t.pre[:14]
    w.fs.outside_write(t.temp, shortened)
    assert recover(w.fs) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.file(t.kept) == shortened


def test_R3_F2b_an_outside_truncation_of_the_displaced_post_object_beside_a_file_at_pre_is_kept() -> None:
    """Red team r3 F2b: the mirror at the restore temp, after a crash right after a restore swap."""
    swap = _restore_swap(CLOSE)
    w = crashed(CLOSE)
    w.fs.inject(swap + 1, crash=True)
    with pytest.raises(Crash):
        recover(w.fs)
    t = w.targets[2]
    assert (w.fs.file(t.path), w.fs.file(t.rtemp)) == (t.pre, t.post)
    shortened = t.post[:12]  # a prefix of pre too
    assert (t.pre or b"").startswith(shortened)
    w.fs.outside_write(t.rtemp, shortened)
    assert recover(w.fs) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.file(t.kept) == shortened


def test_R3_vF3_an_editors_truncating_save_left_at_our_temp_beside_a_file_at_pre_is_kept_in_every_power_loss() -> None:
    """Verifier r3 F3: the F5 setting (an editor writes a.bean in place while our swap is unsynced) with an editor's
    save that deletes the file's last characters: a prefix of our post bytes. A power loss that drops the swap leaves
    it at our temp name beside a file at pre: kept, never deleted."""
    swap = EFFECTS.index("swap /w/L/inc:.a.bean.ft-s1-j7.tmp,a.bean")
    w = crashed(EFFECTS.index("sync /w/L/inc:", swap))
    t = w.targets[0]
    assert t.pre is not None
    assert t.post.startswith(t.pre)
    edited = t.pre[:11]
    w.fs.outside_write(t.path, edited)  # in place, through the name our unsynced swap made
    hit = 0
    for state in w.fs.power_loss_states():
        hit += state.file(t.temp) == edited
        recover(state)
        assert edited in _all_bytes(state), "the editor's save was deleted as a cut write of ours"
    assert hit >= 1, "the power-loss state the attack needs was not reached"


@pytest.mark.parametrize("target", [0, 1, 2])
def test_R3_F3_a_staging_write_torn_in_any_shape_by_a_power_loss_blocks_nothing(target: int) -> None:
    """Red team r3 F3, verifier r3 question: a real volume can tear an unsynced file with zeros or its tail before its
    head, not only as a prefix (the fake now does both). The new bytes are written under the staging name, which no
    swap touches, so whatever a power loss leaves there is ours and deleted: `CutTempNeverBlocks`."""
    t = world().targets[target]
    w = crashed(_first(EFFECTS, f"fsync {t.folder}:.{t.base}.ft-s1-j7."))  # written, its data not synced
    states = list(w.fs.power_loss_states())
    shapes = {s.file(n) for s in states for n in t.scratch} - {None}
    assert bytes(len(t.post)) in shapes, "the zero-filled tear was reached"
    for state in states:
        out = recover(state)
        check_recovered(state, w, out)


def test_R3_F3_a_restore_staging_write_torn_in_any_shape_by_a_power_loss_blocks_nothing() -> None:
    probe = crashed(CLOSE)
    n = len(probe.fs.effects)
    recover(probe.fs)
    fsync = _first(probe.fs.effects, "fsync /w/L:.main.bean.ft-restore-s1-j7.", n)
    w = crashed(CLOSE)
    w.fs.inject(fsync, crash=True)  # recovery dies with the restore copy written but not synced
    with pytest.raises(Crash):
        recover(w.fs)
    for state in w.fs.power_loss_states():
        out = recover(state)
        check_recovered(state, w, out)


def test_a_leftover_staging_file_holding_any_bytes_is_deleted_and_blocks_nothing() -> None:
    """Owner, 2026-10-30: "No swap ever touches the staging name, so whatever it holds is our own write and recovery
    deletes it whatever it holds"."""
    for data in (FOREIGN, b"", bytes(40)):
        w = crashed(CLOSE)
        t = w.targets[2]
        w.fs.outside_write(t.staging, data)
        w.fs.outside_write(t.rstaging, data)
        out = recover(w.fs)
        assert out == TAKEN, data
        assert (w.fs.file(t.staging), w.fs.file(t.rstaging)) == (None, None)
        check_recovered(w.fs, w, out)


@pytest.mark.parametrize("which", ["staging", "rstaging"])
def test_a_non_file_at_a_staging_name_is_not_ours_kept_and_stops(which: str) -> None:
    """Our staging write is always a regular file: a folder there is someone else's, moved to the kept name."""
    w = crashed(CLOSE)
    t = w.targets[2]
    w.fs.outside_mkdirs(getattr(t, which))
    assert recover(w.fs, listed=False) == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path)
    assert w.fs.snapshot()[t.kept] == ("dir",)


def test_a_file_at_a_created_path_beside_our_staging_file_is_never_ours() -> None:
    """@regression-guard (the created-path rule, red team r2 H1, held before with the temp name; this pins its staging
    half): while our staging file is there, its rename to the temp name, and so the exclusive rename after it, never
    happened; a file standing at the path is another program's, even with our post bytes."""
    t = world().targets[1]
    create = _first(EFFECTS, f"create {t.folder}:.{t.base}.ft-s1-j7.")
    w = crashed(create + 3)  # the staging file written and synced, not yet renamed
    w.fs.outside_write(t.path, t.post)
    for state in (w.fs, *w.fs.power_loss_states()):
        recover(state)
        assert state.file(t.path) == t.post, "the other program's prices.bean was deleted"


# The verifier's r3 F2 world: a save that rewrites inside the file (post differs from pre at byte 23), where every
# other world appends (post = pre + more), so a prefix of pre is not a prefix of post.
RW_PRE = b"2026-01-01 open Assets:A\n"
RW_POST = b"2026-01-01 open Assets:B\n2026-01-02 *\n"


def _rewrite_world() -> World:
    from tests.ledger.worlds import BD, Target, commit_for

    fs = MemFS()
    for d in (JD, BD, "/w/L/inc"):
        fs.outside_mkdirs(d)
    fs.outside_write("/w/L/main.bean", b'include "inc/a.bean"\n')
    fs.outside_write("/w/L/inc/a.bean", RW_PRE)
    targets = (Target("/w/L/inc/a.bean", RW_PRE, RW_POST),)
    return World(fs, commit_for(targets), targets, fs.snapshot())


def test_R3_vF2_in_a_world_that_rewrites_inside_the_file_every_crash_of_the_save_and_of_recovery_resolves() -> None:
    """Verifier r3 F2: the sweep of the save and of recovery in the rewrite world: a crash at every effect of the save
    (the staging write cut at every offset), every power loss, recovery; and a crash at every effect of the recovery
    of a save cut before its close (writes cut at 0, 24 and 25: past where pre and post differ), then every power
    loss and recovery again."""
    probe = _rewrite_world()
    durable_commit(probe.fs, probe.commit)
    save = probe.fs.effects
    for at, label in enumerate(save):
        for cut in (
            [None, *range(len(RW_POST) + 1)] if ".ft-s1-j7.new" in label and label.startswith("write ") else [None]
        ):
            w = _rewrite_world()
            w.fs.inject(at, crash=True, cut=cut)
            with pytest.raises(Crash):
                durable_commit(w.fs, w.commit)
            for state in (w.fs, *w.fs.power_loss_states()):
                check_recovered(state, w, recover(state))
    close = save.index(f"rename {JD}:{NAME}.open,{NAME}.closed")

    def crashed_rw() -> World:
        w = _rewrite_world()
        w.fs.inject(close, crash=True)
        with pytest.raises(Crash):
            durable_commit(w.fs, w.commit)
        return w

    r = crashed_rw()
    n = len(r.fs.effects)
    recover(r.fs)
    for k, label in enumerate(r.fs.effects[n:]):
        for cut in [None, 0, 24, 25] if label.startswith("write ") else [None]:
            w = crashed_rw()
            w.fs.inject(len(w.fs.effects) + k, crash=True, cut=cut)
            with pytest.raises(Crash):
                recover(w.fs)
            for state in (w.fs, *w.fs.power_loss_states()):
                check_disk(state, w)
                check_recovered(state, w, recover(state))


def test_a_folder_the_save_made_holding_only_our_staging_file_is_checked_for_removal_before_anything_is_written() -> (
    None
):
    """Mutant `ours_in_without_staging`: a staging file is a name of ours the restore removes, so a folder the save
    made that holds only it would be removed; its denied deletion stops the try at the checks, not half-way."""
    t = world(new_dir=True).targets[1]
    w = crashed(_first(NEW_DIR_EFFECTS, f"fsync {t.folder}:.{t.base}.ft-s1-j7.new"), new_dir=True)
    assert w.fs.names_in(t.folder) == [f".{t.base}.ft-s1-j7.new"]
    w.fs.outside_deny(t.folder, Right.DELETE)
    # S0061 r6: the folder's own `_RMFILE_OK` is checked first (red team r6 F1), and is denied by the same right
    _stops_with_nothing_written(w, Cause.PERMISSION, t.path, t.folder)


# ---- S0061 round 4 (red team F1, O1; verifier attack 3) -------------------------------------------------------------

EXCL_RENAME = EFFECTS.index("rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean")  # the created file's, not yet done


def _recovery_effect(at: int, label: str, *, after: int = 0) -> int:
    """The absolute index of the effect `after` places past `label` in a listed recovery of the save crashed at
    `at`, counted from that save's crash."""
    probe = crashed(at)
    n = len(probe.fs.effects)
    recover(probe.fs, listed=False)
    return n + probe.fs.effects[n:].index(label) + after


def test_R4_F1b_a_file_another_program_puts_at_a_created_path_after_a_try_cut_before_its_resolve_is_never_deleted() -> (
    None
):
    """Red team r4 F1 (b): the save died before its exclusive rename; a try dies before `.resolved`; another program
    then writes the path with our post bytes. The next try never touches it (owner, 2026-10-30: "Never remove it
    (Recommended)"): it stops, the file left as it is."""
    w = crashed(EXCL_RENAME)
    t = w.targets[1]
    assert (w.fs.file(t.temp), w.fs.file(t.path)) == (t.post, None)
    w.fs.inject(_recovery_effect(EXCL_RENAME, f"rename {JD}:{NAME}.open,{NAME}.resolved"), crash=True)
    with pytest.raises(Crash):
        recover(w.fs, listed=False)
    w.fs.outside_write(t.path, t.post)
    out = recover(w.fs)
    assert out == TAKEN, out
    assert w.fs.file(t.path) == t.post, "the other program's prices.bean was deleted"
    check_taken(w.fs, w, out)


@pytest.mark.parametrize(("at", "new_dir"), [(EXCL_RENAME, False), (CLOSE, False), (NEW_DIR_CLOSE, True)])
def test_R4_F1_after_a_crash_at_every_effect_of_recovery_a_file_another_program_puts_at_a_created_path_survives(
    at: int, new_dir: bool
) -> None:
    """The sweep behind F1: a crash at every effect of a try, then, in the disk as kept and in every power-loss state
    where the created path is free, another program writes it with our post bytes, and recovery runs twice: those
    bytes are never deleted (at the path, or in a kept copy)."""
    for k in range(len(_recovery_effects(at, new_dir))):
        w = crashed(at, new_dir=new_dir)
        t = w.targets[1]
        w.fs.inject(len(w.fs.effects) + k, crash=True)
        with pytest.raises(Crash):
            recover(w.fs)
        for state in (w.fs, *w.fs.power_loss_states()):
            if state.file(t.path) is not None:
                continue  # our own file still there: bytes another program writes over it in place are ours too
            try:
                state.outside_write(t.path, t.post)
            except FileNotFoundError:
                state.outside_mkdirs(t.folder)
                state.outside_write(t.path, t.post)
            for _ in range(2):
                recover(state)
                assert state.file(t.path) == t.post or state.file(t.kept) == t.post, (k, "deleted")


@pytest.mark.parametrize("which", ["temp", "rtemp"])
def test_R4_O1_a_read_error_on_a_name_of_ours_at_the_checks_fails_the_try_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    """Red team r4 O1: one transient read error on our own temp (holding the pre bytes) made it an outside save,
    moved to a kept name: every later try stopped on a copy of our own bytes. It now fails the try (`failed`: Retry
    may undo it) with nothing written, and the next try reads it again and resolves."""
    from tests.fakes.memfs import MemDir

    swap = EFFECTS.index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    w = crashed(swap + 1) if which == "temp" else crashed(CLOSE)
    root = w.targets[2]
    if which == "rtemp":
        w.fs.outside_write(root.rtemp, root.pre or b"")  # a leftover restore temp holding our copy of the pre bytes
    name = (root.temp if which == "temp" else root.rtemp).rpartition("/")[2]
    assert w.fs.file(root.temp if which == "temp" else root.rtemp) == root.pre
    real = MemDir.read
    fired = [False]

    def flaky(self: MemDir, n: str) -> bytes | None:
        if n == name and not fired[0]:
            fired[0] = True
            raise OSError(errno.EIO, "transient")
        return real(self, n)

    monkeypatch.setattr(MemDir, "read", flaky)
    with w.fs.open_dir(JD) as d:
        d.sync()
    n = len(w.fs.effects)
    before = w.fs.snapshot()
    out = recover(w.fs, listed=False)
    assert out == Failed(Cause.IO_ERROR, root.path, errno.EIO, root.temp if which == "temp" else root.rtemp), out
    assert w.fs.effects[n:] == [], "a failed read wrote something"
    assert w.fs.snapshot() == before
    assert w.fs.file(root.kept) is None
    out = recover(w.fs)
    assert out == TAKEN, out
    check_recovered(w.fs, w, out)


def test_R4_O1_a_read_error_on_a_temp_at_its_removal_fails_the_try_and_keeps_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red team r4 O1, at step 3: the temp read again right before its removal answers an error: `Failed`, the temp
    left where it is (no kept copy of our own bytes), the journal `.open`; the next try resolves."""
    from tests.fakes.memfs import MemDir

    swap = EFFECTS.index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    w = crashed(swap + 1)
    root = w.targets[2]
    name = root.temp.rpartition("/")[2]
    real = MemDir.read
    reads = [0]

    def flaky(self: MemDir, n: str) -> bytes | None:
        if n == name:
            reads[0] += 1
            if reads[0] == 2:  # the first at the checks, the second at its removal
                raise OSError(errno.EIO, "transient")
        return real(self, n)

    monkeypatch.setattr(MemDir, "read", flaky)
    out = recover(w.fs)
    assert out == Failed(Cause.IO_ERROR, root.path, errno.EIO, root.temp), out
    assert (w.fs.file(root.temp), w.fs.file(root.kept)) == (root.pre, None)
    assert set(journals(w.fs)) == {State.OPEN}
    check_disk(w.fs, w)
    out = recover(w.fs)
    assert out == TAKEN, out
    check_recovered(w.fs, w, out)


SHRINK_MAIN = b'include "inc/a.bean"\n'
SHRINK_CASES = {
    "shrink": (b"2026-01-01 open Assets:A\n2026-01-02 *\n", b"2026-01-01 open Assets:A\n"),
    "empty_post": (b"2026-01-01 open Assets:A\n", b""),
    "empty_pre": (b"", b"2026-01-01 open Assets:A\n"),
    "created_empty": (None, b""),
}


def _shrink_world(case: str) -> World:
    from tests.ledger.worlds import BD, commit_for

    pre, post = SHRINK_CASES[case]
    fs = MemFS()
    for d in (JD, BD, "/w/L/inc"):
        fs.outside_mkdirs(d)
    fs.outside_write("/w/L/main.bean", SHRINK_MAIN)
    if pre is not None:
        fs.outside_write("/w/L/inc/a.bean", pre)
    targets = (Target("/w/L/inc/a.bean", pre, post),)
    return World(fs, commit_for(targets), targets, fs.snapshot())


@pytest.mark.parametrize("case", sorted(SHRINK_CASES))
def test_R4_in_worlds_that_shorten_empty_or_fill_a_file_every_crash_of_the_save_and_of_recovery_resolves(
    case: str,
) -> None:
    """@regression-guard (S0061 verifier r4, attack 3: the code already held): the save/recovery sweep in worlds the
    other sweeps do not use: post a prefix of pre, post empty, pre empty, a created empty file. A crash at every save
    effect (writes cut at 0, 1, 5, 13), every power loss, recovery; a crash at every effect of recovery of a save cut
    before its close, every power loss, recovery again."""
    probe = _shrink_world(case)
    durable_commit(probe.fs, probe.commit)
    save = probe.fs.effects
    for at, label in enumerate(save):
        for cut in [None, 0, 1, 5, 13] if label.startswith("write ") else [None]:
            w = _shrink_world(case)
            w.fs.inject(at, crash=True, cut=cut)
            with pytest.raises(Crash):
                durable_commit(w.fs, w.commit)
            for state in (w.fs, *w.fs.power_loss_states()):
                check_recovered(state, w, recover(state))
    close = save.index(f"rename {JD}:{NAME}.open,{NAME}.closed")

    def crashed_here() -> World:
        w = _shrink_world(case)
        w.fs.inject(close, crash=True)
        with pytest.raises(Crash):
            durable_commit(w.fs, w.commit)
        return w

    r = crashed_here()
    n = len(r.fs.effects)
    recover(r.fs)
    for k, label in enumerate(r.fs.effects[n:]):
        for cut in [None, 0, 3] if label.startswith("write ") else [None]:
            w = crashed_here()
            w.fs.inject(len(w.fs.effects) + k, crash=True, cut=cut)
            with pytest.raises(Crash):
                recover(w.fs)
            for state in (w.fs, *w.fs.power_loss_states()):
                check_disk(state, w)
                check_recovered(state, w, recover(state))


FIRST_SWAP = next(i for i, e in enumerate(EFFECTS) if e.startswith("swap "))


@pytest.mark.parametrize("at", range(FIRST_SWAP + 1, CLOSE + 1))
def test_R4_an_in_place_write_through_a_name_during_recovery_then_a_crash_and_a_power_loss_is_never_lost(
    at: int,
) -> None:
    """Found by re-running the red team r4 probe `sweep_outside_restore.py crash x` (S0061 r4 fix; it held at
    6c423b23 too): the save crashed with a swap not yet durable, or a try's own restore swap is not yet durable; an
    editor saves in place through the file's name (into the object that swap put there); the try then removes the
    object the swap displaced, and dies. A power loss that drops the swap but keeps the removal deleted the editor's
    bytes. The try now syncs every folder the record names before its first removal, and each folder right after its
    restore swap, so no removal of ours is ever durable before the swap it follows. A crash at the effect the write
    came before, or at one of the two after it; the editor's bytes survive in every power-loss state."""
    k_max = len(_recovery_effects(at, False))
    for m in range(k_max):
        for victim in range(3):
            for crash2 in (0, 1, 2):
                w = crashed(at)
                t = w.targets[victim]
                n = len(w.fs.effects)
                fired: list[bool] = [False]

                def edit(
                    i: int,
                    _: str,
                    w: World = w,
                    t: Target = t,
                    at_effect: int = n + m,
                    c2: int = crash2,
                    fired: list[bool] = fired,
                ) -> None:
                    if i != at_effect or w.fs.file(t.path) is None:
                        return
                    w.fs.outside_write(t.path, FOREIGN)  # in place, synced: an editor's save through the name
                    fired[0] = True
                    w.fs.inject(i + c2, crash=True)

                w.fs.on_effect = edit
                with contextlib.suppress(Crash):
                    recover(w.fs)
                w.fs.on_effect = None
                if not fired[0]:
                    continue
                for state in (w.fs, *w.fs.power_loss_states()):
                    assert FOREIGN in _all_bytes(state), (m, t.path, crash2, "the editor's save was lost")


# ---- S0061 round 5 (owner, 2026-10-30: "Never remove it (Recommended)", "Yes to both (Recommended)"; red team r5 F1,
# F2, F3, overflow 1-3; verifier r5 F1-F3) ----------------------------------------------------------------------------

STAGE = EFFECTS.index("create /w/L:.prices.bean.ft-s1-j7.new")  # the created file's staging write
STAGED = EFFECTS.index("rename /w/L:.prices.bean.ft-s1-j7.new,.prices.bean.ft-s1-j7.tmp") + 1  # its folder's sync
A_SWAP_SYNC = EFFECTS.index("swap /w/L/inc:.a.bean.ft-s1-j7.tmp,a.bean") + 1  # the sync after a.bean's swap


def test_R5_our_created_file_left_by_a_crash_after_its_exclusive_rename_is_never_touched_and_the_journal_stops() -> (
    None
):
    """Spec §8.1 step 2, §11 (the new row): the save crashed after it placed prices.bean (our post bytes). Recovery
    cannot tell it from another program's file, so it leaves it, undoes the other files and stops
    (`created_path_taken`); every later try stops the same way, writing nothing but syncs; once the user deletes it,
    Retry resolves."""
    for at in (EXCL_RENAME + 1, FIRST_REMOVE, CLOSE):
        w = crashed(at)
        t = w.targets[1]
        assert w.fs.file(t.path) == t.post
        n = len(w.fs.effects)
        out = recover(w.fs)
        assert out == TAKEN, (at, out)
        assert not [x for x in w.fs.effects[n:] if touches(x, t.path)], at
        assert w.fs.file(t.path) == t.post
        check_taken(w.fs, w, out)
        n = len(w.fs.effects)
        assert recover(w.fs) == TAKEN
        assert [x for x in w.fs.effects[n:] if not x.startswith("sync ")] == [], "a second stop writes nothing"
        check_recovered(w.fs, w, out)  # the user deletes it and presses Retry: resolved


def test_R5_nothing_at_a_created_path_resolves_silently() -> None:
    """@regression-guard (it held before; the owner's option keeps it). Owner, 2026-10-30: "When a save is undone and
    nothing stands at a path it was creating, all is well: our leftovers are removed and the journal resolves, as
    today." Every crash before the created file's exclusive rename, and in the new-folder world the folder made but
    empty of anything but ours."""
    for at in range(STAGE, EXCL_RENAME + 1):
        w = crashed(at)
        out = recover(w.fs)
        assert out == Resolved(NAME), (EFFECTS[at], out)
        check_recovered(w.fs, w, out)
    w = crashed(NEW_DIR_EXCL, new_dir=True)
    out = recover(w.fs)
    assert out == Resolved(NAME)
    assert "/w/L/y2026" not in w.fs.snapshot()
    check_recovered(w.fs, w, out)


@pytest.mark.parametrize("crash_at", range(A_SWAP_SYNC, STAGED + 1))
def test_R5_F1_another_programs_file_at_a_created_path_before_the_staging_is_durable_survives_every_recovery(
    crash_at: int,
) -> None:
    """Red team r5 F1 (b), ported: another program's prices.bean, holding exactly our post bytes, appears at the free
    created path after the save's checks and before it staged that file; the save crashes at any effect from there
    to the staging's folder sync; recovery runs twice in the disk as kept and in every power-loss state (one drops the
    staging entry, so no name of ours is left beside the path): the file is never deleted, and the journal stops."""
    w = world()
    t = w.targets[1]
    w.fs.on_effect = lambda i, _: w.fs.outside_write(t.path, t.post) if i == A_SWAP_SYNC else None
    w.fs.inject(crash_at, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.on_effect = None
    for state in (w.fs, *w.fs.power_loss_states()):
        for _ in range(2):
            out = recover(state)
            assert state.file(t.path) == t.post, (EFFECTS[crash_at], out, "the other program's file was deleted")
            assert out == TAKEN, out
        check_taken(state, w, TAKEN)


@pytest.mark.parametrize("victim", [0, 2])  # a.bean (a child), main.bean (the root): the replaced files
def test_R5_F3_a_read_error_at_a_restore_swaps_displaced_check_fails_the_try_and_moves_nothing(
    monkeypatch: pytest.MonkeyPatch, victim: int
) -> None:
    """Red team r5 F3 (`_settle`), ported: one transient EIO while hashing what a restore swap displaced (our own post
    object) moved it to a kept name, and every later try stopped on it (`kept_copy`). Now the try fails
    (`failed`, with the file and the error) and nothing is moved; the next try removes it as ours."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    t = w.targets[victim]
    name = t.rtemp.rpartition("/")[2]
    real = MemDir.read
    fired = [False]

    def flaky(self: MemDir, n: str) -> bytes | None:
        if n == name and not fired[0]:
            fired[0] = True
            raise OSError(errno.EIO, "transient")
        return real(self, n)

    monkeypatch.setattr(MemDir, "read", flaky)
    out = recover(w.fs)
    monkeypatch.undo()
    assert out == Failed(Cause.IO_ERROR, t.path, errno.EIO, t.rtemp), out
    assert w.fs.file(t.kept) is None, "our own bytes moved to a kept name"
    assert w.fs.file(t.rtemp) == t.post, "what the swap displaced is left where it is"
    check_disk(w.fs, w)
    out = recover(w.fs)
    assert out == TAKEN, out
    check_recovered(w.fs, w, out)


def test_R5_O1_a_read_error_on_the_file_or_its_backup_fails_the_try_with_nothing_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red team r5 overflow 1: a transient read error on the file was `Stopped(foreign)`, on the backup
    `Stopped(backup_bad)`; both are `Failed(io_error)` with the error, nothing written, and the next try goes on."""
    from tests.fakes.memfs import MemDir

    real = MemDir.read
    for name in ("main.bean", "2-main.bean"):
        w = crashed(CLOSE)
        with w.fs.open_dir(JD) as d:
            d.sync()
        fired = [False]

        def flaky(self: MemDir, n: str, name: str = name, fired: list[bool] = fired) -> bytes | None:
            if n == name and not fired[0]:
                fired[0] = True
                raise OSError(errno.EIO, "transient")
            return real(self, n)

        monkeypatch.setattr(MemDir, "read", flaky)
        n = len(w.fs.effects)
        before = w.fs.snapshot()
        out = recover(w.fs, listed=False)
        monkeypatch.undo()
        entry = None if name == "main.bean" else "/w/local/backups/s1-j7/2-main.bean"
        assert out == Failed(Cause.IO_ERROR, "/w/L/main.bean", errno.EIO, entry), (name, out)
        assert w.fs.effects[n:] == [], "a failed read wrote something"
        assert w.fs.snapshot() == before
        assert recover(w.fs) == TAKEN


def test_R5_vF3_a_temp_nobody_may_read_fails_every_try_naming_the_file_the_entry_and_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifier r5 F3: an entry at our temp name that is never readable (mode 000: EACCES on every open) fails every
    try with nothing written (owner, 2026-10-30: "Yes to both (Recommended)": any read error fails the try); the
    outcome carries the file and the error, so §11's `failed` screen can show a permanent cause instead of an
    endless Retry (the screen: BL-0151 slice 3). Verifier r6 F3: it names the entry that failed too, the hidden temp,
    not only the ledger file beside it (which is readable)."""
    from tests.fakes.memfs import MemDir

    w = crashed(CLOSE)
    root = w.targets[2]
    w.fs.outside_write(root.temp, b"another program's file, mode 000\n")
    name = root.temp.rpartition("/")[2]
    real = MemDir.read

    def denied(self: MemDir, n: str) -> bytes | None:
        if n == name:
            raise PermissionError(errno.EACCES, "Permission denied")
        return real(self, n)

    monkeypatch.setattr(MemDir, "read", denied)
    with w.fs.open_dir(JD) as d:
        d.sync()
    before = w.fs.snapshot()
    for _ in range(3):
        n = len(w.fs.effects)
        assert recover(w.fs, listed=False) == Failed(Cause.IO_ERROR, root.path, errno.EACCES, root.temp)
        assert w.fs.effects[n:] == []
    assert w.fs.snapshot() == before


def test_R5_vF1_no_try_ever_writes_renames_or_removes_a_file_at_a_created_path() -> None:
    """Verifier r5 F1 and F2, red team r5 F1 (the class): the rename aside of a created file to its restore temp,
    where an editor's write was left hidden beside a resolved journal (F1), or the proof was moved away (F2), is
    gone. Over every crash point of the save (both worlds), the recovery that follows never names the created path
    in a write, a rename, a swap or a removal."""
    for at, cut, new_dir in SAVE_POINTS:
        if cut not in (None, 0):
            continue
        w = crashed(at, cut=cut, new_dir=new_dir)
        t = w.targets[1]
        n = len(w.fs.effects)
        recover(w.fs)
        assert not [x for x in w.fs.effects[n:] if touches(x, t.path)], (at, new_dir)


def test_R5_vF2_a_file_a_sync_client_brings_back_after_the_user_deleted_ours_is_never_deleted() -> None:
    """Verifier r5 F2, ported: after the stop, the user deletes prices.bean; before Retry, a sync client puts it back
    with the bytes it last saw (our post bytes): the next try stops again and leaves it."""
    w = crashed(CLOSE)
    t = w.targets[1]
    assert recover(w.fs) == TAKEN
    w.fs.outside_remove(t.path)
    w.fs.outside_write(t.path, t.post)
    assert recover(w.fs) == TAKEN
    assert w.fs.file(t.path) == t.post


@pytest.mark.parametrize("how", ["deny_delete", "locked"])
@pytest.mark.parametrize("which", ["staging", "temp"])
def test_R5_F2_a_name_of_ours_that_could_not_be_removed_stops_the_try_before_anything_is_written(
    how: str, which: str
) -> None:
    """Red team r5 F2 (the fake's half; the APFS repro is in `tests/fs/test_darwin.py`): an inherited `deny delete`
    or a lock flag on our own staging file or temp made its removal fail half-way, `Failed` on every try for ever.
    Step 2 now checks every removal it would make: `Stopped(permission)`, nothing written."""
    t = world().targets[0]  # a.bean, the first file: every file still at pre
    at = _first(EFFECTS, f"rename {t.folder}:.{t.base}.ft-s1-j7.new") if which == "staging" else A_SWAP_SYNC
    w = crashed(at)
    name = t.staging if which == "staging" else t.temp
    assert w.fs.file(name) is not None, name
    if how == "locked":
        w.fs.outside_lock(name)
    else:
        w.fs.outside_deny(name, Right.DELETE)
    _stops_with_nothing_written(w, Cause.PERMISSION, t.path, name)


@pytest.mark.parametrize("victim", [0, 2])
def test_R5_O2_a_backup_that_rots_during_a_try_never_costs_the_last_copy_of_the_pre_bytes(victim: int) -> None:
    """Red team r5 overflow 2: the save crashed with every file at post and its temps (the old objects, at the pre
    bytes) still there (FIRST_REMOVE); the backup rots right before a removal of the try. A save temp is removed
    only after its file's pre bytes are back, so the pre bytes are always somewhere: in the file or in our temp."""
    removes = [k for k, x in enumerate(_recovery_effects(FIRST_REMOVE, False)) if x.startswith("remove ")]
    assert len(removes) >= 2, removes
    for k in removes:
        w = crashed(FIRST_REMOVE)
        t = w.targets[victim]
        n = len(w.fs.effects)
        backup = f"{SAVE_DIR}/{journal.backup_name(victim, t.base)}"
        assert w.fs.file(backup) == t.pre

        def rot(i: int, _: str, w: World = w, at: int = n + k, backup: str = backup) -> None:
            if i == at:
                w.fs.outside_write(backup, b"bit rot\n")

        w.fs.on_effect = rot
        out = recover(w.fs)
        w.fs.on_effect = None
        for state in (w.fs, *w.fs.power_loss_states()):
            assert t.pre in _all_bytes(state), (k, out, "the last copy of the pre bytes was removed")


def test_R5_a_folder_the_save_made_holding_a_taken_created_file_is_not_checked_for_removal() -> None:
    """Mutant `ours_in_with_base`: a created file is never removed, so the folder the save made around it is never
    removed either, and a denied deletion of that folder stops nothing: the try stops at the created path."""
    w = crashed(NEW_DIR_CLOSE, new_dir=True)
    w.fs.outside_deny("/w/L/y2026", Right.DELETE)
    assert recover(w.fs) == Stopped(Cause.CREATED_PATH_TAKEN, "/w/L/y2026/prices.bean")


def test_R5_a_folder_the_save_made_holding_only_our_staging_file_in_a_parent_that_denies_its_removal_stops() -> None:
    """Mutant `ours_in_without_staging` (re-anchored in round 5, when the staging file's own removal check came
    first in the round-3 test): the staging file may be removed, but the folder the save made around it may not
    (its parent denies the deletion of its entries): stopped at the checks, at that folder, nothing written."""
    t = world(new_dir=True).targets[1]
    w = crashed(_first(NEW_DIR_EFFECTS, f"fsync {t.folder}:.{t.base}.ft-s1-j7.new"), new_dir=True)
    assert w.fs.names_in(t.folder) == [f".{t.base}.ft-s1-j7.new"]
    w.fs.outside_deny("/w/L", Right.DELETE)
    _stops_with_nothing_written(w, Cause.PERMISSION, t.folder)


def test_R5_a_name_of_ours_that_vanishes_between_its_stat_and_its_read_fails_the_try_with_nothing_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutants `check_fail_as_stop`, `check_fail_keeps` (round 4, re-run in round 5): a read error now fails the try
    at the read itself; what is left of `unreadable` is an entry at a temp name that vanished between its `stat` and
    its read. Nothing is known about it: the try fails (`failed`, Retry looks again), and nothing is written."""
    from tests.fakes.memfs import MemDir

    swap = EFFECTS.index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    w = crashed(swap + 1)
    root = w.targets[2]
    name = root.temp.rpartition("/")[2]
    real = MemDir.read

    def vanishing(self: MemDir, n: str) -> bytes | None:
        return None if n == name else real(self, n)

    monkeypatch.setattr(MemDir, "read", vanishing)
    with w.fs.open_dir(JD) as d:
        d.sync()
    n = len(w.fs.effects)
    before = w.fs.snapshot()
    assert recover(w.fs, listed=False) == Failed(Cause.IO_ERROR, root.path, None, root.temp)
    assert w.fs.effects[n:] == []
    assert w.fs.snapshot() == before


def test_R5_an_in_place_write_after_a_crashed_tries_unsynced_restore_swap_survives_the_next_try() -> None:
    """Mutant `step3_presync_off` (round 4, re-run in round 5, when the save temps' removal beside a file at post moved
    after the restore): a try dies right after its restore swap of main.bean, before that swap's sync (main.bean
    holds our restored copy, the restore temp the displaced post object). The next try finds main.bean at pre and
    removes its names of ours; an editor saves in place through the file's name right before one of those removals,
    and the try dies 0-2 effects later. Without the sync of every folder first, a power loss that drops the earlier
    swap and keeps the removal deletes the editor's bytes."""
    probe = crashed(CLOSE)
    n0 = len(probe.fs.effects)
    recover(probe.fs, listed=False)
    swap = n0 + probe.fs.effects[n0:].index("swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean")
    t = probe.targets[2]
    removals = ("remove /w/L:.main.bean.ft-restore-s1-j7.tmp", "remove /w/L:.main.bean.ft-s1-j7.tmp")
    tried = 0
    for label in removals:
        for crash2 in (0, 1, 2):
            w = crashed(CLOSE)
            w.fs.inject(swap + 1, crash=True)  # after the swap, before its sync
            with pytest.raises(Crash):
                recover(w.fs, listed=False)
            fired = [False]

            def edit(
                i: int, seen: str, w: World = w, label: str = label, c2: int = crash2, fired: list[bool] = fired
            ) -> None:
                if seen == label and not fired[0]:
                    fired[0] = True
                    w.fs.outside_write(t.path, FOREIGN)  # in place, through the name, synced
                    w.fs.inject(i + c2, crash=True)

            w.fs.on_effect = edit
            with contextlib.suppress(Crash):
                recover(w.fs, listed=False)
            w.fs.on_effect = None
            if not fired[0]:
                continue
            tried += 1
            for state in (w.fs, *w.fs.power_loss_states()):
                assert FOREIGN in _all_bytes(state), (label, crash2, "the editor's save was lost")
    assert tried >= 3, tried


def test_R5b_a_backup_folder_that_is_gone_stops_the_journal_as_a_bad_backup_not_a_failed_try() -> None:
    """@regression-guard (it held at d6c04734; it kills the mutant `r5b_backup_missing_fails`): "no such entry"
    keeps its meaning at the backup too: a backup folder that is gone is a missing backup (`backup_bad`, stopped:
    Retry cannot bring it back), never an error to retry."""
    w = crashed(CLOSE)
    w.fs.outside_mkdirs("/w/elsewhere")
    w.fs.outside_move(SAVE_DIR, "/w/elsewhere/backups")
    _stops_with_nothing_written(w, Cause.BACKUP_BAD, "/w/L/inc/a.bean")  # the first file at post checked


# ---- S0061 round 6: the restore's own folder checks (owner, 2026-10-30: "Allowlist (Recommended)"; verifier r6 F2,
# red team r6 F1 and overflow 3) ------------------------------------------------------------------------------------


def _deny(perms: str, flags: str = "") -> AclEntry:
    return AclEntry(True, True, frozenset(perms.split(",")), frozenset(f for f in flags.split(",") if f))


@pytest.mark.parametrize(
    ("perms", "flags"), [("delete", "file_inherit"), ("readattr", "file_inherit,only_inherit"), ("add_file", "")]
)
def test_V6_F2_an_acl_entry_that_appeared_after_the_crash_stops_the_try_before_anything_is_written(
    perms: str, flags: str
) -> None:
    """Verifier r6 F2: the restore never asked the folder's ACL, so after an inheritable deny appeared on a folder
    (the user, a management profile, a sync client), the try wrote its restore staging file, which inherited it, and
    failed; every later try stopped on that leftover of ours, even after the folder's entry was removed. Step 2 now
    applies the same allowlist: `permission`, naming the folder and the entry, nothing written; once the entry is
    removed, the next try goes on (nothing of ours inherited it)."""
    w = crashed(FIRST_REMOVE)  # every file at post, the save's temps still there
    with w.fs.open_dir(JD) as d:
        d.sync()
    w.fs.outside_acl("/w/L/inc", _deny(perms, flags))
    detail = "deny " + ",".join([perms, *sorted(f for f in flags.split(",") if f)])
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/inc/a.bean", "/w/L/inc", detail)
    w.fs.outside_acl_clear("/w/L/inc")
    out = recover(w.fs)
    assert out == TAKEN, out
    check_recovered(w.fs, w, out)


def test_V6_F2_a_folder_that_denies_us_search_fails_the_try_with_nothing_written_and_retry_then_resolves() -> None:
    """@regression-guard (it already held at e6ab8627: the `stat` error fails the try). A direct `deny search` on the
    folder: nothing in it can even be looked up, so the file's own `stat` answers
    `EACCES` before any folder check: `failed` (Retry may undo it), nothing written; once the entry is removed, the
    next try goes on."""
    w = crashed(FIRST_REMOVE)
    with w.fs.open_dir(JD) as d:
        d.sync()
    w.fs.outside_acl("/w/L/inc", _deny("search"))
    n = len(w.fs.effects)
    before = w.fs.snapshot()
    assert recover(w.fs, listed=False) == Failed(Cause.IO_ERROR, "/w/L/inc/a.bean", errno.EACCES)
    assert w.fs.effects[n:] == []
    assert w.fs.snapshot() == before
    w.fs.outside_acl_clear("/w/L/inc")
    out = recover(w.fs)
    assert out == TAKEN, out
    check_recovered(w.fs, w, out)


def test_R6_a_folder_that_denies_removing_its_entries_stops_the_try_before_anything_is_written() -> None:
    """Red team r6 F1, the restore's half: `deny delete_child` on a folder where step 3 removes names of ours
    (`_RMFILE_OK` says no; write and search still say yes): `permission` at the checks, never `failed` half-way."""
    w = crashed(FIRST_REMOVE)
    w.fs.outside_acl("/w/L/inc", _deny("delete_child"))
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/inc/a.bean", "/w/L/inc")


def test_R6_an_acl_entry_on_the_parent_of_a_folder_the_save_made_stops_its_removal_before_anything_is_written() -> None:
    """`_check_folders` applies the allowlist to the folder it removes a folder from: y2026 holds only our temp (the
    save died before the exclusive rename), main.bean is at pre with nothing of ours beside it, so /w/L is written in
    only by the `rmdir`."""
    w = crashed(NEW_DIR_EXCL, new_dir=True)
    with w.fs.open_dir(JD) as d:
        d.sync()
    w.fs.outside_acl("/w/L", _deny("readattr", "file_inherit,only_inherit"))
    _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/y2026", "/w/L", "deny readattr,file_inherit,only_inherit")
    w.fs.outside_acl_clear("/w/L")
    out = recover(w.fs)
    assert out == Resolved(NAME), out
    check_recovered(w.fs, w, out)


@pytest.mark.parametrize("ours_inside", [False, True])
def test_R6_O3_a_lock_flag_on_a_folder_the_save_made_stops_the_try_before_anything_is_written(
    ours_inside: bool,
) -> None:
    """Red team r6 overflow 3: `chflags uchg` on the folder the save made: its `rmdir` (and the removal of our temp
    in it) would answer `EPERM` on every try. Step 2 sees the flag (on the empty folder, `_check_folders`; with our
    temp inside, the folder's own `_RMFILE_OK`): `permission`, nothing written; once cleared, the next try resolves."""
    at = NEW_DIR_EXCL if ours_inside else NEW_DIR_EFFECTS.index("create /w/L/y2026:.prices.bean.ft-s1-j7.new")
    w = crashed(at, new_dir=True)
    with w.fs.open_dir(JD) as d:
        d.sync()
    assert bool(w.fs.names_in("/w/L/y2026")) is ours_inside
    w.fs.outside_lock("/w/L/y2026")
    if ours_inside:
        _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/y2026/prices.bean", "/w/L/y2026")
    else:
        _stops_with_nothing_written(w, Cause.PERMISSION, "/w/L/y2026")
    w.fs.outside_unlock("/w/L/y2026")
    out = recover(w.fs)
    assert out == Resolved(NAME), out
    check_recovered(w.fs, w, out)
