"""The save's durable order and its rollback (`design/specs/engine-jobs.md` §6.1 step 8, §6.4, §10, §13, §14 "Save,
durable order"; ADR-0008 steps 5-6, ADR-0012, ADR-0013; S0060, S0061), over the in-memory file system with its
durable layer.

The oracle is `tests.ledger.worlds.check_disk`, for every state of the disk, crash or not, before or after a power
loss. Then each injected failure checks the outcome told against the disk (§6.4: any failure from the `.open` journal
on rolls back at once): `NotSaved` only with every target at pre (or, for the file whose outside save the save kept,
at that outside save) and, unless `open_journal`, no `.open` journal in the disk or in any power-loss state of it;
`Unfinished` only with a readable journal that is not `.closed`-and-durable; `Landed` only with a `.closed` journal
that every power loss keeps (`SavedSurvivesPowerLoss`). A crash (the process died: no rollback) is followed by
recovery (`tests/ledger/test_restore.py`).
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import replace

import pytest

from ledger_engine.fs.port import Right
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.writer import (
    Change,
    Commit,
    Landed,
    NotSaved,
    Outcome,
    Reason,
    Step,
    Unfinished,
    durable_commit,
    sha256,
)
from tests.fakes.memfs import Crash, MemFS
from tests.ledger.worlds import (
    BD,
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

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]


def clean_run() -> tuple[World, Outcome]:
    w = world()
    return w, durable_commit(w.fs, w.commit)


def test_a_clean_save_lands_and_writes_exactly_the_plan() -> None:
    w, out = clean_run()
    assert isinstance(out, Landed)
    for t in w.targets:
        assert w.fs.file(t.path) == t.post
    assert set(journals(w.fs)) == {State.CLOSED}
    check_disk(w.fs, w)
    assert check_every_power_loss(w.fs, w) == 1  # everything synced: one durable state


def test_the_record_names_every_file_by_canonical_path_and_key_with_the_closed_records_fields() -> None:
    w, out = clean_run()
    assert isinstance(out, Landed)
    r = out.record
    assert [f.canonical_path for f in r.files] == [t.path for t in w.targets]
    assert [f.path_key for f in r.files] == [t.path.lower() for t in w.targets]  # a case-insensitive volume
    assert [f.pre for f in r.files] == [None if t.pre is None else sha256(t.pre) for t in w.targets]
    assert [f.post for f in r.files] == [sha256(t.post) for t in w.targets]
    assert (r.workspace_key, r.lineage, r.engine_build) == ("k1", "lin1", "build1")
    assert (r.post_fingerprint, r.adopted_digest, r.parse_digest) == (
        w.commit.post_fingerprint,
        w.commit.adopted_digest,
        w.commit.parse_digest,
    )
    assert journal.decode(journals(w.fs)[State.CLOSED]) == r


def _index(effects: list[str], label: str, start: int = 0) -> int:
    for i in range(start, len(effects)):
        if effects[i] == label:
            return i
    pytest.fail(f"no effect {label!r} after {start}: {effects}")


def test_the_effects_come_in_the_durable_order_of_step_8() -> None:
    """§6.1 step 8 row by row: every sync before the effect that relies on it; "landed" only after the close's
    folder sync, the last effect."""
    w, out = clean_run()
    assert isinstance(out, Landed)
    e = w.fs.effects
    bsync = _index(e, f"sync {SAVE_DIR}:")
    for i in (0, 2):
        assert _index(e, f"fsync {SAVE_DIR}:{i}-{w.targets[i].path.rpartition('/')[2]}") < bsync  # 8.1
    part = _index(e, f"create {JD}:{NAME}.part")
    assert bsync < part  # 8.1 before 8.2
    assert _index(e, f"fsync {JD}:{NAME}.part") < _index(e, f"rename {JD}:{NAME}.part,{NAME}.open")
    jsync = _index(e, f"sync {JD}:", _index(e, f"rename {JD}:{NAME}.part,{NAME}.open"))
    first_replace = min(i for i, x in enumerate(e) if x.startswith(("swap ", "rename /w/L")))
    assert jsync < first_replace  # the .open journal durable before the first replace
    order: list[int] = []
    for t in w.targets:
        base = t.path.rpartition("/")[2]
        tmp = f".{base}.ft-j7.tmp"
        fsync = _index(e, f"fsync {t.folder}:{tmp}")
        act = _index(e, f"swap {t.folder}:{tmp},{base}" if t.pre else f"rename {t.folder}:{tmp},{base}", fsync)
        dsync = _index(e, f"sync {t.folder}:", act)
        order.append(act)
        assert fsync < act < dsync  # temp synced before its replace; the folder synced after it
    assert order == sorted(order)  # the plan's order: children first, the root last
    removes = [i for i, x in enumerate(e) if x.startswith("remove ")]
    assert len(removes) == 2
    assert min(removes) > max(order)
    close = _index(e, f"rename {JD}:{NAME}.open,{NAME}.closed")
    assert max(removes) < _index(e, "sync /w/L:", max(removes)) < close  # ...and their folders synced before close
    assert e[-1] == f"sync {JD}:"
    assert len(e) - 1 > close


def _sweep(cuts: Callable[[str], list[int | None]]) -> list[tuple[int, int | None]]:
    w, _ = clean_run()
    points: list[tuple[int, int | None]] = []
    for i, label in enumerate(w.fs.effects):
        for cut in cuts(label):
            points.append((i, cut))
    return points


def _cuts(label: str) -> list[int | None]:
    """A write is cut at every offset for a temp (§14: "a temp cut off at every offset"), at a stride for the rest."""
    if not label.startswith("write "):
        return [None]
    if ".ft-j7.tmp" in label:
        return [None, *range(64)]
    return [None, 0, 1, 2, 7, 64, 200, 500]


CRASH_POINTS = _sweep(_cuts)


@pytest.mark.parametrize(("at", "cut"), CRASH_POINTS)
def test_a_crash_at_every_effect_then_every_power_loss_leaves_a_recoverable_disk(at: int, cut: int | None) -> None:
    """§10 every row, §14: a crash at each effect (a write cut at each offset), the disk as the OS keeps it, then
    every state a power loss may leave from there."""
    w = world()
    w.fs.inject(at, crash=True, cut=cut)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    assert check_every_power_loss(w.fs, w) >= 1


def check_told(fs: MemFS, w: World, out: Outcome, *, kept: str | None = None) -> None:
    """The outcome told matches the disk (§6.4, §10, §11; INV-22), in the disk now and in every power-loss state of it.
    `kept`: the file whose outside save the save itself put back (it holds those bytes, not pre)."""
    for state in (fs, *fs.power_loss_states()):
        js = journals(state)
        match out:
            case Landed():
                assert set(js) == {State.CLOSED}
            case NotSaved():
                assert State.CLOSED not in js, "not saved, but a closed record"
                for t in w.targets:
                    if t.path != kept:
                        assert state.file(t.path) == t.pre, f"not saved, but {t.path} is not at pre"
                if not out.open_journal:
                    assert State.OPEN not in js, "an .open journal left behind and not told (writes must pause)"
                if out.unchanged:
                    assert kept is None
            case Unfinished():
                assert len(js) == 1, js
                ((st, data),) = js.items()
                assert st in (State.OPEN, State.CLOSED, State.RESOLVED), st
                assert isinstance(journal.decode(data), Record), "unfinished means a readable journal on disk"


@pytest.mark.parametrize("at", range(len(clean_run()[0].fs.effects)))
def test_an_io_error_at_every_effect_is_rolled_back_and_told_not_saved_in_every_power_loss(at: int) -> None:
    """§6.4: any failure from the `.open` journal on rolls back at once (before it, nothing was replaced: not a
    rollback); with the rollback resolving, every single I/O error is "not saved", the ledger unchanged after the
    re-hash, no `.open` journal and nothing of ours left (§6.4 last bullet, fidelity review 3 L10)."""
    w = world()
    w.fs.inject(at, crash=False, err=errno.ENOSPC)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    assert isinstance(out, NotSaved), (at, w.fs.effects[at], out)
    assert out.reason is Reason.IO_ERROR
    assert (out.unchanged, out.open_journal) == (True, False), out
    check_told(w.fs, w, out)
    assert set(journals(w.fs)) <= {State.PART, State.RESOLVED}
    if State.RESOLVED in journals(w.fs):
        check_recovered(w.fs, w, None)


FIRST_REPLACE = min(i for i, x in enumerate(clean_run()[0].fs.effects) if x.startswith(("swap /w/L", "rename /w/L")))
OPEN_RENAME = clean_run()[0].fs.effects.index(f"rename {JD}:{NAME}.part,{NAME}.open")


def _rollback_effects(at: int) -> range:
    w = world()
    w.fs.inject(at, crash=False)
    durable_commit(w.fs, w.commit)
    return range(at + 1, len(w.fs.effects))


ROLLBACK_POINTS = [(at, j) for at in range(OPEN_RENAME, len(clean_run()[0].fs.effects)) for j in _rollback_effects(at)]


@pytest.mark.parametrize(("at", "j"), ROLLBACK_POINTS)
def test_an_io_error_in_the_rollback_at_every_effect_is_told_truthfully_and_recovery_then_resolves(
    at: int, j: int
) -> None:
    """§6.4: "If the rollback fails (an I/O error included), the journal stays `.open`": unfinished from the first
    replace on, not saved (writes paused) before it; then recovery (Reopen, §6.6) resolves it."""
    w = world()
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    assert not isinstance(out, Landed)
    if at < FIRST_REPLACE:
        assert isinstance(out, NotSaved), (at, j, out)
        assert out.open_journal
    else:
        assert isinstance(out, Unfinished), (at, j, out)
    check_told(w.fs, w, out)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_recovered(state, w, recover(state))


@pytest.mark.parametrize(("at", "j"), ROLLBACK_POINTS)
def test_a_crash_at_every_effect_of_the_rollback_then_recovery_resolves(at: int, j: int) -> None:
    """§10: "A crash during a recovery or a rollback leaves the journal `.open` with each file at pre or post, so the
    next recover-all finishes it"."""
    w = world()
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))


def test_the_rollback_is_the_restore_root_first_and_resolves_after_its_syncs() -> None:
    """§6.4 "rolls back at once from the journal, as recovery does (§8.1 steps 2-4)": a failure at the read-back's
    temp removal (every file at post) is followed by the restore's effects, root first, then `.resolved` synced."""
    base, _ = clean_run()
    at = _index(base.fs.effects, "remove /w/L/inc:.a.bean.ft-j7.tmp")
    w = world()
    w.fs.inject(at, crash=False)
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.READ_BACK, Reason.IO_ERROR, True, path="/w/L/inc/a.bean")
    e = w.fs.effects[at + 1 :]
    assert [x for x in e if x.startswith("swap ")] == [
        "swap /w/L:.main.bean.ft-restore-j7.tmp,main.bean",
        "swap /w/L/inc:.a.bean.ft-restore-j7.tmp,a.bean",
    ]
    assert e[-2:] == [f"rename {JD}:{NAME}.open,{NAME}.resolved", f"sync {JD}:"]
    check_recovered(w.fs, w, None)


@pytest.mark.parametrize("after", [False, True])
def test_a_failed_close_is_rolled_back_and_never_leaves_a_closed_record_over_restored_files(after: bool) -> None:
    """S0060 verifier Q1: the close's rename or its folder sync answers an error (with `after`, it happened): the
    `.closed` record is renamed back to `.open` and synced before any file is restored, so no power loss can show a
    `.closed` record (saved) over files the rollback put back; the save is told not saved."""
    base, _ = clean_run()
    for label in (f"rename {JD}:{NAME}.open,{NAME}.closed", f"sync {JD}:"):
        at = len(base.fs.effects) - 1 if label.startswith("sync") else base.fs.effects.index(label)
        w = world()
        w.fs.inject(at, crash=False, err=errno.EIO, after=after)
        out = durable_commit(w.fs, w.commit)
        assert out == NotSaved(Step.CLOSE, Reason.IO_ERROR, True), (label, out)
        check_told(w.fs, w, out)
        check_recovered(w.fs, w, None)
        e = w.fs.effects[at + 1 :]
        if after:
            assert e[:2] == [f"rename {JD}:{NAME}.closed,{NAME}.open", f"sync {JD}:"], e[:3]
        for k in range(at + 1, len(w.fs.effects)):  # a crash anywhere in that rollback, then every power loss
            v = world()
            v.fs.inject(at, crash=False, err=errno.EIO, after=after)
            v.fs.inject(k, crash=True)
            with pytest.raises(Crash):
                durable_commit(v.fs, v.commit)
            for state in (v.fs, *v.fs.power_loss_states()):
                check_disk(state, v)  # `.closed` only with every file at post
                check_recovered(state, v, recover(state))


def test_a_full_disk_after_the_journal_is_durable_and_before_the_first_replace_is_resolved_and_not_saved() -> None:
    """§14 model finding L10, §6.4 last bullet: every file at pre, the journal `.resolved`, the temps deleted."""
    base, _ = clean_run()
    at = _index(base.fs.effects, "write /w/L/inc:.a.bean.ft-j7.tmp")
    w = world()
    w.fs.inject(at, crash=False, err=errno.ENOSPC, cut=5)
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.IO_ERROR, True, path="/w/L/inc/a.bean")
    assert set(journals(w.fs)) == {State.RESOLVED}
    assert w.fs.file(w.targets[0].temp) is None
    check_recovered(w.fs, w, None)


def test_landed_survives_every_power_loss() -> None:
    w, out = clean_run()
    assert isinstance(out, Landed)
    for state in w.fs.power_loss_states():
        assert set(journals(state)) == {State.CLOSED}
        for t in w.targets:
            assert state.file(t.path) == t.post


def _hook(fs: MemFS, label: str, act: Callable[[], None]) -> None:
    """Run `act` (an outside program) right before the first effect labelled `label`."""
    done = False

    def hook(_: int, seen: str) -> None:
        nonlocal done
        if not done and seen == label:
            done = True
            act()

    fs.on_effect = hook


def test_an_exclusive_create_refuses_a_file_that_appeared_and_keeps_it() -> None:
    w = world()
    _hook(w.fs, "rename /w/L:.prices.bean.ft-j7.tmp,prices.bean", lambda: w.fs.outside_write("/w/L/prices.bean", b"x"))
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, False, path="/w/L/prices.bean")
    assert w.fs.file("/w/L/prices.bean") == b"x"
    assert set(journals(w.fs)) == {State.RESOLVED}, "the other files rolled back (§6.4)"
    check_told(w.fs, w, out, kept="/w/L/prices.bean")


def test_a_displaced_outside_save_at_a_swap_is_swapped_back_synced_and_kept() -> None:
    """§6.4: the swap hashes what it displaced; other bytes are put back at once and the folder synced (F3), so every
    power loss keeps the outside save in place; then the other files are rolled back and the journal resolved, and the
    user is told "Your editor saved this file at the same moment" (§6.4)."""
    w = world()
    foreign = b"; saved by an editor\n"
    _hook(w.fs, "swap /w/L:.main.bean.ft-j7.tmp,main.bean", lambda: w.fs.outside_write("/w/L/main.bean", foreign))
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, False, path="/w/L/main.bean")
    assert w.fs.file("/w/L/main.bean") == foreign
    swap = "swap /w/L:.main.bean.ft-j7.tmp,main.bean"
    back = [i for i, x in enumerate(w.fs.effects) if x == swap][1]
    assert w.fs.effects[back + 1] == "sync /w/L:"  # the swap-back synced before anything else (F3)
    assert set(journals(w.fs)) == {State.RESOLVED}
    check_told(w.fs, w, out, kept="/w/L/main.bean")
    for state in w.fs.power_loss_states():
        assert state.file("/w/L/main.bean") == foreign


def test_a_crash_right_after_a_swap_that_displaced_an_outside_save_keeps_both_versions() -> None:
    """§14: "a crash right after such a swap leaves the journal stopped with both versions": the outside save is in
    the file or in our temp, never lost, in every power-loss state."""
    w = world()
    foreign = b"; saved by an editor\n"
    _hook(w.fs, "swap /w/L:.main.bean.ft-j7.tmp,main.bean", lambda: w.fs.outside_write("/w/L/main.bean", foreign))
    w.fs.inject(_index(clean_run()[0].fs.effects, "swap /w/L:.main.bean.ft-j7.tmp,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    target = w.targets[2]
    for state in [w.fs, *w.fs.power_loss_states()]:
        versions = {state.file(target.path), state.file(target.temp)}
        assert foreign in versions, "the outside save was lost"
        assert set(journals(state)) == {State.OPEN}
        out = recover(state)  # recovery stops on it (§8.1 step 2), both versions kept
        assert type(out).__name__ == "Stopped", out
        assert foreign in {state.file(target.path), state.file(target.temp)}
        assert set(journals(state)) == {State.OPEN}


def _swap_folder_for_link(w: World) -> Callable[[], None]:
    w.fs.outside_mkdirs("/w/elsewhere/inc")
    w.fs.outside_write("/w/elsewhere/inc/a.bean", b"elsewhere\n")

    def act() -> None:
        w.fs.outside_move("/w/L", "/w/L-moved")
        w.fs.outside_symlink("/w/L", "/w/elsewhere")

    return act


def test_the_path_guard_refuses_a_folder_replaced_by_a_link_after_the_journal() -> None:
    """§6.3, INV-32: the save writes a recorded path only while it still resolves to itself; a folder moved away and
    replaced by a link between the journal and the replace stops the save, and nothing is written through the link."""
    w = world()
    _hook(w.fs, f"sync {JD}:", _swap_folder_for_link(w))  # right after the journal's rename to .open
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.PATH_MOVED, False, open_journal=True, path="/w/L/inc")
    assert w.fs.file("/w/elsewhere/inc/a.bean") == b"elsewhere\n"
    assert [p for p in w.fs.snapshot() if p.startswith("/w/elsewhere/") and ".ft-" in p] == []
    for t in w.targets:
        assert w.fs.file(t.path.replace("/w/L/", "/w/L-moved/")) == t.pre


def test_a_folder_swapped_for_a_link_between_the_check_and_the_write_is_still_written_through_its_descriptor() -> None:
    """§6.3: the check and the write use one descriptor, so a link put in the folder's place after the check is never
    followed: the write lands in the folder that was checked (now moved), and the next check stops the save."""
    w = world()
    _hook(w.fs, "create /w/L/inc:.a.bean.ft-j7.tmp", _swap_folder_for_link(w))
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished)
    assert out.reason is Reason.PATH_MOVED
    assert w.fs.file("/w/elsewhere/inc/a.bean") == b"elsewhere\n"
    assert [p for p in w.fs.snapshot() if p.startswith("/w/elsewhere/") and ".ft-" in p] == []
    assert w.fs.file("/w/L-moved/inc/a.bean") == w.targets[0].post  # the checked folder's file, by its descriptor


def test_the_path_guard_checks_through_the_descriptor_that_writes() -> None:
    """A folder replaced by a link before the handle is opened is refused too: the handle opened through the link
    reports where it really is."""
    w = world()
    w.fs.outside_mkdirs("/w/elsewhere")

    def swap_folder() -> None:
        w.fs.outside_move("/w/L/inc", "/w/inc-moved")
        w.fs.outside_symlink("/w/L/inc", "/w/elsewhere")

    _hook(w.fs, f"mkdir {BD}:{NAME}", swap_folder)  # after resolution, before any handle on /w/L/inc
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.PATH_MOVED, False, path="/w/L/inc")
    assert journals(w.fs) == {}


def test_the_journal_and_backup_folders_may_be_named_through_a_link() -> None:
    """The caller's spelling of its own folders is resolved first (on macOS `/var` is a link to `/private/var`), so
    the path guard compares canonical paths; a missing journal folder refuses with nothing written."""
    w = world()
    w.fs.outside_symlink("/w/locallink", "/w/local")
    c = replace(w.commit, journal_dir="/w/locallink/journal", backups_dir="/w/locallink/backups")
    out = durable_commit(w.fs, c)
    assert isinstance(out, Landed)
    assert out.record.backup_dir == SAVE_DIR
    assert set(journals(w.fs)) == {State.CLOSED}
    w2 = world()
    out2 = durable_commit(w2.fs, replace(w2.commit, journal_dir="/w/local/nowhere"))
    assert out2 == NotSaved(Step.BACKUP, Reason.IO_ERROR, True)
    assert w2.fs.effects == []


def test_the_replaced_file_keeps_its_mode() -> None:
    w = world(mode=0o600)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    assert w.fs.snapshot()["/w/L/main.bean"][2] == 0o600


def test_a_symlinked_file_is_written_at_its_real_path() -> None:
    """ADR-0008 step 5: the link stays a link; its real file gets the post bytes."""
    w = world()
    w.fs.outside_mkdirs("/w/real")
    w.fs.outside_write("/w/real/r.bean", b"pre\n")
    w.fs.outside_symlink("/w/L/r.bean", "/w/real/r.bean")
    c = replace(w.commit, changes=(Change("/w/L/r.bean", sha256(b"pre\n"), b"post\n"),))
    out = durable_commit(w.fs, c)
    assert isinstance(out, Landed)
    assert out.record.files[0].canonical_path == "/w/real/r.bean"
    assert w.fs.snapshot()["/w/L/r.bean"] == ("link", "/w/real/r.bean")
    assert w.fs.file("/w/real/r.bean") == b"post\n"


def test_a_created_file_in_a_created_folder_lands_and_its_folder_is_recorded() -> None:
    w = world(new_dir=True)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed)
    assert [d.canonical_path for d in out.record.created_dirs] == ["/w/L/y2026"]
    assert [d.path_key for d in out.record.created_dirs] == ["/w/l/y2026"]
    assert w.fs.file("/w/L/y2026/prices.bean") == w.targets[1].post
    mk = _index(w.fs.effects, "mkdir /w/L:y2026")
    assert _index(w.fs.effects, "sync /w/L:", mk) < _index(w.fs.effects, "create /w/L/y2026:.prices.bean.ft-j7.tmp")


def _effect_count(w: World) -> int:
    durable_commit(w.fs, w.commit)
    return len(w.fs.effects)


NEW_DIR_POINTS = [(i, None) for i in range(_effect_count(world(new_dir=True)))]


@pytest.mark.parametrize(("at", "cut"), NEW_DIR_POINTS)
def test_a_crash_at_every_effect_of_a_save_that_creates_a_folder_leaves_a_recoverable_disk(
    at: int, cut: int | None
) -> None:
    w = world(new_dir=True)
    w.fs.inject(at, crash=True, cut=cut)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)


def test_a_folder_that_appears_before_the_save_makes_it_stops_the_save_and_is_left_alone() -> None:
    w = world(new_dir=True)
    _hook(w.fs, "mkdir /w/L:y2026", lambda: w.fs.outside_mkdirs("/w/L/y2026"))
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, True, path="/w/L/y2026")
    assert w.fs.snapshot()["/w/L/y2026"] == ("dir",), "a folder the save did not make is never removed"
    assert set(journals(w.fs)) == {State.RESOLVED}
    assert [p for p in w.fs.snapshot() if p.startswith("/w/L/y2026/")] == []


def test_a_file_not_at_its_planned_pre_bytes_refuses_before_anything_is_replaced() -> None:
    w = world()
    w.fs.outside_write("/w/L/main.bean", b"; edited after the plan\n")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(
        Step.BACKUP, Reason.CHANGED, False, path="/w/L/main.bean"
    )  # not "unchanged": a file differs from the plan
    assert journals(w.fs) == {}
    assert w.fs.file("/w/L/inc/a.bean") == w.targets[0].pre


def test_a_file_the_save_creates_that_already_exists_refuses_before_anything_is_written() -> None:
    w = world()
    w.fs.outside_write("/w/L/prices.bean", b"already here\n")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, False, path="/w/L/prices.bean")
    assert w.fs.effects == []


def test_a_backup_corrupted_before_its_read_back_refuses_with_nothing_replaced() -> None:
    """ADR-0012 F-1: each backup is read back and hashed against the planned pre hash."""
    w = world()

    def corrupt(_: int, label: str) -> None:
        if label == f"fsync {SAVE_DIR}:0-a.bean":
            w.fs.on_effect = None
            w.fs.outside_write(f"{SAVE_DIR}/0-a.bean", b"bit rot\n")

    w.fs.on_effect = corrupt
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.IO_ERROR, True, path="/w/L/inc/a.bean")
    assert journals(w.fs) == {}


def test_a_read_back_mismatch_stops_the_save_unfinished() -> None:
    """ADR-0008 step 6: an outside write after the last replace and before the read-back is caught."""
    w = world()

    def late(_: int, label: str) -> None:
        if label == "sync /w/L:" and w.fs.file("/w/L/main.bean") == w.targets[2].post:
            w.fs.on_effect = None
            w.fs.outside_write("/w/L/inc/a.bean", b"; late outside edit\n")

    w.fs.on_effect = late
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.READ_BACK, Reason.READ_BACK, "/w/L/inc/a.bean")
    assert set(journals(w.fs)) == {State.OPEN}


def test_a_malformed_plan_raises_before_any_effect() -> None:
    w = world()
    for bad in (
        replace(w.commit, changes=()),
        replace(w.commit, job="j-7"),
        replace(w.commit, changes=(Change("relative.bean", None, b""),)),
        replace(w.commit, changes=(Change("/w/L/main.bean", "abc", b""),)),
    ):
        with pytest.raises(ValueError, match=r"."):
            durable_commit(w.fs, bad)
    assert w.fs.effects == []


def test_two_paths_of_one_file_are_refused_as_a_malformed_plan() -> None:
    """One entry per `path_key`: a link and its target name one file."""
    w = world()
    w.fs.outside_symlink("/w/L/alias.bean", "/w/L/main.bean")
    pre = sha256(w.targets[2].pre or b"")
    c = replace(w.commit, changes=(Change("/w/L/main.bean", pre, b"1"), Change("/w/L/alias.bean", pre, b"2")))
    with pytest.raises(ValueError, match="twice"):
        durable_commit(w.fs, c)
    assert w.fs.effects == []


# ---- S0060 verifier round 1 and data-safety red team round 1 ------------------------------------------------------


def test_a_hard_linked_ledger_file_refuses_the_save_before_anything_is_written() -> None:
    """Owner, 2026-10-29: "Refuse to save it (Recommended)" (ADR-0013 amendment): the swap would leave the other
    link with the old bytes, so a file with a second hard link is refused, the message naming it (red team 1)."""
    w = world()
    w.fs.outside_mkdirs("/w/Backup")
    w.fs.outside_link("/w/L/main.bean", "/w/Backup/copy.bean")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.HARD_LINKED, True, path="/w/L/main.bean")
    assert w.fs.effects == []
    assert w.fs.file("/w/Backup/copy.bean") == w.targets[2].pre


def test_a_hard_link_made_after_resolution_still_refuses_before_any_ledger_file_is_replaced() -> None:
    w = world()
    w.fs.outside_mkdirs("/w/Backup")
    _hook(w.fs, f"mkdir {BD}:{NAME}", lambda: w.fs.outside_link("/w/L/main.bean", "/w/Backup/copy.bean"))
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.HARD_LINKED, True, path="/w/L/main.bean")
    assert journals(w.fs) == {}


def test_a_locked_ledger_file_refuses_the_save_before_anything_is_written() -> None:
    """Red team 10: a `uchg` file would fail at the swap after the journal; it is refused first."""
    w = world()
    w.fs.outside_lock("/w/L/main.bean")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.LOCKED, True, path="/w/L/main.bean")
    assert w.fs.effects == []


def test_a_fifo_at_a_ledger_path_refuses_without_opening_it() -> None:
    """Verifier F4, red team 8: a FIFO is never opened (it would block for ever)."""
    w = world()
    w.fs.outside_fifo("/w/L/other.fifo")
    c = replace(w.commit, changes=(Change("/w/L/other.fifo", "0" * 64, b"x"),))
    out = durable_commit(w.fs, c)
    assert out == NotSaved(Step.BACKUP, Reason.NOT_A_FILE, False, path="/w/L/other.fifo")
    assert w.fs.effects == []


def _with_extra(w: World, *extra: Change) -> Commit:
    ch = w.commit.changes
    return replace(w.commit, changes=(ch[0], ch[1], *extra, ch[2]))


def test_two_created_files_in_one_created_folder_land_and_the_folder_is_made_once() -> None:
    """Verifier F1, red team 3."""
    w = world(new_dir=True)
    out = durable_commit(w.fs, _with_extra(w, Change("/w/L/y2026/tx.bean", None, b"2026-01-04 *\n")))
    assert isinstance(out, Landed)
    assert [d.canonical_path for d in out.record.created_dirs] == ["/w/L/y2026"]
    assert w.fs.effects.count("mkdir /w/L:y2026") == 1
    assert w.fs.file("/w/L/y2026/tx.bean") == b"2026-01-04 *\n"


def test_nested_created_folders_shared_by_two_files_are_made_once_each_parents_first() -> None:
    w = world()
    extra = (Change("/w/L/new/sub/a.bean", None, b"a\n"), Change("/w/L/new/b.bean", None, b"b\n"))
    out = durable_commit(w.fs, _with_extra(w, *extra))
    assert isinstance(out, Landed)
    assert [d.canonical_path for d in out.record.created_dirs] == ["/w/L/new", "/w/L/new/sub"]
    assert w.fs.effects.count("mkdir /w/L:new") == 1
    assert w.fs.effects.count("mkdir /w/L/new:sub") == 1


def test_created_paths_that_collide_are_refused_before_the_first_effect() -> None:
    """Verifier F2: a created file that is another target's folder, and two created folders that differ only in case
    on a case-insensitive volume."""
    w = world()
    for extra in (
        (Change("/w/L/x", None, b"x\n"), Change("/w/L/x/y.bean", None, b"y\n")),
        (Change("/w/L/New/a.bean", None, b"a\n"), Change("/w/L/NEW/b.bean", None, b"b\n")),
    ):
        with pytest.raises(ValueError, match=r"file_inside_file|differ_in_case"):
            durable_commit(w.fs, _with_extra(w, *extra))
    assert w.fs.effects == []


def test_an_entry_standing_at_a_created_path_refuses_before_the_first_effect() -> None:
    """Verifier F3, red team 5: a dangling link resolves as on macOS (its own name is the missing part) and is
    refused, never written through."""
    w = world()
    w.fs.outside_mkdirs("/w/far")
    w.fs.outside_symlink("/w/L/prices.bean", "/w/far/missing.bean")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, False, path="/w/L/prices.bean")
    assert w.fs.effects == []
    assert w.fs.file("/w/far/missing.bean") is None


def test_a_failed_exclusive_rename_with_nothing_replaced_is_not_saved() -> None:
    """Red team 5: a file that appears at the only created path before its rename: nothing was replaced."""
    w = world()
    c = replace(w.commit, changes=(w.commit.changes[1],))
    _hook(w.fs, "rename /w/L:.prices.bean.ft-j7.tmp,prices.bean", lambda: w.fs.outside_write("/w/L/prices.bean", b"x"))
    out = durable_commit(w.fs, c)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, False, path="/w/L/prices.bean")
    assert set(journals(w.fs)) == {State.RESOLVED}
    assert w.fs.file("/w/L/prices.bean") == b"x"


def test_an_outside_write_into_the_displaced_file_after_the_swap_is_kept_and_the_save_stops() -> None:
    """Red team 2: a program that held main.bean open writes through its descriptor after the displaced check; its
    bytes are in our temp, which is kept (§6.4), and the save does not say saved."""
    w = world()
    fs = w.fs
    d = fs._dir(fs._walk("/w/L")[1])  # pyright: ignore[reportPrivateUsage]
    held = d.entries["main.bean"][1]
    foreign = b"; written through an open descriptor\n"

    def hook(_: int, label: str) -> None:
        if label == "sync /w/L:" and fs.file("/w/L/main.bean") == w.targets[2].post:
            fs.on_effect = None
            node = fs.nodes[held]
            node.data = node.synced = foreign  # type: ignore[union-attr]

    fs.on_effect = hook
    out = durable_commit(fs, w.commit)
    assert out == Unfinished(Step.READ_BACK, Reason.CHANGED, "/w/L/main.bean")
    assert fs.file(w.targets[2].temp) == foreign
    assert set(journals(fs)) == {State.OPEN}


def test_a_file_replaced_by_another_with_the_same_bytes_before_the_swap_is_not_swapped_over() -> None:
    """The displaced entry must be the object resolution found (`st_dev`, `st_ino`), not only the same bytes: an
    editor's save by rename puts a new file under the name."""
    w = world()
    pre = w.targets[2].pre or b""
    replaced = False

    def hook(_: int, label: str) -> None:
        nonlocal replaced
        if label == "create /w/L:.main.bean.ft-j7.tmp" and not replaced:
            replaced = True
            w.fs.outside_write("/w/L/main.bean", pre, new_inode=True)

    w.fs.on_effect = hook
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, True, path="/w/L/main.bean")
    assert w.fs.file("/w/L/main.bean") == pre
    assert set(journals(w.fs)) == {State.RESOLVED}


def test_empty_record_fields_are_refused_before_the_first_effect() -> None:
    """Verifier F5."""
    w = world()
    with pytest.raises(ValueError, match="empty_record_field"):
        durable_commit(w.fs, replace(w.commit, lineage=""))
    assert w.fs.effects == []


def test_targets_in_the_app_folders_and_app_folders_in_the_ledger_folder_are_refused() -> None:
    """Red team 6 (INV-27, INV-30): a target inside the journal or backup folder; a journal folder inside a folder
    the save writes in."""
    w = world()
    w.fs.outside_mkdirs("/w/L/local/journal")
    for bad in (
        replace(w.commit, changes=(Change(f"{JD}/{NAME}.closed", None, b"x\n"),)),
        replace(w.commit, changes=(Change(f"{BD}/x.bean", None, b"x\n"),)),
        replace(w.commit, journal_dir="/w/L/local/journal"),
    ):
        with pytest.raises(ValueError, match="overlap"):
            durable_commit(w.fs, bad)
    assert w.fs.effects == []


def test_a_target_at_another_targets_temp_name_is_refused() -> None:
    """Red team 7: recovery deletes temps, so no ledger file may sit at one."""
    w = world()
    c = replace(w.commit, changes=(Change("/w/L/.main.bean.ft-j7.tmp", None, b"x\n"), w.commit.changes[2]))
    with pytest.raises(ValueError, match="temp_name_taken"):
        durable_commit(w.fs, c)
    assert w.fs.effects == []


def test_the_replaced_file_keeps_its_extended_attributes() -> None:
    """Red team 10: Finder tags and other extended attributes go with the swap."""
    w = world()
    w.fs.outside_setxattr("/w/L/main.bean", "com.apple.metadata:_kMDItemUserTags", b"tag")
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    assert w.fs.xattrs("/w/L/main.bean") == {"com.apple.metadata:_kMDItemUserTags": b"tag"}


def test_a_file_whose_derived_names_would_pass_name_max_lands() -> None:
    """Red team 11: the temp and backup names are shortened by a hash past NAME_MAX (255 bytes)."""
    w = world()
    long = "/w/L/" + "a" * 245 + ".bean"  # `.<name>.ft-j7.tmp` and `0-<name>` would pass 255
    w.fs.outside_write(long, b"pre\n")
    out = durable_commit(w.fs, replace(w.commit, changes=(Change(long, sha256(b"pre\n"), b"post\n"),)))
    assert isinstance(out, Landed)
    f = out.record.files[0]
    assert f.backup is not None
    assert len(f.temp.encode()) <= 255
    assert len(f.backup.encode()) <= 255
    assert w.fs.file(long) == b"post\n"


@pytest.mark.parametrize("at", range(len(clean_run()[0].fs.effects)))
def test_an_effect_that_happens_but_answers_an_error_is_told_truthfully(at: int) -> None:
    """@regression-guard (S0060 red team 12: the injection was missing, the writer already held): an operation that
    took place and then answered EIO (a swap-back included)."""
    w = world()
    w.fs.inject(at, crash=False, err=errno.EIO, after=True)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    if isinstance(out, NotSaved):
        for t in w.targets:
            assert w.fs.file(t.path) == t.pre, "not saved, but a ledger file changed"
        assert out.unchanged
    assert not isinstance(out, Landed), "an operation that answered an error is never told saved"


WRITE_POINTS = [(i, cut) for i, cut in CRASH_POINTS if cut is not None]


@pytest.mark.parametrize(("at", "cut"), WRITE_POINTS)
def test_an_io_error_part_way_through_a_write_is_told_truthfully(at: int, cut: int) -> None:
    """@regression-guard (S0060 red team 12: the injection was missing, the writer already held): a write that wrote
    `cut` bytes and then failed (a full disk)."""
    w = world()
    w.fs.inject(at, crash=False, cut=cut)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    assert not isinstance(out, Landed)
    if isinstance(out, NotSaved):
        assert out.unchanged


def test_a_file_replaced_by_one_with_the_same_bytes_before_its_backup_refuses_with_nothing_replaced() -> None:
    """The backup re-checks that the entry is still the object resolution found (`st_dev`, `st_ino`): a save by
    rename with equal bytes after resolution is a changed file, refused before anything is replaced."""
    w = world()
    pre = w.targets[2].pre or b""
    _hook(w.fs, f"mkdir {BD}:{NAME}", lambda: w.fs.outside_write("/w/L/main.bean", pre, new_inode=True))
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/main.bean")
    assert journals(w.fs) == {}


def test_a_replaced_file_that_vanished_refuses_before_the_first_effect() -> None:
    w = world()
    w.fs.outside_mkdirs("/w/gone")
    w.fs.outside_move("/w/L/inc/a.bean", "/w/gone/a.bean")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, False, path="/w/L/inc/a.bean")
    assert w.fs.effects == []


# ---- S0060 verifier round 2 and data-safety red team round 2 ------------------------------------------------------


def test_a_second_outside_save_before_the_swap_back_is_kept_and_nothing_is_swapped_again() -> None:
    """@regression-guard (S0060 red team r2 M1: 4c275f57 already ended this way; the swap-back's displaced entry is
    now checked as well). An outside save by rename during our temp's write is displaced by our swap; a second one takes
    the name before the swap-back. The swap-back's displaced entry is then not our temp: the save stops at once,
    with the first outside save in the file and the second in our temp, and swaps nothing more."""
    w = world()
    first, second = b"; outside save 1\n", b"; outside save 2\n"
    swaps = 0

    def on_swap(i: int, label: str) -> None:
        nonlocal swaps
        if label == "create /w/L:.main.bean.ft-j7.tmp":
            w.fs.outside_write("/w/L/main.bean", first, new_inode=True)
        if label == "swap /w/L:.main.bean.ft-j7.tmp,main.bean":
            swaps += 1
            if swaps == 2:  # right before the swap-back
                w.fs.outside_write("/w/L/main.bean", second, new_inode=True)

    w.fs.on_effect = on_swap
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/main.bean")
    assert swaps == 2
    assert w.fs.file("/w/L/main.bean") == first
    assert w.fs.file(w.targets[2].temp) == second
    for state in w.fs.power_loss_states():
        assert {state.file("/w/L/main.bean"), state.file(w.targets[2].temp)} == {first, second}


def test_a_hard_link_made_after_the_journal_refuses_before_that_file_is_swapped() -> None:
    """@regression-guard (S0060 verifier r2 F5, its proposed test: it kills the mutant that drops the check before the
    swap)."""
    w = world()
    w.fs.outside_mkdirs("/w/Backup")
    _hook(w.fs, f"sync {JD}:", lambda: w.fs.outside_link("/w/L/inc/a.bean", "/w/Backup/copy.bean"))
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.HARD_LINKED, True, path="/w/L/inc/a.bean")
    assert set(journals(w.fs)) == {State.RESOLVED}
    assert w.fs.file("/w/Backup/copy.bean") == w.fs.file("/w/L/inc/a.bean")


def test_a_hard_link_made_between_the_pre_swap_check_and_the_swap_is_never_told_saved() -> None:
    """Verifier r2 F1, red team r2 M2 (the verifier's proposed test, with the outcome pinned): the displaced file has
    a second link, so it is swapped back and both names keep the pre bytes."""
    w = world()
    w.fs.outside_mkdirs("/w/Backup")
    _hook(w.fs, "create /w/L:.main.bean.ft-j7.tmp", lambda: w.fs.outside_link("/w/L/main.bean", "/w/Backup/copy.bean"))
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.HARD_LINKED, True, path="/w/L/main.bean")
    assert w.fs.file("/w/L/main.bean") == w.fs.file("/w/Backup/copy.bean") == w.targets[2].pre
    assert set(journals(w.fs)) == {State.RESOLVED}


def test_a_hard_link_made_to_the_displaced_file_before_its_temp_is_removed_stops_the_save() -> None:
    """Red team r2 M2, second timing: after the swap, a link to the displaced file (now our temp)."""
    w = world()
    w.fs.outside_mkdirs("/w/Backup")

    def link_late(_: int, label: str) -> None:
        if label == "sync /w/L:" and w.fs.file("/w/L/main.bean") == w.targets[2].post:
            w.fs.on_effect = None
            w.fs.outside_link(w.targets[2].temp, "/w/Backup/copy.bean")

    w.fs.on_effect = link_late
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.READ_BACK, Reason.HARD_LINKED, True, path="/w/L/main.bean")
    # rolled back: the temp holds the pre bytes (§8.1 step 2 allows it); its other name keeps them
    assert w.fs.file("/w/Backup/copy.bean") == w.targets[2].pre
    assert set(journals(w.fs)) == {State.RESOLVED}


@pytest.mark.parametrize(
    ("where", "right"),
    [
        ("/w/L", Right.CHANGE_ENTRIES),  # a read-only ledger folder
        ("/w/L/main.bean", Right.DELETE),  # an ACL: deny delete on the file
        ("/w/L", Right.DELETE),  # an ACL: deny delete_child on the folder
        ("/w/L/main.bean", Right.READ_XATTRS),  # an ACL: deny readextattr (the tags could not be copied)
    ],
)
def test_permissions_that_would_block_the_swap_or_its_clean_up_refuse_before_the_first_effect(
    where: str, right: Right
) -> None:
    """Verifier r2 F2, red team r2 M3 and L3: refused up front, "not saved", the ledger unchanged."""
    w = world()
    w.fs.outside_deny(where, right)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved)
    assert (out.reason, out.unchanged, out.open_journal) == (Reason.PERMISSION, True, False)
    assert out.path in ("/w/L/main.bean", "/w/L/prices.bean")
    assert w.fs.effects == []


def test_a_ledger_on_a_volume_without_the_swap_refuses_before_the_first_effect() -> None:
    """Verifier r2 F3, red team r2 M4; owner, 2026-10-29: refused until the fallback of BL-0160 exists."""
    w = world()
    w.fs.outside_mkdirs("/w/X", no_swap=True)
    w.fs.outside_write("/w/X/main.bean", b"pre\n")
    out = durable_commit(w.fs, replace(w.commit, changes=(Change("/w/X/main.bean", sha256(b"pre\n"), b"post\n"),)))
    assert out == NotSaved(Step.BACKUP, Reason.VOLUME_NO_SWAP, True, path="/w/X/main.bean")
    assert w.fs.effects == []


def test_two_targets_with_one_temp_name_or_a_folder_at_a_temp_name_are_refused() -> None:
    """Red team r2 L1: a long name's hashed temp equal to another target's temp; a created folder at a temp name."""
    w = world()
    long = "a" * 250
    sha_temp = journal.temp_name(long, "j7")  # `.<sha16>.ft-j7.tmp`
    w.fs.outside_write(f"/w/L/{long}", b"x\n")
    twin = sha_temp[1 : -len(".ft-j7.tmp")]  # a file literally named <sha16>: its temp is the same name
    for bad in (
        replace(
            w.commit, changes=(Change(f"/w/L/{long}", sha256(b"x\n"), b"y\n"), Change(f"/w/L/{twin}", None, b"z\n"))
        ),
        replace(w.commit, changes=(w.commit.changes[2], Change("/w/L/.main.bean.ft-j7.tmp/x.bean", None, b"z\n"))),
    ):
        with pytest.raises(ValueError, match="temp_name_taken"):
            durable_commit(w.fs, bad)
    assert w.fs.effects == []


def test_an_entry_already_at_a_temp_name_refuses_before_the_first_effect() -> None:
    """Red team r2 L2: the journal never names a temp the save did not make, so recovery cannot delete a stranger."""
    w = world()
    w.fs.outside_write("/w/L/.main.bean.ft-j7.tmp", b"someone else's\n")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/main.bean")
    assert w.fs.effects == []
    assert w.fs.file("/w/L/.main.bean.ft-j7.tmp") == b"someone else's\n"


def test_a_failure_before_the_created_folder_is_made_is_not_saved_and_unchanged() -> None:
    """@regression-guard (S0060 verifier r2 F6, its proposed test: it kills the mutant that answers False for a folder
    never made)."""
    w = world(new_dir=True)
    probe = world(new_dir=True)
    durable_commit(probe.fs, probe.commit)
    at = next(i for i, e in enumerate(probe.fs.effects) if e.startswith("rename ") and e.endswith(".open"))
    w.fs.inject(at, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved)
    assert out.unchanged, "every target is at its pre bytes and the created file's folder was never made"
    assert w.fs.file("/w/L/y2026/prices.bean") is None


def test_an_io_error_names_the_file_the_writer_was_on() -> None:
    """Verifier r2 F8: an `OSError` outcome carries the target it happened at."""
    base, _ = clean_run()
    for label, path in (
        (f"fsync {SAVE_DIR}:2-main.bean", "/w/L/main.bean"),
        ("fsync /w/L/inc:.a.bean.ft-j7.tmp", "/w/L/inc/a.bean"),
        ("swap /w/L:.main.bean.ft-j7.tmp,main.bean", "/w/L/main.bean"),
        (f"rename {JD}:{NAME}.part,{NAME}.open", None),
    ):
        w = world()
        w.fs.inject(base.fs.effects.index(label), crash=False)
        out = durable_commit(w.fs, w.commit)
        assert isinstance(out, NotSaved | Unfinished)
        assert out.path == path, (label, out)


def test_a_folder_name_that_is_not_utf8_refuses_as_an_outcome_before_the_first_effect() -> None:
    """Red team r2 overflow: a name decoded with surrogates (not UTF-8 on disk) cannot be written in the journal."""
    w = world()
    bad = "/w/L/x\udcff.bean"
    w.fs.outside_write(bad, b"pre\n")
    out = durable_commit(w.fs, replace(w.commit, changes=(Change(bad, sha256(b"pre\n"), b"post\n"),)))
    assert isinstance(out, NotSaved)
    assert out.reason is Reason.NAME_NOT_UTF8
    assert w.fs.effects == []


def test_a_file_created_in_a_read_only_folder_refuses_before_the_first_effect() -> None:
    """Verifier r2 F2 for a created file: only the folder's own right applies (no file to delete)."""
    w = world()
    w.fs.outside_mkdirs("/w/RO")
    w.fs.outside_deny("/w/RO", Right.CHANGE_ENTRIES)
    out = durable_commit(w.fs, replace(w.commit, changes=(Change("/w/RO/new.bean", None, b"x\n"),)))
    assert out == NotSaved(Step.BACKUP, Reason.PERMISSION, True, path="/w/RO/new.bean")
    assert w.fs.effects == []


def test_a_target_at_a_restore_temp_name_or_an_entry_standing_at_one_is_refused_before_the_first_effect() -> None:
    """S0061: recovery deletes restore temps without a check (§6.4), so no ledger file may sit at one, and a save
    never starts while a stranger stands at one of its restore temps."""
    w = world()
    c = replace(w.commit, changes=(Change("/w/L/.main.bean.ft-restore-j7.tmp", None, b"x\n"), w.commit.changes[2]))
    with pytest.raises(ValueError, match="temp_name_taken"):
        durable_commit(w.fs, c)
    w.fs.outside_write("/w/L/.main.bean.ft-restore-j7.tmp", b"someone else's\n")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/main.bean")
    assert w.fs.effects == []
    w2 = world()
    w2.fs.outside_write("/w/L/.prices.bean.ft-restore-j7.tmp", b"someone else's\n")
    assert durable_commit(w2.fs, w2.commit) == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/prices.bean")


def test_the_rollback_of_a_save_that_made_a_folder_removes_it_and_leaves_nothing_of_ours() -> None:
    """§6.4, §8.1 step 3: a failure after the created file landed in its new folder: the rollback removes the file
    and the folder the save made."""
    probe = world(new_dir=True)
    durable_commit(probe.fs, probe.commit)
    for at in range(probe.fs.effects.index(f"rename {JD}:{NAME}.part,{NAME}.open"), len(probe.fs.effects)):
        w = world(new_dir=True)
        w.fs.inject(at, crash=False, err=errno.EIO, after=True)
        out = durable_commit(w.fs, w.commit)
        assert isinstance(out, NotSaved), (at, probe.fs.effects[at], out)
        assert (out.unchanged, out.open_journal) == (True, False)
        assert "/w/L/y2026" not in w.fs.snapshot(), probe.fs.effects[at]
        check_recovered(w.fs, w, None)


def test_a_rollback_that_cannot_tell_whether_the_journal_exists_tries_the_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer's own look at the journal folder fails: it never assumes there is no `.open` record."""
    from tests.fakes.memfs import MemDir

    base, _ = clean_run()
    at = _index(base.fs.effects, "swap /w/L/inc:.a.bean.ft-j7.tmp,a.bean")
    w = world()
    w.fs.inject(at, crash=False)
    real = MemDir.stat
    calls = 0

    def failing(self: MemDir, name: str) -> object:
        nonlocal calls
        if name == f"{NAME}.open":
            calls += 1
            if calls == 1:
                raise OSError(errno.EIO, "injected stat error")
        return real(self, name)

    monkeypatch.setattr(MemDir, "stat", failing)
    out = durable_commit(w.fs, w.commit)
    assert calls >= 1
    check_told(w.fs, w, out)
    assert set(journals(w.fs)) == {State.RESOLVED}
