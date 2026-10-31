"""The save, its rollback and recovery on a volume without the swap (`design/specs/engine-jobs.md` §6.1 step 8,
§6.4, §8.1, §15; ADR-0013 amendment of 2026-10-29: owner, "Unsafe fallback on such drives"; BL-0160, S0065), over
the in-memory file system in its two no-swap modes: `no_swap` (RENAME_EXCL only: HFS+) and `plain` (neither: exFAT,
FAT, network shares; a replace a power loss may split, so a file may be missing, never torn).

The oracle is `tests.ledger.worlds.check_disk` (which allows a missing file or part of a created file only on a
`plain` volume, only under an `.open` record), then the outcome told against the disk (`check_told`), then recovery
(`check_recovered`). The swap path's own tests are `test_writer.py` and `test_restore.py`; none of their effects
changes in this slice.
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import replace
from functools import cache

import pytest

from ledger_engine.ledger.core.journal import State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.restore import Resolved, Stopped
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Step, Unfinished, durable_commit, sha256
from tests.fakes.memfs import Crash, MemFS
from tests.ledger.test_writer import check_told
from tests.ledger.worlds import (
    JD,
    NAME,
    World,
    assert_our_created_file,
    check_disk,
    check_every_power_loss,
    check_recovered,
    journals,
    recover,
    world,
)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

NO_SWAP = ("no_swap", "plain")
FOREIGN = b"; saved by an editor\n"


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


def _placed(volume: str) -> int:
    """The last effect of putting the created file at its path: its exclusive rename (`no_swap`); on `plain` the data
    sync that ends its exclusive create (a create that answers an error removes the part it made: verifier r1 F2)."""
    e = clean_effects(volume)
    label = "fsync /w/L:prices.bean" if volume == "plain" else "rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean"
    return e.index(label)


# -- the clean save ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("volume", NO_SWAP)
def test_a_save_on_a_volume_without_the_swap_lands_with_nothing_of_ours_left(volume: str) -> None:
    """BL-0160: the fallback replaces the refusal; "saved" only with the closed record durable."""
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


@pytest.mark.parametrize("volume", NO_SWAP)
def test_the_no_swap_order_backups_and_journal_first_then_staging_temp_sync_and_a_plain_rename(volume: str) -> None:
    """§6.1 step 8 on such a volume: backups and the `.open` journal durable before the first replace; per replaced
    file the new bytes synced at the staging name, moved exclusively to the temp name (`rename`: RENAME_EXCL;
    `move` after a check, on `plain`), the folder synced, then a plain rename of the temp onto the file and the folder
    synced; a created file by an exclusive rename of its temp, or on `plain` an exclusive create of the file itself."""
    e = clean_effects(volume)
    jsync = e.index(f"sync {JD}:", e.index(f"rename {JD}:{NAME}.part,{NAME}.open"))
    staged = "rename" if volume == "no_swap" else "move"
    for folder, base in (("/w/L/inc", "a.bean"), ("/w/L", "main.bean")):
        tmp, new = f".{base}.ft-s1-j7.tmp", f".{base}.ft-s1-j7.new"
        fsync = e.index(f"fsync {folder}:{new}")
        named = e.index(f"{staged} {folder}:{new},{tmp}")
        nsync = e.index(f"sync {folder}:", named)
        put = e.index(f"move {folder}:{tmp},{base}")
        dsync = e.index(f"sync {folder}:", put)
        assert jsync < fsync < named < nsync < put < dsync, (base, e)
    if volume == "plain":
        made = e.index("create /w/L:prices.bean")
        assert e[made + 1 : made + 4] == ("write /w/L:prices.bean", "fsync /w/L:prices.bean", "sync /w/L:")
        assert not any(".prices.bean.ft-" in x for x in e), "no temp for a file made in place"
    else:
        named = e.index("rename /w/L:.prices.bean.ft-s1-j7.new,.prices.bean.ft-s1-j7.tmp")
        placed = e.index("rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean")
        assert named < e.index("sync /w/L:", named) < placed < e.index("sync /w/L:", placed)
    assert e.index("move /w/L/inc:.a.bean.ft-s1-j7.tmp,a.bean") < e.index("move /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    assert e[-1] == f"sync {JD}:"


@pytest.mark.parametrize("volume", NO_SWAP)
def test_the_volume_is_judged_before_any_effect_and_a_swap_volume_keeps_the_swap(volume: str) -> None:
    """The write path is chosen per volume before any effect: a ledger folder on an APFS volume keeps the swap even
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
    assert "move /w/X:.b.bean.ft-s1-j7.tmp,b.bean" in w.fs.effects


# -- sweeps ----------------------------------------------------------------------------------------------------------


def _cuts(label: str) -> list[int | None]:
    if not label.startswith("write "):
        return [None]
    return [None, 0, 1, 7, 30]


CRASH_POINTS = [(v, i, cut) for v in NO_SWAP for i, x in enumerate(clean_effects(v)) for cut in _cuts(x)]


@pytest.mark.parametrize(("volume", "at", "cut"), CRASH_POINTS)
def test_a_crash_at_every_effect_then_every_power_loss_leaves_a_disk_recovery_resolves(
    volume: str, at: int, cut: int | None
) -> None:
    """§10 on such a volume: a crash at each effect (a write cut at offsets), every state a power loss may leave
    (a split replace included: a file missing, never torn), then recovery puts every file back at pre."""
    w = world(volume=volume)
    w.fs.inject(at, crash=True, cut=cut)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))


@pytest.mark.parametrize(("volume", "at"), [(v, i) for v in NO_SWAP for i in range(len(clean_effects(v)))])
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


ROLLBACK_POINTS = [p for v in NO_SWAP for p in _rollback_points(v)]


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


@pytest.mark.parametrize(("volume", "at"), [(v, i) for v in NO_SWAP for i in range(len(clean_effects(v)))])
def test_an_effect_that_happens_but_answers_an_error_is_never_told_saved_and_recovery_resolves(
    volume: str, at: int
) -> None:
    """An operation that took place and then answered EIO: told truthfully, never "saved"; on a no-swap volume the
    retry check (below) applies only to `EEXIST` and `ENOENT`, so an EIO keeps failing the save."""
    w = world(volume=volume)
    w.fs.inject(at, crash=False, err=errno.EIO, after=True)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)
    assert not isinstance(out, Landed)
    check_recovered(w.fs, w, recover(w.fs))


# -- the re-hash before the plain rename, and the windows ----------------------------------------------------------


@pytest.mark.parametrize("volume", NO_SWAP)
def test_an_outside_save_before_the_re_hash_refuses_that_file_and_is_never_lost(volume: str) -> None:
    """BL-0160 "a re-hash just before a plain rename": an editor's save that lands after the backup and before the
    replace is found by the re-hash; that file is not replaced and keeps the editor's bytes; the save rolls back."""
    w = world(volume=volume)
    _hook(w.fs, "sync /w/L/inc:", lambda: w.fs.outside_write("/w/L/inc/a.bean", FOREIGN))  # after its staging
    out = durable_commit(w.fs, w.commit)
    # nothing replaced; the rollback stops on the file it finds changed (`foreign`), as on APFS: not saved, writes
    # paused until the user keeps the files as they are (the journal names a file that is neither pre nor post)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, False, True, "/w/L/inc/a.bean"), out
    assert w.fs.file("/w/L/inc/a.bean") == FOREIGN
    assert "move /w/L/inc:.a.bean.ft-s1-j7.tmp,a.bean" not in w.fs.effects
    assert set(journals(w.fs)) == {State.OPEN}
    for state in w.fs.power_loss_states():
        assert state.file("/w/L/inc/a.bean") == FOREIGN


@pytest.mark.parametrize("volume", NO_SWAP)
def test_an_outside_save_by_rename_before_the_re_hash_refuses_that_file(volume: str) -> None:
    """The re-hash checks the object too: a save by rename (a new file under the name) with any bytes stops it."""
    w = world(volume=volume)
    t = w.targets[0]
    _hook(w.fs, "sync /w/L/inc:", lambda: w.fs.outside_write(t.path, t.pre or b"", new_inode=True))
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.CHANGED, out
    assert w.fs.file(t.path) == t.pre


@pytest.mark.parametrize("volume", NO_SWAP)
def test_W1_an_outside_save_between_the_re_hash_and_the_plain_rename_is_lost(
    volume: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The accepted unsafe window W1 (spec §15; owner, 2026-10-29: "Unsafe fallback on such drives"), pinned so it
    stays as small and as stated as it is: an editor's save landing after the re-hash read the file and before the
    plain rename is overwritten, and the save is told "saved" (true of our change). On APFS the swap keeps it."""
    w = world(volume=volume)
    t = w.targets[2]
    from tests.fakes.memfs import MemDir

    real = MemDir.read
    fired = False

    def read(self: MemDir, name: str) -> bytes | None:
        nonlocal fired
        data = real(self, name)
        if name == "main.bean" and not fired and w.fs.file(t.temp) == t.post:
            fired = True
            w.fs.outside_write(t.path, FOREIGN)  # right after the re-hash read it
        return data

    monkeypatch.setattr(MemDir, "read", read)
    out = durable_commit(w.fs, w.commit)
    assert fired
    assert isinstance(out, Landed)
    assert w.fs.file(t.path) == t.post
    assert FOREIGN not in [w.fs.file(p) for p in w.fs.snapshot()], "W1: the outside save is gone"


@pytest.mark.parametrize("volume", NO_SWAP)
def test_an_entry_at_the_temp_name_before_the_move_is_never_replaced_and_is_kept(volume: str) -> None:
    """The exclusive move onto the temp name: RENAME_EXCL answers EEXIST (`no_swap`); on `plain` the check just before
    the plain rename sees the entry. Either way the entry is never replaced: the save stops and the rollback keeps it
    at the kept name (the temp rule)."""
    w = world(volume=volume)
    t = w.targets[0]
    label = "fsync /w/L/inc:.a.bean.ft-s1-j7.new"  # before the check of the temp name
    _hook(w.fs, label, lambda: w.fs.outside_write(t.temp, FOREIGN))
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.CHANGED
    assert FOREIGN in (w.fs.file(t.temp), w.fs.file(t.kept)), "the entry at our temp name was lost"
    assert w.fs.file(t.path) == t.pre


def test_an_entry_at_a_created_path_before_the_exclusive_create_is_never_touched() -> None:
    """On `plain` a created file is made by an exclusive create (`O_EXCL`) of the file itself: an entry that appeared
    there makes it fail and stays as it was; the rollback leaves it (owner, 2026-10-30: "Never remove it")."""
    w = world(volume="plain")
    _hook(w.fs, "create /w/L:prices.bean", lambda: w.fs.outside_write("/w/L/prices.bean", b"x"))
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/prices.bean", "created_path_taken", "/w/L/prices.bean")
    assert w.fs.file("/w/L/prices.bean") == b"x"
    assert w.fs.file("/w/L/inc/a.bean") == w.targets[0].pre
    check_told(w.fs, w, out)


# -- a network volume's retried operation (S0061 red team r4 F3) ----------------------------------------------------


@pytest.mark.parametrize(
    ("volume", "label", "err"),
    [
        ("no_swap", "rename /w/L/inc:.a.bean.ft-s1-j7.new,.a.bean.ft-s1-j7.tmp", errno.EEXIST),
        ("no_swap", "rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean", errno.EEXIST),
        ("plain", "move /w/L/inc:.a.bean.ft-s1-j7.new,.a.bean.ft-s1-j7.tmp", errno.ENOENT),
        ("plain", "move /w/L:.main.bean.ft-s1-j7.tmp,main.bean", errno.ENOENT),
        ("no_swap", "move /w/L:.main.bean.ft-s1-j7.tmp,main.bean", errno.ENOENT),
    ],
)
def test_a_rename_retried_by_a_network_volume_after_it_took_effect_is_ours(volume: str, label: str, err: int) -> None:
    """A network volume can retry a rename whose first attempt took effect, and answer EEXIST (exclusive) or ENOENT
    (plain): with the source gone and the target the same object, the rename is ours and the save goes on."""
    w = world(volume=volume)
    w.fs.inject(clean_effects(volume).index(label), crash=False, err=err, after=True)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out
    check_disk(w.fs, w)


@pytest.mark.parametrize("volume", NO_SWAP)
def test_a_mkdir_retried_by_a_network_volume_after_it_took_effect_is_ours(volume: str) -> None:
    w = world(volume=volume, new_dir=True)
    w.fs.inject(clean_effects(volume, True).index("mkdir /w/L:y2026"), crash=False, err=errno.EEXIST, after=True)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out


@pytest.mark.parametrize("volume", NO_SWAP)
def test_eexist_is_still_someone_elses_when_the_source_is_there_or_the_target_is_another_object(volume: str) -> None:
    """The retry check never takes another program's entry for ours: a rename that did not happen (its source still
    there), and a folder that is not empty, stay foreign: the save stops and nothing of theirs is touched."""
    w = world(volume=volume)
    label = "rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean" if volume == "no_swap" else None
    if label is not None:
        w.fs.inject(clean_effects(volume).index(label), crash=False, err=errno.EEXIST)
        out = durable_commit(w.fs, w.commit)
        assert not isinstance(out, Landed), out
    w = world(volume=volume, new_dir=True)

    def theirs() -> None:
        w.fs.outside_mkdirs("/w/L/y2026")
        w.fs.outside_write("/w/L/y2026/x", b"x")

    _hook(w.fs, "mkdir /w/L:y2026", theirs)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed), out
    assert w.fs.file("/w/L/y2026/x") == b"x"


def test_on_apfs_an_eexist_is_never_taken_for_a_retried_rename() -> None:
    """The retry check is for volumes without the swap only (APFS answers EEXIST only when nothing happened): the swap
    path is unchanged."""
    w = world()
    e = clean_effects("swap")
    w.fs.inject(
        e.index("rename /w/L/inc:.a.bean.ft-s1-j7.new,.a.bean.ft-s1-j7.tmp"), crash=False, err=errno.EEXIST, after=True
    )
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed), out


# -- recovery: a file missing in the middle of a replace -----------------------------------------------------------


def _missing_state(volume: str, base: str = "main.bean") -> tuple[World, MemFS]:
    """A crash right after the plain rename onto `base`, then a power loss that kept the removal of the old entry
    and dropped the rename: the file is missing (a `plain` volume only)."""
    w = world(volume=volume)
    folder = "/w/L" if base == "main.bean" else "/w/L/inc"
    w.fs.inject(clean_effects(volume).index(f"move {folder}:.{base}.ft-s1-j7.tmp,{base}") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    path = f"{folder}/{base}"
    states = [s for s in w.fs.power_loss_states() if s.file(path) is None]
    assert states, "a split replace must be able to leave the file missing"
    return w, states[0]


def test_a_file_missing_in_the_middle_of_a_replace_is_restored_from_its_backup() -> None:
    w, state = _missing_state("plain")
    out = recover(state)
    assert isinstance(out, Stopped | Resolved), out
    check_recovered(state, w, out)
    assert state.file("/w/L/main.bean") == w.targets[2].pre


def test_a_missing_file_whose_backup_is_bad_stops_and_writes_nothing() -> None:
    _, state = _missing_state("plain", "a.bean")
    with state.open_dir(f"/w/local/backups/{NAME}") as d:
        d.remove("0-a.bean")
    state.outside_write("/w/L/inc/.a.bean.ft-restore-s1-j7.new", b"a cut copy of ours")  # a leftover of a try
    before = state.snapshot()
    out = recover(state)
    assert out == Stopped(Cause.BACKUP_BAD, "/w/L/inc/a.bean"), out
    assert state.snapshot() == before


def test_on_a_swap_volume_a_missing_replaced_file_still_stops() -> None:
    """Only a volume without the swap can lose a file in the middle of a replace; on APFS a missing file was removed by
    someone, and recovery leaves the choice to the user, as before (`foreign`)."""
    w = world()
    w.fs.inject(clean_effects("swap").index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_remove("/w/L/main.bean")
    out = recover(w.fs)
    assert out == Stopped(Cause.FOREIGN, "/w/L/main.bean"), out


@pytest.mark.parametrize("volume", NO_SWAP)
def test_the_restore_puts_the_old_bytes_back_by_the_no_swap_order(volume: str) -> None:
    """§8.1 step 3 on such a volume: the pre bytes from the backup at the restore staging name, synced, moved
    exclusively to the restore temp, the folder synced, the file hashed again, a plain rename onto it, the folder
    synced; never a swap."""
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
    staged = "rename" if volume == "no_swap" else "move"
    rs, rt = ".main.bean.ft-restore-s1-j7.new", ".main.bean.ft-restore-s1-j7.tmp"
    i = e.index(f"fsync /w/L:{rs}")
    j = e.index(f"{staged} /w/L:{rs},{rt}", i)
    k = e.index("sync /w/L:", j)
    m = e.index(f"move /w/L:{rt},main.bean", k)
    assert e.index("sync /w/L:", m) > m
    assert w.fs.file("/w/L/main.bean") == w.targets[2].pre
    check_recovered(w.fs, w, out)


@pytest.mark.parametrize("volume", NO_SWAP)
def test_an_outside_save_at_a_file_the_restore_would_replace_stops_it_with_the_bytes_kept(volume: str) -> None:
    """The restore's re-hash: a file found at post at the checks that holds an editor's save when the restore is
    about to replace it is not replaced (`foreign`); its bytes stay."""
    w = world(volume=volume)
    w.fs.inject(clean_effects(volume).index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    _hook(w.fs, "create /w/L:.main.bean.ft-restore-s1-j7.new", lambda: w.fs.outside_write("/w/L/main.bean", FOREIGN))
    out = recover(w.fs)
    assert out == Stopped(Cause.FOREIGN, "/w/L/main.bean"), out
    assert w.fs.file("/w/L/main.bean") == FOREIGN


def test_an_entry_at_a_missing_files_path_before_the_restore_puts_it_back_is_never_replaced() -> None:
    """The missing file's put-back is an exclusive move: a file another program puts at that path before it (here
    right before the restore temp's folder sync) stays, and the try stops (`foreign`)."""
    w, state = _missing_state("plain")
    _hook(state, "sync /w/L:", lambda: state.outside_write("/w/L/main.bean", FOREIGN))
    out = recover(state)
    assert out == Stopped(Cause.FOREIGN, "/w/L/main.bean"), out
    assert state.file("/w/L/main.bean") == FOREIGN
    assert state.file(w.targets[2].rtemp) == w.targets[2].pre, "the restore copy stays, ours, for the next try"


@pytest.mark.parametrize("volume", NO_SWAP)
def test_a_hard_link_made_before_the_restores_plain_rename_stops_it(volume: str) -> None:
    """The owner's hard-link rule at the restore's re-hash: a second link made since the checks would keep the post
    bytes, unseen, after the rename; the try stops with nothing replaced."""
    w = world(volume=volume)
    w.fs.inject(clean_effects(volume).index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    _hook(w.fs, "create /w/L:.main.bean.ft-restore-s1-j7.new", lambda: w.fs.outside_link("/w/L/main.bean", "/w/L/x"))
    out = recover(w.fs)
    assert out == Stopped(Cause.HARD_LINKED, "/w/L/main.bean"), out
    assert w.fs.file("/w/L/main.bean") == w.targets[2].post


@pytest.mark.parametrize("volume", NO_SWAP)
def test_eexist_or_enoent_with_the_source_gone_but_another_object_at_the_target_is_never_ours(volume: str) -> None:
    """The retry check needs both: the source gone **and** the target the object the source was. Here another program
    took our staging file away and put its own file at our temp name at the moment of the move: never ours."""
    w = world(volume=volume)
    t = w.targets[0]
    staged = "rename" if volume == "no_swap" else "move"

    def theirs() -> None:
        w.fs.outside_remove(t.staging)
        w.fs.outside_write(t.temp, FOREIGN)

    _hook(w.fs, f"{staged} /w/L/inc:.a.bean.ft-s1-j7.new,.a.bean.ft-s1-j7.tmp", theirs)
    if volume == "plain":  # the check before the plain rename passed; the rename itself answers ENOENT
        w.fs.inject(
            clean_effects(volume).index(f"move /w/L/inc:{t.staging.rpartition('/')[2]},{t.temp.rpartition('/')[2]}"),
            crash=False,
            err=errno.ENOENT,
        )
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert FOREIGN in (w.fs.file(t.temp), w.fs.file(t.kept)), "their file was lost"
    assert w.fs.file(t.path) == t.pre


def test_eexist_with_the_source_still_there_is_never_ours_even_when_the_target_is_the_same_object() -> None:
    """A second hard link of our staging file at our temp name (HFS+ has hard links): `RENAME_EXCL` answers EEXIST
    and the source is still there, so the move did not happen: the save stops."""
    w = world(volume="no_swap")
    t = w.targets[0]
    _hook(
        w.fs, "rename /w/L/inc:.a.bean.ft-s1-j7.new,.a.bean.ft-s1-j7.tmp", lambda: w.fs.outside_link(t.staging, t.temp)
    )
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.CHANGED
    assert w.fs.file(t.path) == t.pre


def test_an_entry_at_the_only_created_path_before_the_exclusive_create_is_not_saved_with_nothing_replaced() -> None:
    """`plain`: with nothing replaced before it, an `O_EXCL` that answers EEXIST made nothing of ours: "not saved", the
    journal left for recovery to ask about the file (owner, 2026-10-30), never "unfinished"."""
    w = world(volume="plain")
    c = replace(w.commit, changes=(w.commit.changes[1],))
    _hook(w.fs, "create /w/L:prices.bean", lambda: w.fs.outside_write("/w/L/prices.bean", b"x"))
    out = durable_commit(w.fs, c)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, False, True, "/w/L/prices.bean"), out
    assert w.fs.file("/w/L/prices.bean") == b"x"


@pytest.mark.parametrize("when", ["before_the_try", "after_the_checks"])
def test_an_outside_save_at_a_restore_temp_on_a_plain_volume_goes_to_the_kept_name(when: str) -> None:
    """The kept-name design on a volume without the exclusive rename: an outside file at a name of ours is moved to
    its kept name by `move_excl` (a check, then a plain rename), found at the checks or right before the removal."""
    w = world(volume="plain")
    w.fs.inject(clean_effects("plain").index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    t = w.targets[2]
    if when == "before_the_try":
        w.fs.outside_write(t.rtemp, FOREIGN)
    else:
        _hook(w.fs, "sync /w/L:", lambda: w.fs.outside_write(t.rtemp, FOREIGN))
    out = recover(w.fs)
    assert out == Stopped(Cause.OUTSIDE_SAVE_IN_TEMP, t.path), out
    assert w.fs.file(t.kept) == FOREIGN


WRITE_POINTS = [(v, i, cut) for v, i, cut in CRASH_POINTS if cut is not None]


@pytest.mark.parametrize(("volume", "at", "cut"), WRITE_POINTS)
def test_an_io_error_part_way_through_a_write_is_told_truthfully_and_recovery_resolves(
    volume: str, at: int, cut: int
) -> None:
    """A write that wrote `cut` bytes and then failed (a full disk): never "saved"; "not saved" only "unchanged" after
    the re-hash; a created file cut in place (`plain`) is left and asked about."""
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


# -- round 1 (S0065 verifier r1 F1, F2; red team r1 F1, F2, F4, F5) ------------------------------------------------

MAIN, RT_MAIN = "/w/L/main.bean", ".main.bean.ft-restore-s1-j7.tmp"


def test_a_file_put_at_a_missing_files_path_up_to_its_put_back_is_never_replaced() -> None:
    """Verifier and red team r1 F1: on `plain` the put-back first claims the empty name with an exclusive create
    (`O_EXCL`) of an empty file. A file another program puts at that path at the last moment before the claim (on
    7c78838c: after the check that the name is free, right before the plain rename onto it) stays, and the try stops
    (`foreign`) with its restore copy kept for the next try."""
    w, state = _missing_state("plain")
    _hook(
        state, ("create /w/L:main.bean", f"move /w/L:{RT_MAIN},main.bean"), lambda: state.outside_write(MAIN, FOREIGN)
    )
    out = recover(state)
    assert out == Stopped(Cause.FOREIGN, MAIN), out
    assert state.file(MAIN) == FOREIGN
    assert state.file(w.targets[2].rtemp) == w.targets[2].pre


@pytest.mark.parametrize("new_inode", [False, True], ids=["in_place", "by_rename"])
def test_W1_a_save_onto_the_claimed_name_after_its_check_is_overwritten(new_inode: bool) -> None:
    """Pins window W1 of spec §15 for the put-back (not a guarantee): once the empty claim is checked, an outside save
    onto it before the plain rename is overwritten, as at any file the fallback replaces."""
    w, state = _missing_state("plain")
    _hook(state, f"move /w/L:{RT_MAIN},main.bean", lambda: state.outside_write(MAIN, FOREIGN, new_inode=new_inode))
    check_recovered(state, w, recover(state))
    assert state.file(MAIN) == w.targets[2].pre


@pytest.mark.parametrize("theirs", ["bytes", "link"])
def test_a_claimed_name_that_is_not_our_empty_file_at_its_check_is_never_replaced(theirs: str) -> None:
    """The check after the claim: what stands there must be an empty regular file with one link (our claim). Bytes
    written into it, or a second link made to it, right after the claim stop the try (`foreign`, `hard_linked`) with
    nothing replaced."""
    _, state = _missing_state("plain")

    def act() -> None:
        if theirs == "bytes":
            state.outside_write(MAIN, FOREIGN)
        else:
            state.outside_link(MAIN, "/w/L/x")

    _hook(state, "fsync /w/L:main.bean", act)  # the claim's own data sync: the empty file stands there
    out = recover(state)
    if theirs == "bytes":
        assert out == Stopped(Cause.FOREIGN, MAIN), out
        assert state.file(MAIN) == FOREIGN
    else:
        assert out == Stopped(Cause.HARD_LINKED, MAIN), out
        assert state.file(MAIN) == b""
        assert state.file("/w/L/x") == b""


def test_an_empty_file_at_a_missing_files_path_is_the_claim_of_a_cut_try_and_is_put_back() -> None:
    """A try cut after its claim leaves an empty file at the path (red team r1 F1's fix): the next try takes it for
    the claim (no bytes are at stake) and puts the old version back."""
    w, state = _missing_state("plain")
    state.outside_write(MAIN, b"")
    check_recovered(state, w, recover(state))
    assert state.file(MAIN) == w.targets[2].pre


def test_on_hfs_an_empty_file_at_a_missing_files_path_is_foreign() -> None:
    """The empty claim exists only on `plain`; with RENAME_EXCL (HFS+) the put-back is one exclusive rename, so an
    empty file at a replaced path there is another program's: the try stops."""
    w = world(volume="no_swap")
    w.fs.inject(clean_effects("no_swap").index("move /w/L:.main.bean.ft-s1-j7.tmp,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_write(MAIN, b"", new_inode=True)
    assert recover(w.fs) == Stopped(Cause.FOREIGN, MAIN)
    assert w.fs.file(MAIN) == b""


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
    """The verifier's nested sweep, kept: a missing file's recovery crashed at each of its effects (the claim
    included), then every power-loss state, then recovery again, which must end at pre."""
    w = world(volume="plain")
    s = _missing_at(at, j)
    s.inject(len(s.effects) + k, crash=True)
    with pytest.raises(Crash):
        recover(s)
    for p in (s, *s.power_loss_states()):
        check_disk(p, w)
        check_recovered(p, w, recover(p))


@pytest.mark.parametrize(
    ("label", "cut", "after"),
    [
        ("create /w/L:prices.bean", None, True),
        ("write /w/L:prices.bean", 0, False),
        ("write /w/L:prices.bean", 7, False),
        ("write /w/L:prices.bean", None, True),
        ("fsync /w/L:prices.bean", None, False),
    ],
)
def test_a_created_file_whose_create_answers_an_error_is_removed_and_not_saved_unchanged(
    label: str, cut: int | None, after: bool
) -> None:
    """Verifier r1 F2: on `plain` a write error part-way through the exclusive create of a new file (a full USB stick,
    an I/O error) left a cut file at its path and "unfinished". The save now removes the part it made (a regular
    file with one link holding a prefix of our bytes) and answers as on APFS: not saved, unchanged after the
    re-hash."""
    w = world(volume="plain")
    w.fs.inject(clean_effects("plain").index(label), crash=False, cut=cut, after=after)
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.IO_ERROR, True, path="/w/L/prices.bean"), out
    assert w.fs.file("/w/L/prices.bean") is None
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)


@pytest.mark.parametrize("only", [False, True], ids=["after_a_replace", "alone"])
@pytest.mark.parametrize("theirs", ["bytes", "link"])
def test_after_a_failed_create_a_file_at_its_path_that_is_not_ours_alone_is_never_removed(
    theirs: str, only: bool
) -> None:
    """The removal's check: bytes that are not a prefix of ours (another program wrote into the file), or a second
    link to it, leave the file where it is; the undo then asks about it (`created_path_taken`, window W4), and the
    save is "unfinished" even when the created file is its only one (something stands at a path it was making)."""
    w = world(volume="plain")
    if only:
        w = replace(w, commit=replace(w.commit, changes=(w.commit.changes[1],)))
    probe = world(volume="plain")
    assert isinstance(durable_commit(probe.fs, w.commit), Landed)
    at = probe.fs.effects.index("fsync /w/L:prices.bean")

    def act() -> None:
        if theirs == "bytes":
            w.fs.outside_write("/w/L/prices.bean", FOREIGN)
        else:
            w.fs.outside_link("/w/L/prices.bean", "/w/L/x")

    _hook(w.fs, "fsync /w/L:prices.bean", act)
    w.fs.inject(at, crash=False)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished), out
    assert (out.cause, out.cause_path) == (Cause.CREATED_PATH_TAKEN.value, "/w/L/prices.bean"), out
    assert w.fs.file("/w/L/prices.bean") == (FOREIGN if theirs == "bytes" else w.targets[1].post)


EDITOR = b"2026-01-01 open Assets:A\n2026-01-02 open Assets:B ; the user's edit meanwhile\n"


def test_a_try_that_stops_on_another_file_still_puts_a_missing_file_back_and_keep_keeps_it() -> None:
    """Red team r1 F2: a missing root while recovery stops on another file (an editor's change to `inc/a.bean`): the
    put-back writes only into an empty name, so the try still puts the root back before it stops; Keep then keeps
    the files with the root at its old version, never missing."""
    from ledger_engine.ledger.recover_all import Done, keep
    from tests.fakes.memfs import MemLocks

    w, state = _missing_state("plain")
    state.outside_write("/w/L/inc/a.bean", EDITOR)
    out = recover(state)
    assert out == Stopped(Cause.FOREIGN, "/w/L/inc/a.bean"), out
    assert state.file(MAIN) == w.targets[2].pre
    for p in state.power_loss_states():
        assert p.file(MAIN) == w.targets[2].pre, "the put-back is durable before the try answers"
    assert keep(state, MemLocks(), JD, NAME) == Done()
    assert state.file(MAIN) == w.targets[2].pre
    assert state.file("/w/L/inc/a.bean") == EDITOR


def test_a_try_that_stops_on_another_file_names_a_missing_file_it_cannot_put_back() -> None:
    """When the missing file itself cannot be put back (its backup is gone), the stop names it, not the other file."""
    _, state = _missing_state("plain")
    state.outside_write("/w/L/inc/a.bean", EDITOR)
    state.outside_remove(f"/w/local/backups/{NAME}/2-main.bean")
    assert recover(state) == Stopped(Cause.BACKUP_BAD, MAIN)
    assert state.file(MAIN) is None


@pytest.mark.parametrize("post", ["bytes", "empty"])
def test_a_try_that_stops_never_puts_back_a_file_at_post(post: str) -> None:
    """Only a missing file is put back when a try stops: a file at post stays at post, even when its post bytes are
    empty (it looks like a claim), and nothing beside it is checked or moved (here an outside file at its restore
    temp stays where it is): the stop writes nothing else and names the file it stopped at."""
    w = world(volume="plain")
    c = w.commit
    if post == "empty":
        c = replace(c, changes=(*c.changes[:2], replace(c.changes[2], post=b"")))
    w.fs.inject(clean_effects("plain").index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, c)
    w.fs.outside_write("/w/L/inc/a.bean", EDITOR)
    w.fs.outside_write(w.targets[2].rtemp, FOREIGN)
    before = w.fs.snapshot()
    assert recover(w.fs) == Stopped(Cause.FOREIGN, "/w/L/inc/a.bean")
    assert w.fs.snapshot() == before


def test_on_apfs_a_try_that_stops_is_answered_as_before_whatever_is_absent() -> None:
    """On a swap volume the pass for missing files does nothing: a replaced file someone removed is not looked at,
    and the stop names the file the checks stopped at."""
    w = world()
    w.fs.inject(clean_effects("swap").index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_write("/w/L/inc/a.bean", EDITOR)
    w.fs.outside_remove(MAIN)
    before = w.fs.snapshot()
    assert recover(w.fs) == Stopped(Cause.FOREIGN, "/w/L/inc/a.bean")
    assert w.fs.snapshot() == before


def test_beside_a_missing_file_the_saves_temp_stays_until_its_old_version_is_back() -> None:
    """Red team r1 F4: the save's temp (our post bytes) beside a missing file is removed only after the put-back, so
    a backup that turns bad between the checks and the put-back leaves it (`backup_bad`, both copies still there)."""
    w, state = _missing_state("plain")
    t = w.targets[2]
    assert state.file(t.temp) == t.post
    _hook(  # the restore's first folder sync (step 3): every check is done
        state,
        ("sync /w/L/inc:", "sync /w/L:"),
        lambda: state.outside_write(f"/w/local/backups/{NAME}/2-main.bean", b"spoiled\n"),
    )
    assert recover(state) == Stopped(Cause.BACKUP_BAD, MAIN)
    assert state.file(t.temp) == t.post


def test_the_oracle_allows_a_missing_or_empty_replaced_file_only_after_a_power_loss() -> None:
    """Red team r1 F5: a missing (or empty) replaced file is a power-loss state only; in the disk as a crash left it
    the oracle refuses it."""
    w = world(volume="plain")
    w.fs.inject(clean_effects("plain").index("move /w/L:.main.bean.ft-s1-j7.tmp,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    for spoiled in (None, b""):
        s = next(iter(w.fs.power_loss_states()))
        s.lost_power = False
        if spoiled is None:
            s.outside_remove(MAIN)
        else:
            s.outside_write(MAIN, spoiled)
        with pytest.raises(AssertionError, match="without a power loss"):
            check_disk(s, w)
        s.lost_power = True
        check_disk(s, w)


def test_the_oracle_takes_only_a_part_of_our_bytes_for_our_created_file() -> None:
    """Red team r1 F5: after `created_path_taken` the oracle checks the file left there is ours: on `plain` a prefix
    of the post bytes (after a power loss perhaps torn), never other bytes."""
    w = world(volume="plain")
    w.fs.outside_write("/w/L/prices.bean", w.targets[1].post[:4])
    assert_our_created_file(w.fs, w, "/w/L/prices.bean")
    w.fs.outside_write("/w/L/prices.bean", FOREIGN)
    with pytest.raises(AssertionError, match="not our own file"):
        assert_our_created_file(w.fs, w, "/w/L/prices.bean")


def test_the_oracle_allows_at_a_created_path_only_a_part_of_our_bytes() -> None:
    """Red team r1 F5: on `plain` a created file cut in its exclusive create holds a prefix of the post bytes (after
    a power loss perhaps a torn shape of one); other bytes there are not ours."""
    w = world(volume="plain")
    w.fs.inject(clean_effects("plain").index("fsync /w/L:prices.bean"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    w.fs.outside_write("/w/L/prices.bean", w.targets[1].post[:5])
    check_disk(w.fs, w)
    w.fs.outside_write("/w/L/prices.bean", bytes(5))
    with pytest.raises(AssertionError, match="not ours"):
        check_disk(w.fs, w)
    w.fs.lost_power = True
    check_disk(w.fs, w)  # a torn shape of a prefix (zeros: its data not yet synced)
    w.fs.outside_write("/w/L/prices.bean", FOREIGN)
    with pytest.raises(AssertionError, match="not ours"):
        check_disk(w.fs, w)
