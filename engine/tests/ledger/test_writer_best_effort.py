"""The save, its rollback and recovery on a volume without the swap: the **best-effort mode** (ADR-0068 D2, owner,
2026-11-01; `design/specs/engine-jobs.md` §6.1 step 8, §6.4, §6.8, §8.1, §15; S0071), over the in-memory file system
in its two no-swap modes: `no_swap` (RENAME_EXCL only: HFS+) and `plain` (neither: exFAT, FAT, network shares; a
replace a power loss may split, so a file may be missing, never torn).

What the mode keeps: the journal, the synced backup of every file it replaces, the read-back, recovery from the
backup. What it gives up, by the owner's policy: another program's save to the same file in the same instant can be
lost (no re-hash before the plain rename, no ownership rules; `test_an_outside_save_between_the_backup_and_the_replace
_is_overwritten`).

The oracle is `tests.ledger.worlds.check_disk` (which allows a missing file only on a `plain` volume, only after a
power loss, only under an `.open` record), then the outcome told against the disk (`check_told`), then recovery
(`check_recovered`). The swap path's own tests are `test_writer.py` and `test_restore.py`.
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import replace
from functools import cache

import pytest

from ledger_engine.ledger.core.journal import State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.recover_all import Done, inspect, keep
from ledger_engine.ledger.restore import Resolved, Stopped, recover_journal
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Unfinished, durable_commit, sha256
from tests.fakes.memfs import Crash, MemFS
from tests.ledger.test_recover_all import record_of
from tests.ledger.test_writer import check_told
from tests.ledger.worlds import (
    BD,
    JD,
    NAME,
    Target,
    World,
    check_disk,
    check_every_power_loss,
    check_recovered,
    commit_for,
    journals,
    recover,
    world,
)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

BEST_EFFORT = ("no_swap", "plain")
FOREIGN = b"; saved by an editor\n"
MAIN = "/w/L/main.bean"


@cache
def clean_effects(volume: str, new_dir: bool = False) -> tuple[str, ...]:
    w = world(volume=volume, new_dir=new_dir)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


def _hook(fs: MemFS, label: str | tuple[str, ...], act: Callable[[], None]) -> None:
    """Run `act` (an outside program) right before the first effect labelled `label` (or any of the labels)."""
    done = False
    labels = (label,) if isinstance(label, str) else label

    def hook(_: int, seen: str) -> None:
        nonlocal done
        if not done and seen in labels:
            done = True
            act()

    fs.on_effect = hook


def _put(volume: str, folder: str, base: str, *, created: bool = False) -> str:
    """The effect that puts a file's new bytes at its name: a plain rename of its staging name (`move`); for a
    created file `RENAME_EXCL` where the volume has it (`rename`)."""
    verb = "rename" if created and volume == "no_swap" else "move"
    return f"{verb} {folder}:.{base}.ft-s1-j7.new,{base}"


def _placed(volume: str) -> int:
    """The effect that puts the created file at its path."""
    return clean_effects(volume).index(_put(volume, "/w/L", "prices.bean", created=True))


# -- the clean save ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_a_save_on_a_volume_without_the_swap_lands_with_nothing_of_ours_left(volume: str) -> None:
    """ "Saved" only with the closed record durable; the backup holds the pre bytes; no swap is ever asked."""
    w = world(volume=volume)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out
    for t in w.targets:
        assert w.fs.file(t.path) == t.post
        for name in t.scratch:
            assert w.fs.file(name) is None, name
    assert set(journals(w.fs)) == {State.CLOSED}
    assert check_every_power_loss(w.fs, w) == 1
    assert not any(x.startswith("swap ") for x in w.fs.effects), "no swap is ever asked of such a volume"


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_the_best_effort_order_backups_and_journal_first_then_staging_synced_and_a_plain_rename(volume: str) -> None:
    """§6.8: backups and the `.open` journal durable before the first replace; per file the new bytes synced at the
    staging name, then a plain rename of it onto the file (a created file: RENAME_EXCL where the volume has it), and
    the folder synced. No temp name, no re-hash of the file before its rename (ADR-0068 D2)."""
    e = clean_effects(volume)
    jsync = e.index(f"sync {JD}:", e.index(f"rename {JD}:{NAME}.part,{NAME}.open"))
    for folder, base, made in (
        ("/w/L/inc", "a.bean", False),
        ("/w/L", "prices.bean", True),
        ("/w/L", "main.bean", False),
    ):
        fsync = e.index(f"fsync {folder}:.{base}.ft-s1-j7.new")
        put = e.index(_put(volume, folder, base, created=made))
        dsync = e.index(f"sync {folder}:", put)
        assert jsync < fsync < put < dsync, (base, e)
        assert not any(f"{folder}:{base}" in x and x.startswith("read ") for x in e[jsync:put]), "no re-hash"
    assert not any(".ft-s1-j7.tmp" in x for x in e), "no temp name on such a volume"
    assert e.index(_put(volume, "/w/L/inc", "a.bean")) < e.index(_put(volume, "/w/L", "main.bean"))
    assert e[-1] == f"sync {JD}:"


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_the_volume_is_judged_before_any_effect_and_a_swap_volume_keeps_the_swap(volume: str) -> None:
    """The write path is chosen per folder before any effect: a ledger folder on an APFS volume keeps the swap even
    when another folder of the set is on a no-swap one."""
    w = world()
    w.fs.outside_mkdirs("/w/X", no_swap=True, no_excl=volume == "plain")
    w.fs.outside_write("/w/X/b.bean", b"b\n")
    c = replace(
        w.commit, changes=(*w.commit.changes, replace(w.commit.changes[0], path="/w/X/b.bean", pre=sha256(b"b\n")))
    )
    out = durable_commit(w.fs, c)
    assert isinstance(out, Landed), out
    assert "swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean" in w.fs.effects
    assert "move /w/X:.b.bean.ft-s1-j7.new,b.bean" in w.fs.effects


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_an_outside_save_between_the_backup_and_the_replace_is_overwritten(volume: str) -> None:
    """ADR-0068 D2's accepted loss, stated as a test so it stays known: on such a volume nothing detects another
    program's save in the same instant. The save lands; the backup holds the bytes before the save, not the other
    program's."""
    w = world(volume=volume)
    _hook(w.fs, "fsync /w/L:.main.bean.ft-s1-j7.new", lambda: w.fs.outside_write(MAIN, FOREIGN))  # after its backup
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out
    assert w.fs.file(MAIN) == w.targets[2].post
    assert w.fs.file(f"/w/local/backups/{NAME}/2-main.bean") == w.targets[2].pre


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_an_editors_save_by_rename_after_the_commit_point_is_never_overwritten(volume: str) -> None:
    """The one check left on such a volume (spec §6.8): just before its new bytes are written, the file must still be
    the entry the gate saw (its identity). An editor that saved by writing a new file and renaming it over ours after
    the commit point stops the save (`changed`); the editor's bytes stay. A write into the same entry, or a rename
    while our new bytes are being written, is lost (ADR-0068 D2). S0071 verifier V2."""
    w = world(volume=volume)

    def editor() -> None:
        w.fs.outside_write(MAIN, FOREIGN, new_inode=True)

    _hook(w.fs, _put(volume, "/w/L/inc", "a.bean"), editor)  # the journal durable, as a.bean is replaced
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished), out
    assert out.reason is Reason.CHANGED, out
    assert w.fs.file(MAIN) == FOREIGN


def test_a_created_file_first_whose_path_was_taken_is_not_saved_and_replaced_nothing() -> None:
    """A save whose first file is a created one, on a volume with the exclusive rename but no swap (HFS+): another
    program's file at that path stops the save (`changed`) before any file is replaced, so the outcome is "not saved"
    and the rollback has nothing to put back (`progress.replaced` stays false). S0071 verifier V3."""
    fs = MemFS()
    for d in (JD, BD):
        fs.outside_mkdirs(d)
    fs.outside_mkdirs("/w/L", no_swap=True, no_excl=False)
    fs.outside_write(MAIN, b"m\n")
    targets = (Target("/w/L/new.bean", None, b"n\n"), Target(MAIN, b"m\n", b"m2\n"))
    _hook(fs, "rename /w/L:.new.bean.ft-s1-j7.new,new.bean", lambda: fs.outside_write("/w/L/new.bean", FOREIGN))
    out = durable_commit(fs, commit_for(targets))
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path, out.open_journal) == (Reason.CHANGED, "/w/L/new.bean", True), out
    assert (fs.file("/w/L/new.bean"), fs.file(MAIN)) == (FOREIGN, b"m\n")


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_a_folder_that_appeared_where_the_save_makes_one_is_never_taken_for_ours(volume: str) -> None:
    """No network-retry ownership (ADR-0068 D2): a `mkdir` answered `EEXIST` is a stop (`folder_appeared` since S0100,
    BL-0187; `changed` before), nothing replaced, and the folder that stands there is left."""
    w = world(volume=volume, new_dir=True)
    _hook(w.fs, "mkdir /w/L:y2026", lambda: w.fs.outside_mkdirs("/w/L/y2026"))
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.entry) == (Reason.FOLDER_APPEARED, "/w/L/y2026"), out
    assert w.fs.snapshot()["/w/L/y2026"][0] == "dir"
    for t in w.targets:
        if t.pre is not None:
            assert w.fs.file(t.path) == t.pre


# -- sweeps ----------------------------------------------------------------------------------------------------------


def _cuts(label: str) -> list[int | None]:
    if not label.startswith("write "):
        return [None]
    return [None, 0, 1, 7, 30]


CRASH_POINTS = [(v, i, cut) for v in BEST_EFFORT for i, x in enumerate(clean_effects(v)) for cut in _cuts(x)]


@pytest.mark.parametrize(("volume", "at", "cut"), CRASH_POINTS)
def test_a_crash_at_every_effect_then_every_power_loss_leaves_a_disk_recovery_resolves(
    volume: str, at: int, cut: int | None
) -> None:
    """§6.6's table on such a volume: a crash at each effect (a write cut at offsets), every state a power loss may
    leave (a split replace included: a file missing, never torn), then recovery puts every file back at pre."""
    w = world(volume=volume)
    w.fs.inject(at, crash=True, cut=cut)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))


@pytest.mark.parametrize(("volume", "at"), [(v, i) for v in BEST_EFFORT for i in range(len(clean_effects(v)))])
def test_an_io_error_at_every_effect_is_rolled_back_and_told_truthfully(volume: str, at: int) -> None:
    """§6.4: every single I/O error rolls back at once; "not saved" and "unchanged" (after the re-hash) until the
    created file is placed; after it, unfinished, the file left and asked about (owner, 2026-10-30)."""
    w = world(volume=volume)
    w.fs.inject(at, crash=False, err=errno.ENOSPC)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)
    if at > _placed(volume):
        assert isinstance(out, Unfinished), (at, w.fs.effects[at], out)
        check_recovered(w.fs, w, recover(w.fs))
        return
    assert isinstance(out, NotSaved), (at, w.fs.effects[at], out)
    assert (out.reason, out.unchanged, out.open_journal) == (Reason.IO_ERROR, True, False), out
    assert State.OPEN not in journals(w.fs)


def _rollback_points(volume: str) -> list[tuple[str, int, int]]:
    e = clean_effects(volume)
    start = e.index(f"rename {JD}:{NAME}.part,{NAME}.open")
    out: list[tuple[str, int, int]] = []
    for at in range(start, len(e)):
        w = world(volume=volume)
        w.fs.inject(at, crash=False)
        durable_commit(w.fs, w.commit)
        out += [(volume, at, j) for j in range(at + 1, len(w.fs.effects))]
    return out


ROLLBACK_POINTS = [p for v in BEST_EFFORT for p in _rollback_points(v)]


@pytest.mark.parametrize(("volume", "at", "j"), ROLLBACK_POINTS)
def test_an_io_error_or_a_crash_in_the_rollback_is_told_truthfully_and_recovery_then_resolves(
    volume: str, at: int, j: int
) -> None:
    """§6.4: a rollback that fails leaves the journal `.open` (never "saved"); a crash in it leaves each file at pre,
    at post or (`plain`, after a power loss) missing; recovery then resolves from the backups."""
    w = world(volume=volume)
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed)
    check_told(w.fs, w, out)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))
    w = world(volume=volume)
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))


@pytest.mark.parametrize(("volume", "at"), [(v, i) for v in BEST_EFFORT for i in range(len(clean_effects(v)))])
def test_an_effect_that_happens_but_answers_an_error_is_never_told_saved_and_recovery_resolves(
    volume: str, at: int
) -> None:
    """An operation that took place and then answered EIO (or a network volume's retried rename): told truthfully,
    never "saved", and recovery puts every file back."""
    w = world(volume=volume)
    w.fs.inject(at, crash=False, err=errno.EIO, after=True)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)
    assert not isinstance(out, Landed)
    check_recovered(w.fs, w, recover(w.fs))


WRITE_POINTS = [(v, i, cut) for v, i, cut in CRASH_POINTS if cut is not None]


@pytest.mark.parametrize(("volume", "at", "cut"), WRITE_POINTS)
def test_an_io_error_part_way_through_a_write_is_told_truthfully_and_recovery_resolves(
    volume: str, at: int, cut: int
) -> None:
    """A write that wrote `cut` bytes and then failed (a full disk): never "saved"; "not saved" only "unchanged" after
    the re-hash."""
    w = world(volume=volume)
    w.fs.inject(at, crash=False, cut=cut)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)
    assert not isinstance(out, Landed)
    if isinstance(out, NotSaved):
        assert out.unchanged
    check_recovered(w.fs, w, recover(w.fs))


# -- a file missing after a power loss, and the restore ----------------------------------------------------------


def _missing_state(base: str = "main.bean") -> tuple[World, MemFS]:
    """A crash right after the plain rename onto `base`, then a power loss that kept the removal of the old entry
    and dropped the rename: the file is missing (a `plain` volume only)."""
    w = world(volume="plain")
    folder = "/w/L" if base == "main.bean" else "/w/L/inc"
    w.fs.inject(clean_effects("plain").index(_put("plain", folder, base)) + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    path = f"{folder}/{base}"
    states = [s for s in w.fs.power_loss_states() if s.file(path) is None]
    assert states, "a split replace must be able to leave the file missing"
    return w, states[0]


def test_a_file_missing_in_the_middle_of_a_replace_is_restored_from_its_backup() -> None:
    w, state = _missing_state()
    out = recover(state)
    assert isinstance(out, Stopped | Resolved), out
    check_recovered(state, w, out)
    assert state.file(MAIN) == w.targets[2].pre


def test_a_missing_file_whose_backup_is_bad_stops_and_writes_nothing() -> None:
    _, state = _missing_state("a.bean")
    with state.open_dir(f"/w/local/backups/{NAME}") as d:
        d.remove("0-a.bean")
    state.outside_write("/w/L/inc/.a.bean.ft-restore-s1-j7.new", b"a cut copy of ours")  # a leftover of a try
    before = state.snapshot()
    out = recover(state)
    assert out == Stopped(Cause.BACKUP_BAD, "/w/L/inc/a.bean"), out
    assert state.snapshot() == before


def test_on_a_swap_volume_a_missing_replaced_file_still_stops() -> None:
    """Only a volume without the swap can lose a file in the middle of a replace; on APFS a missing file was removed by
    someone, and recovery leaves the choice to the user (`foreign`)."""
    w = world()
    w.fs.inject(clean_effects("swap").index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_remove(MAIN)
    out = recover(w.fs)
    assert out == Stopped(Cause.FOREIGN, MAIN), out


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_an_empty_file_at_a_replaced_path_is_like_any_other_programs_file(volume: str) -> None:
    """No "empty = ours or not" rule (ADR-0068 D2): an empty file there holds neither the pre nor the post bytes, so
    the try stops `foreign` with no detail and writes nothing; the user chooses."""
    w = world(volume=volume)
    w.fs.inject(clean_effects(volume).index(_put(volume, "/w/L", "main.bean")) + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_write(MAIN, b"")
    before = w.fs.snapshot()
    out = recover(w.fs)
    assert out == Stopped(Cause.FOREIGN, MAIN), out
    assert w.fs.snapshot() == before


def test_keep_keeps_the_files_as_they_are_even_with_one_missing() -> None:
    """Keep is the user's choice of the files as they are now (ADR-0068 D2: no refusal while a file is missing); the
    old version stays in the backup."""
    w, state = _missing_state()
    seen = {i.path: i for i in inspect(state, record_of(state))}
    assert (seen[MAIN].reachable, seen[MAIN].now, seen[MAIN].errno) == (True, None, None), "what the screen names"
    assert keep(state, JD, NAME) == Done()
    assert set(journals(state)) == {State.KEPT}
    assert state.file(MAIN) is None
    assert state.file(f"/w/local/backups/{NAME}/2-main.bean") == w.targets[2].pre


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_two_outside_saves_kept_in_one_try_each_get_their_own_kept_name(volume: str) -> None:
    """Two foreign entries at names of ours beside one file, met in one recovery try: each goes to its own kept
    name, neither replaces the other (S0071 red team F1: on a volume without the exclusive rename the kept name is
    chosen by a check before the plain rename; without it the second plain rename replaced the first kept copy)."""
    w = world(volume=volume)
    w.fs.inject(clean_effects(volume).index(_put(volume, "/w/L", "main.bean")) + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    x, y = b"; another program's bytes X\n", b"; another program's bytes Y\n"
    w.fs.outside_write("/w/L/.main.bean.ft-s1-j7.tmp", x)
    w.fs.outside_write("/w/L/.main.bean.ft-restore-s1-j7.tmp", y)
    out = recover(w.fs)
    assert isinstance(out, Stopped), out
    kept = {n: w.fs.file(f"/w/L/{n}") for n in w.fs.names_in("/w/L") if ".kept-" in n}
    assert sorted(kept.values(), key=lambda b: b or b"") == sorted([x, y]), kept


@pytest.mark.parametrize("backup", ["good", "removed", "other_bytes", "a_folder"])
def test_the_inspection_says_a_backup_is_good_only_when_it_holds_the_old_version(backup: str) -> None:
    """`Inspected.backup` (S0071 round 2, R2-1, then S0090: its path): the screen promises "Finzytrack has its old
    version in its backup" only when the engine names it. A missing file whose backup is gone, holds other bytes or
    is not a file is not known good (the try stops `backup_bad` there); a created file never has one."""
    w, state = _missing_state("a.bean")
    copy = f"/w/local/backups/{NAME}/0-a.bean"
    if backup != "good":
        with state.open_dir(f"/w/local/backups/{NAME}") as d:
            d.remove("0-a.bean")
        if backup == "other_bytes":
            state.outside_write(copy, b"; not the old version\n")
        elif backup == "a_folder":
            state.outside_mkdirs(copy)
    seen = {i.path: i for i in inspect(state, record_of(state))}
    assert seen["/w/L/inc/a.bean"].now is None
    assert seen["/w/L/inc/a.bean"].backup == (copy if backup == "good" else None)
    assert seen["/w/L/prices.bean"].backup is None, "a created file has no backup"
    assert seen[MAIN].backup == f"/w/local/backups/{NAME}/2-main.bean"
    out = recover(state)
    if backup == "good":
        assert state.file("/w/L/inc/a.bean") == w.targets[0].pre, "the backup it called good put the file back"
    else:
        assert out == Stopped(Cause.BACKUP_BAD, "/w/L/inc/a.bean"), out


@pytest.mark.parametrize("volume", BEST_EFFORT)
def test_the_restore_puts_the_old_bytes_back_by_the_best_effort_order(volume: str) -> None:
    """§6.8, §8.1 step 3 on such a volume: the pre bytes from the backup at the restore staging name, synced, then a
    plain rename onto the file and the folder synced; never a swap, no restore temp, no re-hash before the rename."""
    w = world(volume=volume)
    w.fs.inject(clean_effects(volume).index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    start = len(w.fs.effects)
    out = recover(w.fs)
    assert isinstance(out, Stopped), out
    assert out.cause is Cause.CREATED_PATH_TAKEN, out  # our created file: asked once
    e = w.fs.effects[start:]
    assert not any(x.startswith("swap ") for x in e)
    assert not any(".ft-restore-s1-j7.tmp" in x for x in e), "no restore temp on such a volume"
    rs = ".main.bean.ft-restore-s1-j7.new"
    i = e.index(f"fsync /w/L:{rs}")
    m = e.index(f"move /w/L:{rs},main.bean", i)
    assert e.index("sync /w/L:", m) > m
    assert w.fs.file(MAIN) == w.targets[2].pre
    check_recovered(w.fs, w, out)


@cache
def _missing_points() -> tuple[tuple[int, int], ...]:
    """(effect, power-loss state) of every state a save crashed at that effect leaves with a replaced file missing."""
    out: list[tuple[int, int]] = []
    for at in range(len(clean_effects("plain"))):
        w = world(volume="plain")
        w.fs.inject(at, crash=True)
        with pytest.raises(Crash):
            durable_commit(w.fs, w.commit)
        for j, s in enumerate(w.fs.power_loss_states()):
            if any(s.file(t.path) is None for t in w.targets if t.pre is not None):
                out.append((at, j))
    return tuple(out)


def _missing_at(at: int, j: int) -> MemFS:
    """A fresh copy of the state `_missing_points` names."""
    w = world(volume="plain")
    w.fs.inject(at, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    return next(s for i, s in enumerate(w.fs.power_loss_states()) if i == j)


def _put_back_points() -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for at, j in _missing_points():
        s = _missing_at(at, j)
        start = len(s.effects)
        recover(s)
        out += [(at, j, k) for k in range(len(s.effects) - start)]
    return out


PUT_BACK_POINTS = _put_back_points()


@pytest.mark.parametrize(("at", "j", "k"), PUT_BACK_POINTS)
def test_a_crash_at_every_effect_of_a_put_back_then_every_power_loss_still_recovers(at: int, j: int, k: int) -> None:
    """A missing file's recovery crashed at each of its effects, then every power-loss state, then recovery again,
    which must end at pre."""
    w = world(volume="plain")
    s = _missing_at(at, j)
    s.inject(len(s.effects) + k, crash=True)
    with pytest.raises(Crash):
        recover(s)
    for p in (s, *s.power_loss_states()):
        check_disk(p, w)
        check_recovered(p, w, recover(p))


def test_the_oracle_allows_a_missing_replaced_file_only_after_a_power_loss() -> None:
    """A missing replaced file is a power-loss state only; in the disk as a crash left it the oracle refuses it, and
    an empty one it refuses always (nothing of ours leaves one)."""
    w = world(volume="plain")
    w.fs.inject(clean_effects("plain").index(_put("plain", "/w/L", "main.bean")) + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    s = next(iter(w.fs.power_loss_states()))
    s.lost_power = False
    s.outside_remove(MAIN)
    with pytest.raises(AssertionError, match="without a power loss"):
        check_disk(s, w)
    s.lost_power = True
    check_disk(s, w)
    s.outside_write(MAIN, b"")
    with pytest.raises(AssertionError, match="torn or foreign"):
        check_disk(s, w)


def test_a_volume_with_the_swap_but_no_exclusive_rename_gets_the_best_effort_mode() -> None:
    """`handles.write_mode`: the swap path needs both calls (its temps move by `RENAME_EXCL`); a volume that offers
    only the swap is written best effort, never by a swap path that would fail on its first exclusive move."""
    from ledger_engine.ledger.handles import Mode, write_mode

    fs = MemFS()
    fs.outside_mkdirs("/v/swap_only", no_swap=False, no_excl=True)
    fs.outside_mkdirs("/v/apfs")
    with fs.open_dir("/v/swap_only") as d:
        assert write_mode(d) is Mode.BEST_EFFORT
    with fs.open_dir("/v/apfs") as d:
        assert write_mode(d) is Mode.SWAP


def test_a_file_put_at_a_missing_files_path_before_its_put_back_is_never_replaced_where_renames_are_exclusive() -> None:
    """On HFS+ (`RENAME_EXCL` offered) a missing file is put back by the exclusive rename: a file another program put
    at that path just before is kept, and the try stops (`foreign`) for the user."""
    w = world(volume="no_swap")
    w.fs.inject(clean_effects("no_swap").index(_put("no_swap", "/w/L", "main.bean")) + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_remove(MAIN)
    _hook(w.fs, "rename /w/L:.main.bean.ft-restore-s1-j7.new,main.bean", lambda: w.fs.outside_write(MAIN, FOREIGN))
    out = recover(w.fs)
    assert out == Stopped(Cause.FOREIGN, MAIN), out
    assert w.fs.file(MAIN) == FOREIGN


# -- FAT and exFAT: the evidence rule in the sweeps (S0090, BL-0220 V2-4) -----------------------------------------

SERIAL_TYPED = ("msdos", "exfat")
STICK = 2  # the drive `/w/L` is on; the journal and backups stay on the Mac's own disk
ONE_FILE = (Target(MAIN, b'include "inc/a.bean"\n', b'include "inc/a.bean"\n2026-02-01 *\n'),)


def _stick(fs_type: str, one_file: bool) -> World:
    """The test world with `/w/L` on a `plain` stick of `fs_type` (FAT or exFAT: no swap, no exclusive rename, a drive
    UUID from a format serial, so a missing file needs evidence: `restore._unconfirmed`); `one_file`: a save of
    `main.bean` alone, whose missing file can have no evidence."""
    w = world(volume="plain")
    w.fs.outside_mount("/w/L", STICK)
    w.fs.outside_fs_type(STICK, fs_type)
    if one_file:
        w = replace(w, commit=commit_for(ONE_FILE), targets=ONE_FILE)
    return replace(w, before=w.fs.snapshot())


@cache
def _stick_effects(fs_type: str, one_file: bool) -> tuple[str, ...]:
    w = _stick(fs_type, one_file)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


def _settle(state: MemFS, w: World, out: object, seen: list[str]) -> None:
    """The oracle with the evidence rule (S0089 round 2; owner, 2026-11-09: "Evidence for missing (Recommended)"): a
    try that stops `missing_unconfirmed` wrote nothing, names a replaced file that is missing and a backup holding its
    old version; once the user copies that backup there, Retry finishes like any other try (`check_recovered`)."""
    if isinstance(out, Stopped) and out.cause is Cause.MISSING_UNCONFIRMED:
        seen.append(out.path or "")
        t = next(t for t in w.targets if t.path == out.path)
        assert t.pre is not None
        assert state.file(t.path) is None, out
        assert out.entry is not None
        assert state.file(out.entry) == t.pre, "the named backup does not hold the old version"
        state.outside_write(t.path, t.pre)  # the user's manual copy, as the blocked screen says
        out = recover(state)
    check_recovered(state, w, out)  # type: ignore[arg-type]


def _try_and_settle(state: MemFS, w: World, seen: list[str]) -> None:
    check_disk(state, w)
    before = (state.snapshot(), dict(state.mtimes))
    out = recover(state)
    if isinstance(out, Stopped) and out.cause is Cause.MISSING_UNCONFIRMED:
        with state.open_dir(JD) as d:  # the listing's own sync is not a write on the stick
            d.sync()
        assert (state.snapshot(), dict(state.mtimes)) == before, "missing_unconfirmed wrote something"
    _settle(state, w, out, seen)


@pytest.mark.parametrize("one_file", [False, True], ids=["world", "one_file"])
@pytest.mark.parametrize("fs_type", SERIAL_TYPED)
def test_a_crash_at_every_effect_on_a_fat_or_exfat_stick_then_every_power_loss_recovers(
    fs_type: str, one_file: bool
) -> None:
    """@regression-guard (it held on dc656deb; the S0089 final verifier's scratch sweep, made permanent). §6.6's table
    on a FAT or exFAT stick: a crash at each effect (a write cut at offsets), every state a power loss may leave, then
    recovery: every file back at pre, or (a one-file save whose file a split replace left missing) a stop that wrote
    nothing and names the backup, after whose manual copy Retry resolves. The one-file sweep must reach that stop."""
    seen: list[str] = []
    effects = _stick_effects(fs_type, one_file)
    for at, label in enumerate(effects):
        for cut in _cuts(label):
            w = _stick(fs_type, one_file)
            w.fs.inject(at, crash=True, cut=cut)
            with pytest.raises(Crash):
                durable_commit(w.fs, w.commit)
            for state in (w.fs, *w.fs.power_loss_states()):
                _try_and_settle(state, w, seen)
    assert bool(seen) is one_file, seen  # with `inc/a.bean` on the stick there is always evidence


@pytest.mark.parametrize("one_file", [False, True], ids=["world", "one_file"])
@pytest.mark.parametrize("fs_type", SERIAL_TYPED)
def test_an_error_or_a_crash_in_the_saves_rollback_on_a_fat_or_exfat_stick_then_recovery_finishes(
    fs_type: str, one_file: bool
) -> None:
    """@regression-guard (it held on dc656deb). The save's own rollback (§6.4) on such a stick, at every effect after
    the record is `.open`: an I/O error or a crash at each of the rollback's effects, then every power-loss state,
    then recovery under the evidence rule (`_settle`)."""
    seen: list[str] = []
    effects = _stick_effects(fs_type, one_file)
    start = effects.index(f"rename {JD}:{NAME}.part,{NAME}.open")
    for at in range(start, len(effects)):
        probe = _stick(fs_type, one_file)
        probe.fs.inject(at, crash=False)
        durable_commit(probe.fs, probe.commit)
        for j in range(at + 1, len(probe.fs.effects)):
            for crash in (False, True):
                w = _stick(fs_type, one_file)
                w.fs.inject(at, crash=False)
                if crash:
                    w.fs.inject(j, crash=True)
                    with pytest.raises(Crash):
                        durable_commit(w.fs, w.commit)
                else:
                    w.fs.inject(j, crash=False, err=errno.EIO)
                    out = durable_commit(w.fs, w.commit)
                    assert not isinstance(out, Landed)
                    check_told(w.fs, w, out)
                for state in (w.fs, *w.fs.power_loss_states()):
                    _try_and_settle(state, w, seen)
    assert bool(seen) is one_file, seen


@pytest.mark.parametrize("fs_type", SERIAL_TYPED)
def test_the_saves_own_rollback_meeting_a_missing_file_on_a_fat_stick_writes_nothing_and_names_the_backup(
    fs_type: str,
) -> None:
    """@regression-guard (it held on dc656deb). The evidence rule through the save's rollback, not only recovery's
    try: a one-file save whose folder sync fails after `main.bean` was replaced, while another program removed
    `main.bean`. The rollback finds it missing with no other file of the save on the stick as evidence, so it puts
    nothing back: the save ends unfinished, naming the file; the journal stays `.open`; recovery stops the same way,
    names the backup, and resolves after the user's copy."""
    w = _stick(fs_type, one_file=True)
    effects = _stick_effects(fs_type, True)
    at = effects.index("sync /w/L:", effects.index(_put("plain", "/w/L", "main.bean")))
    _hook(w.fs, "sync /w/L:", lambda: w.fs.outside_remove(MAIN))
    w.fs.inject(at, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished), out
    assert (out.cause, out.cause_path) == ("missing_unconfirmed", MAIN)
    assert w.fs.file(MAIN) is None
    assert State.OPEN in journals(w.fs)
    before = (w.fs.snapshot(), dict(w.fs.mtimes))
    got = recover_journal(w.fs, JD, NAME)
    assert got == Stopped(Cause.MISSING_UNCONFIRMED, MAIN, f"{BD}/{NAME}/0-main.bean"), got
    assert (w.fs.snapshot(), dict(w.fs.mtimes)) == before
    _settle(w.fs, w, got, [])
