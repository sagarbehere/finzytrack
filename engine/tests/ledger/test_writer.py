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

from ledger_engine.fs.port import AclEntry, Right
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.restore import Stopped
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
    check_taken,
    journals,
    recover,
    undo,
    world,
)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

PRICES = "/w/L/prices.bean"  # the world's created file: anything there is never removed (owner, 2026-10-30)
TAKEN = Stopped(Cause.CREATED_PATH_TAKEN, PRICES)


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
    first_replace = min(i for i, x in enumerate(e) if _is_replace(x))
    assert jsync < first_replace  # the .open journal durable before the first replace
    order: list[int] = []
    for t in w.targets:
        base = t.path.rpartition("/")[2]
        tmp, new = f".{base}.ft-s1-j7.tmp", f".{base}.ft-s1-j7.new"
        fsync = _index(e, f"fsync {t.folder}:{new}")
        named = _index(e, f"rename {t.folder}:{new},{tmp}", fsync)
        nsync = _index(e, f"sync {t.folder}:", named)
        act = _index(e, f"swap {t.folder}:{tmp},{base}" if t.pre else f"rename {t.folder}:{tmp},{base}", fsync)
        dsync = _index(e, f"sync {t.folder}:", act)
        order.append(act)
        # the new bytes written and synced under the staging name, renamed to the temp name, that rename made durable
        # by the folder's sync, and only then the replace (owner, 2026-10-30: "Staging name"); the folder synced after
        assert fsync < named < nsync < act < dsync
        assert not any(x.startswith(f"create {t.folder}:{tmp}") for x in e), "nothing is ever written at a temp name"
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
    if ".ft-s1-j7.new" in label:  # the staging write (owner, 2026-10-30: "Staging name")
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
    re-hash, no `.open` journal and nothing of ours left (§6.4 last bullet, fidelity review 3 L10). Once the save has
    placed its created file, the rollback leaves it and stops (owner, 2026-10-30: "Never remove it (Recommended)"):
    unfinished, every other file at pre, until the user deletes it and recovery resolves."""
    w = world()
    w.fs.inject(at, crash=False, err=errno.ENOSPC)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    if at > PLACED:
        assert isinstance(out, Unfinished), (at, w.fs.effects[at], out)
        assert out.reason is Reason.IO_ERROR
        check_told(w.fs, w, out)
        check_recovered(w.fs, w, recover(w.fs))
        return
    assert isinstance(out, NotSaved), (at, w.fs.effects[at], out)
    assert out.reason is Reason.IO_ERROR
    assert (out.unchanged, out.open_journal) == (True, False), out
    check_told(w.fs, w, out)
    assert set(journals(w.fs)) <= {State.PART, State.RESOLVED}
    if State.RESOLVED in journals(w.fs):
        check_recovered(w.fs, w, None)


def _is_replace(label: str) -> bool:
    """A swap with a ledger file, or the exclusive rename of a temp onto one (not a staging file's rename to its temp
    name, which replaces nothing)."""
    return label.startswith("swap /w/L") or (label.startswith("rename /w/L") and not label.endswith(".tmp"))


FIRST_REPLACE = min(i for i, x in enumerate(clean_run()[0].fs.effects) if _is_replace(x))
PLACED = clean_run()[0].fs.effects.index("rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean")  # the created file's
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
    at = _index(base.fs.effects, "remove /w/L/inc:.a.bean.ft-s1-j7.tmp")
    w = world()
    w.fs.inject(at, crash=False)
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.READ_BACK, Reason.IO_ERROR, "/w/L/inc/a.bean")  # the created file is left (r5)
    e = w.fs.effects[at + 1 :]
    assert [x for x in e if x.startswith("swap ")] == [
        "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean",
        "swap /w/L/inc:.a.bean.ft-restore-s1-j7.tmp,a.bean",
    ]
    assert not [x for x in e if x.startswith(f"rename {JD}:")], "stopped at the created path: never resolved"
    assert sorted(e[-2:]) == ["sync /w/L/inc:", "sync /w/L:"], "every folder synced before the stop"
    check_recovered(w.fs, w, recover(w.fs))


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
        end = len(w.fs.effects)
        assert out == Unfinished(Step.CLOSE, Reason.IO_ERROR, None), (label, out)  # the created file is left (r5)
        if after:
            assert set(journals(w.fs)) == {State.OPEN}, "the .closed record reopened before any file was restored"
        check_told(w.fs, w, out)
        check_recovered(w.fs, w, recover(w.fs))
        e = w.fs.effects[at + 1 :]
        if after:
            assert e[:2] == [f"rename {JD}:{NAME}.closed,{NAME}.open", f"sync {JD}:"], e[:3]
        for k in range(at + 1, end):  # a crash anywhere in that rollback, then every power loss
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
    at = _index(base.fs.effects, "write /w/L/inc:.a.bean.ft-s1-j7.new")
    w = world()
    w.fs.inject(at, crash=False, err=errno.ENOSPC, cut=5)
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.REPLACE, Reason.IO_ERROR, True, path="/w/L/inc/a.bean")
    assert set(journals(w.fs)) == {State.RESOLVED}
    assert all(w.fs.file(n) is None for n in w.targets[0].scratch)
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
    _hook(
        w.fs, "rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean", lambda: w.fs.outside_write("/w/L/prices.bean", b"x")
    )
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/prices.bean")  # a.bean was replaced first
    assert w.fs.file("/w/L/prices.bean") == b"x"
    assert w.fs.file("/w/L/inc/a.bean") == w.targets[0].pre, "the other files rolled back (§6.4)"
    assert set(journals(w.fs)) == {State.OPEN}, "the journal stops at the created path (owner, 2026-10-30)"
    check_told(w.fs, w, out)
    out2 = recover(w.fs)
    assert out2 == TAKEN
    assert w.fs.file("/w/L/prices.bean") == b"x"


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
    _hook(w.fs, "create /w/L/inc:.a.bean.ft-s1-j7.new", _swap_folder_for_link(w))
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
    assert _index(w.fs.effects, "sync /w/L:", mk) < _index(w.fs.effects, "create /w/L/y2026:.prices.bean.ft-s1-j7.new")


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
    _hook(
        w.fs, "rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean", lambda: w.fs.outside_write("/w/L/prices.bean", b"x")
    )
    out = durable_commit(w.fs, c)
    # owner, 2026-10-30 ("Never remove it (Recommended)"): the rollback leaves the file and stops; before the first
    # replace, so not saved with the journal open (writes paused until recovery resolves it)
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, False, True, "/w/L/prices.bean")
    assert set(journals(w.fs)) == {State.OPEN}
    assert w.fs.file("/w/L/prices.bean") == b"x"
    assert w.fs.file("/w/L/.prices.bean.ft-s1-j7.tmp") is None, "our own temp removed by the temp rule"


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
    c = replace(w.commit, changes=(Change("/w/L/.main.bean.ft-s1-j7.tmp", None, b"x\n"), w.commit.changes[2]))
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
    long = "/w/L/" + "a" * 245 + ".bean"  # `.<name>.ft-s1-j7.tmp` and `0-<name>` would pass 255
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
    _hook(
        w.fs, "create /w/L:.main.bean.ft-s1-j7.new", lambda: w.fs.outside_link("/w/L/main.bean", "/w/Backup/copy.bean")
    )
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.HARD_LINKED, "/w/L/main.bean")  # prices.bean placed: left (r5)
    assert w.fs.file("/w/L/main.bean") == w.fs.file("/w/Backup/copy.bean") == w.targets[2].pre
    assert set(journals(w.fs)) == {State.OPEN}
    assert recover(w.fs) == TAKEN


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
    assert out == Unfinished(Step.READ_BACK, Reason.HARD_LINKED, "/w/L/main.bean")  # prices.bean placed: left (r5)
    # rolled back: the temp holds the pre bytes (§8.1 step 2 allows it); its other name keeps them
    assert w.fs.file("/w/Backup/copy.bean") == w.targets[2].pre
    assert w.fs.file("/w/L/main.bean") == w.targets[2].pre
    assert set(journals(w.fs)) == {State.OPEN}


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
    sha_temp = journal.temp_name(long, "s1-j7")  # `.<sha16>.ft-s1-j7.tmp`
    w.fs.outside_write(f"/w/L/{long}", b"x\n")
    twin = sha_temp[1 : -len(".ft-s1-j7.tmp")]  # a file literally named <sha16>: its temp is the same name
    for bad in (
        replace(
            w.commit, changes=(Change(f"/w/L/{long}", sha256(b"x\n"), b"y\n"), Change(f"/w/L/{twin}", None, b"z\n"))
        ),
        replace(w.commit, changes=(w.commit.changes[2], Change("/w/L/.main.bean.ft-s1-j7.tmp/x.bean", None, b"z\n"))),
    ):
        with pytest.raises(ValueError, match="temp_name_taken"):
            durable_commit(w.fs, bad)
    assert w.fs.effects == []


def test_an_entry_already_at_a_temp_name_refuses_before_the_first_effect() -> None:
    """Red team r2 L2: the journal never names a temp the save did not make, so recovery cannot delete a stranger."""
    w = world()
    w.fs.outside_write("/w/L/.main.bean.ft-s1-j7.tmp", b"someone else's\n")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/main.bean")
    assert w.fs.effects == []
    assert w.fs.file("/w/L/.main.bean.ft-s1-j7.tmp") == b"someone else's\n"


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
        ("fsync /w/L/inc:.a.bean.ft-s1-j7.new", "/w/L/inc/a.bean"),
        ("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean", "/w/L/main.bean"),
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
    assert out == NotSaved(Step.BACKUP, Reason.PERMISSION, True, path="/w/RO/new.bean", entry="/w/RO")
    assert w.fs.effects == []


def test_a_target_at_a_restore_temp_name_or_an_entry_standing_at_one_is_refused_before_the_first_effect() -> None:
    """S0061: recovery deletes restore temps without a check (§6.4), so no ledger file may sit at one, and a save
    never starts while a stranger stands at one of its restore temps."""
    w = world()
    c = replace(w.commit, changes=(Change("/w/L/.main.bean.ft-restore-s1-j7.tmp", None, b"x\n"), w.commit.changes[2]))
    with pytest.raises(ValueError, match="temp_name_taken"):
        durable_commit(w.fs, c)
    w.fs.outside_write("/w/L/.main.bean.ft-restore-s1-j7.tmp", b"someone else's\n")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/main.bean")
    assert w.fs.effects == []
    w2 = world()
    w2.fs.outside_write("/w/L/.prices.bean.ft-restore-s1-j7.tmp", b"someone else's\n")
    assert durable_commit(w2.fs, w2.commit) == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/prices.bean")


def test_the_rollback_of_a_save_that_made_a_folder_removes_it_and_leaves_nothing_of_ours() -> None:
    """§6.4, §8.1 step 3: a failure after the created file landed in its new folder: the rollback removes the file
    and the folder the save made."""
    probe = world(new_dir=True)
    durable_commit(probe.fs, probe.commit)
    placed = probe.fs.effects.index("rename /w/L/y2026:.prices.bean.ft-s1-j7.tmp,prices.bean")
    for at in range(probe.fs.effects.index(f"rename {JD}:{NAME}.part,{NAME}.open"), len(probe.fs.effects)):
        w = world(new_dir=True)
        w.fs.inject(at, crash=False, err=errno.EIO, after=True)
        out = durable_commit(w.fs, w.commit)
        if at >= placed:  # the created file is in the folder: left with it, the journal stops (owner, 2026-10-30)
            assert isinstance(out, Unfinished), (at, probe.fs.effects[at], out)
            assert w.fs.file("/w/L/y2026/prices.bean") == w.targets[1].post
            check_recovered(w.fs, w, recover(w.fs))
            assert "/w/L/y2026" not in w.fs.snapshot(), "removed once the user deleted the file and retried"
            continue
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
    at = _index(base.fs.effects, "swap /w/L/inc:.a.bean.ft-s1-j7.tmp,a.bean")
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


# ---- the kept name (owner, 2026-10-29: "Yes"; ADR-0013 amendment; S0061 round 1) ----------------------------------


FOREIGN = b"; saved by an editor\n"


def _delete_kept_and_recover(w: World, *paths: str) -> None:
    """The user deletes the kept copies: the next try can resolve, every file back at pre."""
    for p in paths:
        d, name = w.fs._parent_and_name(p)  # pyright: ignore[reportPrivateUsage]
        del d.entries[name.lower()]
    out = undo(w.fs, w)  # a created file the save had placed: the user deletes it too (owner, 2026-10-30)
    assert type(out).__name__ == "Resolved", out
    check_recovered(w.fs, w, out)


def test_a_displaced_outside_save_at_a_swap_is_moved_to_the_kept_name_and_the_save_stops() -> None:
    """§6.4 as decided: what the swap displaced is not ours, so it is never swapped back: it goes at once to
    `main.bean.kept-s1-j7` beside the file, the folder is synced, the save stops; the file keeps Finzytrack's version;
    recovery stops while the kept copy exists."""
    w = world()
    _hook(w.fs, "swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean", lambda: w.fs.outside_write("/w/L/main.bean", FOREIGN))
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/main.bean")
    t = w.targets[2]
    assert (w.fs.file(t.path), w.fs.file(t.kept), w.fs.file(t.temp)) == (t.post, FOREIGN, None)
    swap = w.fs.effects.index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    assert w.fs.effects[swap + 1 : swap + 3] == [
        "rename /w/L:.main.bean.ft-s1-j7.tmp,main.bean.kept-s1-j7",
        "sync /w/L:",
    ]
    assert w.fs.effects.count("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean") == 1, "never swapped back"
    assert set(journals(w.fs)) == {State.OPEN}
    for state in w.fs.power_loss_states():
        assert FOREIGN in {state.file(t.path), state.file(t.temp), state.file(t.kept)}, "the outside save was lost"
    assert type(recover(w.fs)).__name__ == "Stopped"
    assert w.fs.file(t.kept) == FOREIGN
    _delete_kept_and_recover(w, t.kept)


def test_a_crash_right_after_a_swap_that_displaced_an_outside_save_keeps_it_and_recovery_moves_it_aside() -> None:
    """§14: "a crash right after such a swap leaves the journal stopped with both versions": the outside save is in
    the file or in our temp in every power-loss state; recovery stops, moving a temp holding it to the kept name."""
    w = world()
    _hook(w.fs, "swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean", lambda: w.fs.outside_write("/w/L/main.bean", FOREIGN))
    w.fs.inject(_index(clean_run()[0].fs.effects, "swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    t = w.targets[2]
    for state in [w.fs, *w.fs.power_loss_states()]:
        assert FOREIGN in {state.file(t.path), state.file(t.temp)}, "the outside save was lost"
        assert set(journals(state)) == {State.OPEN}
        out = recover(state)
        assert type(out).__name__ == "Stopped", out
        assert FOREIGN in {state.file(t.path), state.file(t.kept)}
        assert state.file(t.temp) != FOREIGN, "an outside save left at a temp name, where the temp rule reaches it"


def test_an_outside_write_into_the_displaced_file_after_the_swap_goes_to_the_kept_name() -> None:
    """S0060 red team finding 2: a program that held main.bean open writes through its descriptor after the swap; at
    the temp's clean-up its bytes are moved to the kept name, never deleted, and the save stops."""
    w = world()
    fs = w.fs
    d = fs._dir(fs._walk("/w/L")[1])  # pyright: ignore[reportPrivateUsage]
    held = d.entries["main.bean"][1]

    def hook(_: int, label: str) -> None:
        if label == "sync /w/L:" and fs.file("/w/L/main.bean") == w.targets[2].post:
            fs.on_effect = None
            node = fs.nodes[held]
            node.data = node.synced = FOREIGN  # type: ignore[union-attr]

    fs.on_effect = hook
    out = durable_commit(fs, w.commit)
    assert out == Unfinished(Step.READ_BACK, Reason.CHANGED, "/w/L/main.bean")
    assert fs.file(w.targets[2].kept) == FOREIGN
    assert fs.file(w.targets[2].temp) is None
    assert set(journals(fs)) == {State.OPEN}


def test_A3_a_kept_outside_save_survives_the_file_being_put_back_to_its_old_version() -> None:
    """S0061 red team M1: after the stop, the user (or git, or a sync tool) puts main.bean back to its old version;
    recovery still stops on the kept copy, which nothing deletes, until the user deletes it."""
    w = world()
    fs = w.fs
    d = fs._dir(fs._walk("/w/L")[1])  # pyright: ignore[reportPrivateUsage]
    held = d.entries["main.bean"][1]

    def hook(_: int, label: str) -> None:
        if label == "sync /w/L:" and fs.file("/w/L/main.bean") == w.targets[2].post:
            fs.on_effect = None
            node = fs.nodes[held]
            node.data = node.synced = FOREIGN  # type: ignore[union-attr]

    fs.on_effect = hook
    durable_commit(fs, w.commit)
    t = w.targets[2]
    fs.outside_write(t.path, t.pre or b"")
    for _ in range(2):
        out = recover(fs)
        assert type(out).__name__ == "Stopped", out
        assert fs.file(t.kept) == FOREIGN
    _delete_kept_and_recover(w, t.kept)


def test_A2_an_outside_save_with_the_pre_bytes_at_the_swap_is_ours_and_a_second_one_is_never_deleted() -> None:
    """S0061 red team H1: outside save 1 replaces main.bean by rename with the same pre bytes (a no-op editor save,
    git checkout); the swap displaces it, and it is treated as ours (deleting it loses nothing). Outside save 2
    replaces main.bean after our swap: the read-back sees it, the rollback stops on it, and recovery stops on it
    too: it is never deleted."""
    w = world()
    t = w.targets[2]
    second = b"; outside 2: the user's real edit\n"

    def on(_: int, label: str) -> None:
        if label == "create /w/L:.main.bean.ft-s1-j7.new":
            w.fs.outside_write(t.path, t.pre or b"", new_inode=True)
        if label == "sync /w/L:" and w.fs.file(t.path) == t.post:
            w.fs.on_effect = None
            w.fs.outside_write(t.path, second, new_inode=True)

    w.fs.on_effect = on
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished), out
    assert w.fs.file(t.path) == second
    for _ in range(2):
        assert type(recover(w.fs)).__name__ == "Stopped"
        assert w.fs.file(t.path) == second


def test_a_file_replaced_by_another_with_the_same_bytes_before_the_swap_loses_nothing_and_lands() -> None:
    """Owner, 2026-10-29 (the kept-name design, step 2): a displaced file byte-identical to the pre bytes (the backup
    holds them) is treated as ours: an editor's save by rename with equal bytes after the pre-swap check is not an
    outside change, and the save lands."""
    w = world()
    pre = w.targets[2].pre or b""
    replaced = False

    def hook(_: int, label: str) -> None:
        nonlocal replaced
        if label == "create /w/L:.main.bean.ft-s1-j7.new" and not replaced:
            replaced = True
            w.fs.outside_write("/w/L/main.bean", pre, new_inode=True)

    w.fs.on_effect = hook
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed)
    assert w.fs.file("/w/L/main.bean") == w.targets[2].post


def test_a_second_outside_save_after_the_first_was_kept_is_kept_too() -> None:
    """The old M1 case (S0060 red team r2): outside save 1 is displaced by our swap and kept; outside save 2 replaces
    the file right then. Both survive, in the file and in the kept copy, in every power-loss state."""
    w = world()
    first, second = b"; outside save 1\n", b"; outside save 2\n"
    t = w.targets[2]

    def on(_: int, label: str) -> None:
        if label == "create /w/L:.main.bean.ft-s1-j7.new":
            w.fs.outside_write(t.path, first, new_inode=True)
        if label == "rename /w/L:.main.bean.ft-s1-j7.tmp,main.bean.kept-s1-j7":
            w.fs.outside_write(t.path, second, new_inode=True)

    w.fs.on_effect = on
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, t.path)
    assert (w.fs.file(t.path), w.fs.file(t.kept)) == (second, first)
    for state in w.fs.power_loss_states():
        assert {first, second} <= {state.file(t.path), state.file(t.temp), state.file(t.kept)}


def test_an_entry_at_a_kept_name_or_a_target_at_one_is_refused_before_the_first_effect() -> None:
    """Recovery stops while a kept copy exists: a stranger at one before the save starts would stop it for ever."""
    w = world()
    w.fs.outside_write("/w/L/main.bean.kept-s1-j7-3", b"x\n")
    assert durable_commit(w.fs, w.commit) == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/main.bean")
    assert w.fs.effects == []
    w2 = world()
    c = replace(w2.commit, changes=(Change("/w/L/main.bean.kept-s1-j7", None, b"x\n"), w2.commit.changes[2]))
    with pytest.raises(ValueError, match="temp_name_taken"):
        durable_commit(w2.fs, c)


def test_a_folder_put_where_the_swap_displaces_the_file_is_moved_to_the_kept_name() -> None:
    """Not a regular file, so never ours: an outside program replaced main.bean by a folder between the check and the
    swap."""
    w = world()
    t = w.targets[2]

    def to_folder() -> None:
        w.fs.outside_write(w.targets[0].path, FOREIGN)  # a.bean changed too: the rollback stops there first
        w.fs.outside_move(t.path, "/w/moved-away.bean")
        w.fs.outside_mkdirs(t.path)

    _hook(w.fs, "swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean", to_folder)
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, t.path)
    assert w.fs.snapshot()[t.kept] == ("dir",)
    assert w.fs.file(t.path) == t.post
    swap = w.fs.effects.index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    assert w.fs.effects[swap + 1] == "rename /w/L:.main.bean.ft-s1-j7.tmp,main.bean.kept-s1-j7", (
        "moved at once, by the save"
    )
    assert w.fs.file("/w/moved-away.bean") == t.pre


def test_the_save_keeps_an_outside_save_itself_when_its_rollback_stops_on_another_file_first() -> None:
    """The writer moves the displaced outside save to the kept name itself: its rollback may stop on an earlier file
    of the record (here a.bean, changed by another program) before it reaches this one."""
    w = world()
    a, t = w.targets[0], w.targets[2]
    second = b"; another outside save\n"

    def on(_: int, label: str) -> None:
        if label == "create /w/L:.main.bean.ft-s1-j7.new":
            w.fs.outside_write(a.path, FOREIGN)  # a.bean, already replaced, changed in place
        if label == "swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean":
            w.fs.outside_write(t.path, second)

    w.fs.on_effect = on
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished), out
    assert w.fs.file(t.kept) == second
    assert w.fs.file(t.temp) is None


def test_a_displaced_file_holding_our_post_bytes_at_the_save_swap_is_ours() -> None:
    """@regression-guard (S0061 verifier r2 F3: the code already held; it kills the mutant
    writer_displaced_pre_only): an outside save by rename with exactly our post bytes just
    before our swap: equal to bytes we hold, so the save goes on and lands."""
    w = world()
    t = w.targets[2]
    _hook(
        w.fs, "swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean", lambda: w.fs.outside_write(t.path, t.post, new_inode=True)
    )
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed)
    assert w.fs.file(t.path) == t.post


def test_a_temp_gone_at_its_clean_up_loses_nothing_and_the_save_lands() -> None:
    """@regression-guard (S0061 verifier r2 F3: the code already held; it kills the mutant
    remove_temps_gone_stops): our temp (the displaced old file) vanished before step 8.4
    (another program deleted or moved it): its bytes are the backup's, nothing is lost, so the save goes on."""
    w = world()
    t = w.targets[2]

    def gone() -> None:
        d, name = w.fs._parent_and_name(t.temp)  # pyright: ignore[reportPrivateUsage]
        del d.entries[name.lower()]

    _hook(w.fs, "remove /w/L/inc:.a.bean.ft-s1-j7.tmp", gone)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed)
    assert w.fs.file(t.temp) is None


def test_a_created_folder_at_a_files_kept_name_is_refused_before_the_first_effect() -> None:
    """S0061 red team r2 L2: recovery stops while a kept copy exists, so a folder the save makes at one would stop it
    for ever."""
    w = world()
    c = replace(w.commit, changes=(Change("/w/L/main.bean.kept-s1-j7/x.bean", None, b"x\n"), *w.commit.changes))
    with pytest.raises(ValueError, match="temp_name_taken"):
        durable_commit(w.fs, c)
    assert w.fs.effects == []


# ---- S0061 round 3 (owner, 2026-10-30: "Staging name (Recommended)") ---------------------------------------------


@pytest.mark.parametrize("staged", [".main.bean.ft-s1-j7.new", ".main.bean.ft-restore-s1-j7.new"])
def test_a_target_at_a_staging_name_or_an_entry_standing_at_one_is_refused_before_the_first_effect(staged: str) -> None:
    """Recovery deletes whatever stands at a staging name (§6.4), so no ledger file may sit at one, nor inside one,
    and a save never starts while a stranger stands at one of its staging names (it would be deleted)."""
    w = world()
    for path in (f"/w/L/{staged}", f"/w/L/{staged}/x.bean"):
        c = replace(w.commit, changes=(Change(path, None, b"x\n"), w.commit.changes[2]))
        with pytest.raises(ValueError, match="temp_name_taken"):
            durable_commit(w.fs, c)
    w.fs.outside_write(f"/w/L/{staged}", b"someone else's\n")
    out = durable_commit(w.fs, w.commit)
    assert out == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/main.bean")
    assert w.fs.effects == []
    assert w.fs.file(f"/w/L/{staged}") == b"someone else's\n"
    w2 = world()
    created = staged.replace("main.bean", "prices.bean")
    w2.fs.outside_write(f"/w/L/{created}", b"someone else's\n")
    assert durable_commit(w2.fs, w2.commit) == NotSaved(Step.BACKUP, Reason.CHANGED, True, path="/w/L/prices.bean")


def test_an_in_place_write_right_after_the_rollbacks_restore_swap_survives_every_power_loss() -> None:
    """Verifier r3 F1 for the rollback (it runs the same restore): the save fails after its swaps, its rollback dies
    right after putting main.bean back, an editor writes main.bean in place, and a power loss drops what was not
    synced: the editor's bytes keep a name in every state, and recovery never deletes them."""
    base, _ = clean_run()
    at = _index(base.fs.effects, "remove /w/L/inc:.a.bean.ft-s1-j7.tmp")  # every file at post
    probe = world()
    probe.fs.inject(at, crash=False)
    durable_commit(probe.fs, probe.commit)
    swap = _index(probe.fs.effects, "swap /w/L:.main.bean.ft-restore-s1-j7.tmp,main.bean", at + 1)
    w = world()
    w.fs.inject(at, crash=False)
    w.fs.inject(swap + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    t = w.targets[2]
    assert w.fs.file(t.path) == t.pre
    foreign = b"; an editor's save in place after the rollback's swap\n"
    w.fs.outside_write(t.path, foreign)
    for state in w.fs.power_loss_states():
        files = {e[1] for e in state.snapshot().values() if e[0] == "file"}
        assert foreign in files, "the editor's bytes lost their only name"
        recover(state)
        files = {e[1] for e in state.snapshot().values() if e[0] == "file"}
        assert foreign in files, "the editor's bytes were deleted"


def test_an_entry_that_appears_at_the_temp_name_before_the_staging_rename_stops_the_save_and_is_kept() -> None:
    """The rename from the staging name to the temp name is exclusive: an entry that appeared there after the plan's
    checks is never replaced; the save stops (`changed`), and the rollback moves it to the kept name."""
    w = world()
    t = w.targets[0]
    stranger = b"; someone else's file at our temp name\n"
    _hook(
        w.fs,
        f"rename {t.folder}:.{t.base}.ft-s1-j7.new,.{t.base}.ft-s1-j7.tmp",
        lambda: w.fs.outside_write(t.temp, stranger),
    )
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.open_journal) == (Reason.CHANGED, True)
    assert w.fs.file(t.kept) == stranger
    assert w.fs.file(t.path) == t.pre
    for state in w.fs.power_loss_states():
        assert stranger in {state.file(t.temp), state.file(t.kept)}


# ---- S0061 round 4 (red team F2; verifier F1) ---------------------------------------------------------------------


def test_R4_F2_a_power_loss_after_a_save_over_an_editors_unsynced_write_never_stops_on_bytes_nobody_wrote() -> None:
    """Red team r4 F2: an editor saved main.bean in place without a sync just before the save (many editors do not
    sync); the save read it for its backup, swapped it to the temp name and synced the folder. Unsynced, the displaced
    old object could be torn by a power loss, and recovery stopped on a kept copy "another program" never wrote. The
    save now syncs each file it replaces before it reads it for the backup (§6.1 step 8.1), so the old object is
    durable before the swap: every power-loss state recovers."""
    clean = world()
    durable_commit(clean.fs, clean.commit)
    swap = clean.fs.effects.index("swap /w/L:.main.bean.ft-s1-j7.tmp,main.bean")
    assert clean.fs.effects[swap + 1] == "sync /w/L:"
    w = world()
    root = w.targets[2]
    for t in w.targets:
        if t.pre is not None:
            w.fs.outside_unsynced(t.path)  # the data in the page cache only
    w.fs.inject(swap + 2, crash=True)  # the swap durable, then the process dies
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    seen = 0
    for state in w.fs.power_loss_states():
        if state.file(root.path) != root.post:
            continue  # only the states in which the durable swap put our post bytes in place
        seen += 1
        out = recover(state)
        check_recovered(state, w, out)
    assert seen


def test_R4_F2_each_replaced_file_is_synced_before_it_is_read_for_its_backup() -> None:
    """Red team r4 F2, the order: `sync_file` on each file the save replaces, before the backup's create."""
    w, out = clean_run()
    assert isinstance(out, Landed)
    e = w.fs.effects
    for folder, base, index in (("/w/L/inc", "a.bean", 0), ("/w/L", "main.bean", 2)):
        synced = e.index(f"sync_file {folder}:{base}")
        assert synced < e.index(f"create {SAVE_DIR}:{index}-{base}")
    assert not any(x.startswith("sync_file /w/L:prices.bean") for x in e), "a created file is never synced first"


def test_R4_vF1_an_outside_write_into_our_temp_beside_a_taken_created_path_after_the_checks_is_never_deleted() -> None:
    """S0061 verifier r4 F1, under the round-5 design (owner, 2026-10-30: "Never remove it (Recommended)"): our temp
    beside a created path another program took is removed by the temp rule at step 3, so an outside write into it
    after the checks is moved to the kept name, never deleted."""
    w = world()
    temp = "/w/L/.prices.bean.ft-s1-j7.tmp"
    foreign = b"; another program's bytes at our temp name\n"
    acts: dict[str, Callable[[], None]] = {
        "rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean": lambda: w.fs.outside_write(PRICES, b"x"),
        "sync /w/L/inc:": lambda: w.fs.outside_write(temp, foreign),  # the rollback's first sync, after its checks
    }
    rolled = [False]

    def hook(_: int, seen: str) -> None:
        if seen == "rename /w/L:.prices.bean.ft-s1-j7.tmp,prices.bean":
            rolled[0] = True
        act = acts.pop(seen, None) if rolled[0] else None
        if act is not None:
            act()

    w.fs.on_effect = hook
    out = durable_commit(w.fs, w.commit)
    w.fs.on_effect = None
    assert isinstance(out, Unfinished), out
    assert foreign in (w.fs.file(temp), w.fs.file("/w/L/prices.bean.kept-s1-j7")), "the outside bytes were deleted"
    assert w.fs.file(PRICES) == b"x"


# ---- S0061 round 5 (owner, 2026-10-30: "Never remove it (Recommended)", "Yes to both (Recommended)"; red team r5 F1,
# F2, F3; verifier r5 F3) --------------------------------------------------------------------------------------------

A_SWAP_SYNC = clean_run()[0].fs.effects.index("swap /w/L/inc:.a.bean.ft-s1-j7.tmp,a.bean") + 1


def test_R5_after_a_read_back_mismatch_the_rollback_leaves_our_own_created_file_and_stops() -> None:
    """Spec §6.4 (the created-path bullet): the user put a.bean back to its old version right after the save's swaps;
    the read-back fails; the rollback undoes main.bean, finds a.bean at pre, and leaves prices.bean (our own post
    bytes, placed by this save) as it is: it cannot tell it from another program's. Unfinished, the journal `.open`;
    recovery stops the same way until the user deletes it."""
    w = world()

    def revert(_: int, label: str) -> None:
        if label == "sync /w/L:" and w.fs.file("/w/L/main.bean") == w.targets[2].post:
            w.fs.on_effect = None
            w.fs.outside_write("/w/L/inc/a.bean", w.targets[0].pre or b"")

    w.fs.on_effect = revert
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.READ_BACK, Reason.READ_BACK, "/w/L/inc/a.bean")
    assert w.fs.file(PRICES) == w.targets[1].post
    check_told(w.fs, w, out)
    out2 = recover(w.fs)
    assert out2 == TAKEN
    check_recovered(w.fs, w, out2)


def test_R5_F1_the_rollback_never_deletes_another_programs_file_at_a_created_path_before_the_staging() -> None:
    """Red team r5 F1 (a), the High, ported: another program's prices.bean (exactly our post bytes: a sync client
    restoring a file this save re-creates) appears after the checks and before the save stages that file; the save
    fails right then (EIO on the sync after a.bean's swap). The rollback deleted it and told "not saved, unchanged".
    Now it is never touched: the other files undone, the journal stopped, the save told unfinished."""
    w = world()
    t = w.targets[1]
    w.fs.on_effect = lambda i, _: w.fs.outside_write(t.path, t.post) if i == A_SWAP_SYNC else None
    w.fs.inject(A_SWAP_SYNC, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    w.fs.on_effect = None
    assert w.fs.file(t.path) == t.post, f"the other program's prices.bean was deleted; told {out}"
    assert out == Unfinished(Step.REPLACE, Reason.IO_ERROR, "/w/L/inc/a.bean"), out
    check_told(w.fs, w, out)
    check_taken(w.fs, w, TAKEN)
    assert recover(w.fs) == TAKEN
    assert w.fs.file(t.path) == t.post


def test_R5_F1_another_programs_folder_and_file_at_a_created_folder_are_never_touched_by_the_rollback() -> None:
    """Red team r5 F1, the created-folder variant: another program makes y2026/ and y2026/prices.bean (our post
    bytes) after the checks; the save's mkdir answers EEXIST; the rollback leaves both (a folder it did not make is
    never removed) and stops."""
    probe = world(new_dir=True)
    durable_commit(probe.fs, probe.commit)
    mkdir = probe.fs.effects.index("mkdir /w/L:y2026")
    t = probe.targets[1]
    w = world(new_dir=True)

    def arrive(i: int, _: str) -> None:
        if i == mkdir:
            w.fs.outside_mkdirs("/w/L/y2026")
            w.fs.outside_write(t.path, t.post)

    w.fs.on_effect = arrive
    out = durable_commit(w.fs, w.commit)
    w.fs.on_effect = None
    assert w.fs.file(t.path) == t.post, f"deleted; told {out}"
    assert isinstance(out, Unfinished), out
    assert recover(w.fs) == Stopped(Cause.CREATED_PATH_TAKEN, t.path)
    assert w.fs.file(t.path) == t.post


def _flaky_read(monkeypatch: pytest.MonkeyPatch, name: str, nth: int) -> None:
    from tests.fakes.memfs import MemDir

    real = MemDir.read
    seen = [0]

    def read(self: MemDir, n: str) -> bytes | None:
        if n == name:
            seen[0] += 1
            if seen[0] == nth:
                raise OSError(errno.EIO, "transient")
        return real(self, n)

    monkeypatch.setattr(MemDir, "read", read)


@pytest.mark.parametrize("nth", [1, 2])  # 1: the check of what the swap displaced; 2: the temp's check at its removal
def test_R5_F3_a_read_error_at_the_saves_displaced_check_moves_nothing_and_rolls_back_as_an_io_error(
    monkeypatch: pytest.MonkeyPatch, nth: int
) -> None:
    """Red team r5 F3 (`_check_displaced`, `_remove_temps`), ported: one transient EIO while hashing what main.bean's
    swap displaced (the pre bytes) moved our own bytes to a kept name, and every later try stopped on it. Now the save
    fails like any I/O error and rolls back, and no kept copy is ever made (owner, 2026-10-30: "Yes to both")."""
    w = world()
    t = w.targets[2]
    _flaky_read(monkeypatch, t.temp.rpartition("/")[2], nth)
    out = durable_commit(w.fs, w.commit)
    monkeypatch.undo()
    assert isinstance(out, Unfinished), out
    assert out.reason is Reason.IO_ERROR, out
    assert w.fs.file(t.kept) is None, "our own bytes moved to a kept name"
    check_told(w.fs, w, out)
    out2 = recover(w.fs)
    assert out2 == TAKEN, out2
    check_recovered(w.fs, w, out2)


def _deny(perms: str, flags: str = "", *, ours: bool = True) -> AclEntry:
    """An ACL entry as `chmod +a "<who> deny <perms>,<flags>"` writes it."""
    return AclEntry(True, ours, frozenset(perms.split(",")), frozenset(f for f in flags.split(",") if f))


def _refused_before_anything_is_written(w: World, folder: str, detail: str | None) -> None:
    before = w.fs.snapshot()
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.unchanged, out.open_journal) == (Reason.PERMISSION, True, False), out
    assert (out.entry, out.detail) == (folder, detail), out
    assert w.fs.effects == [], "refused before the first effect"
    assert w.fs.snapshot() == before


@pytest.mark.parametrize("where", ["/w/L", "/w/L/inc"])
def test_R5_F2_a_ledger_folder_whose_acl_denies_us_what_the_save_makes_there_is_refused_before_anything_is_written(
    where: str,
) -> None:
    """Red team r5 F2 (the fake's half; on APFS in `tests/fs/test_darwin.py`): an inheritable `deny delete` (or
    `deny read`) on a folder the save writes in would make its own staging file unremovable, or its new file
    unreadable. The save refuses with `permission` before anything is written, naming the folder and the entry."""
    w = world()
    w.fs.outside_acl(where, _deny("delete", "file_inherit"))
    _refused_before_anything_is_written(w, where, "deny delete,file_inherit")


# ---- S0061 round 6: the ACL allowlist (owner, 2026-10-30: "Allowlist (Recommended)"), red team r6 F1-F3 and
# overflow 2-4, verifier r6 F1, in the fake (each shape also on APFS in `tests/fs/test_darwin.py`) -----------------

R6_REFUSED = {
    # case: (new_dir, the folder the entry is on, the entry); a direct `deny delete_child` is met first by the
    # system's own check (`_RMFILE_OK`), so its refusal names the folder without an entry
    "rt_F1_delete_child_direct": (False, "/w/L", _deny("delete_child")),
    "rt_F1_delete_child_into_made_folder": (True, "/w/L", _deny("delete_child", "directory_inherit,only_inherit")),
    "rt_F2_search_into_made_folder": (True, "/w/L", _deny("search", "directory_inherit,only_inherit")),
    "rt_F3_delete_child_beside_made_folder": (True, "/w/L", _deny("delete_child")),
    "v_F1_readattr_inherited": (False, "/w/L", _deny("readattr", "file_inherit,only_inherit")),
    "v_F1_readsecurity_inherited": (False, "/w/L", _deny("readsecurity", "file_inherit,only_inherit")),
    "rt_O2_readextattr_inherited": (False, "/w/L/inc", _deny("readextattr", "file_inherit")),
    "rt_O2_writeextattr_inherited": (False, "/w/L/inc", _deny("writeextattr", "file_inherit")),
    "add_file_into_made_folder": (True, "/w/L", _deny("add_file", "directory_inherit,only_inherit")),
}


@pytest.mark.parametrize("case", sorted(R6_REFUSED))
def test_R6_a_folder_whose_acl_denies_us_anything_the_save_or_its_undo_needs_is_refused_before_anything_is_written(
    case: str,
) -> None:
    """Red team r6 F1 (High), F2, F3, overflow 2 and verifier r6 F1: each entry got past round 5's check (it looked
    only for an inherited `deny read` or `deny delete`); the save then wrote, failed, and every undo stopped on our
    own leftovers, which kept the inherited entry after the user removed the folder's. Every such entry is now
    refused before the first effect, naming the folder and the entry (§11)."""
    new_dir, where, entry = R6_REFUSED[case]
    w = world(new_dir=new_dir)
    w.fs.outside_acl(where, entry)
    flags = sorted(entry.flags)
    detail = None if not flags and "delete_child" in entry.perms else "deny " + ",".join([*sorted(entry.perms), *flags])
    _refused_before_anything_is_written(w, where, detail)


@pytest.mark.parametrize(
    "entry",
    [
        _deny("delete"),
        _deny("writeextattr"),
        _deny("delete,writeextattr"),  # macOS's own pair, as `chmod` merges it
        _deny("writeattr,writesecurity,chown"),
        _deny("delete_child,search,list", "file_inherit,directory_inherit", ours=False),  # another user's
    ],
)
def test_R6_a_folder_with_only_entries_the_allowlist_passes_is_saved(entry: AclEntry) -> None:
    """Owner, 2026-10-30: "Allowlist (Recommended)": macOS's own entries (`group:everyone deny delete`, `deny
    writeextattr`, not inherited) and another user's deny entries do not stop a save."""
    for where in ("/w/L", "/w/L/inc"):
        w = world()
        w.fs.outside_acl(where, entry)
        out = durable_commit(w.fs, w.commit)
        assert isinstance(out, Landed), (where, out)


def test_R6_the_fake_reproduces_red_team_F1_when_the_save_does_not_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Red team r6 overflow 4 (fake fidelity): with the allowlist bypassed, the fake shows the F1 shape APFS showed:
    `deny delete_child,directory_inherit,only_inherit` on the folder the save makes its folder in, inc/a.bean already
    swapped, the staging file's rename to its temp name refused in the new folder (which inherited the entry), the
    rollback stopped on our own staging file, the save unfinished and the journal left `.open`."""
    import ledger_engine.ledger.writer as writer_module

    def no_denial(_h: object) -> None:
        return None

    monkeypatch.setattr(writer_module, "acl_denial", no_denial)
    w = world(new_dir=True)
    w.fs.outside_acl("/w/L", _deny("delete_child", "directory_inherit,only_inherit"))
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished), out
    assert set(journals(w.fs)) == {State.OPEN}
    assert w.fs.file(w.targets[0].path) == w.targets[0].post
    assert w.fs.file(w.targets[1].staging) == w.targets[1].post, "our own staging file, never removable there"


def test_R5b_a_stat_error_at_the_saves_displaced_check_moves_nothing_and_rolls_back_as_an_io_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """@regression-guard (it already held at d6c04734: `displaced` raises a `stat` error, which the save treats as any
    I/O error): what main.bean's swap displaced cannot be `stat`ed: the save rolls back, no kept copy is made."""
    from tests.fakes.memfs import MemDir

    w = world()
    t = w.targets[2]
    name = t.temp.rpartition("/")[2]
    real = MemDir.stat
    fired = [False]

    def failing(self: MemDir, n: str) -> object:
        if n == name and w.fs.file(t.path) == t.post and not fired[0]:
            fired[0] = True
            raise OSError(errno.EIO, "injected")
        return real(self, n)

    monkeypatch.setattr(MemDir, "stat", failing)
    out = durable_commit(w.fs, w.commit)
    monkeypatch.undo()
    assert fired[0]
    assert isinstance(out, Unfinished), out
    assert out.reason is Reason.IO_ERROR
    assert w.fs.file(t.kept) is None
    check_told(w.fs, w, out)
    check_recovered(w.fs, w, recover(w.fs))


def test_R6_a_folder_whose_acl_we_may_not_read_is_refused_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant `unreadable_acl_passes`: an ACL the system will not let us read (probed on APFS: `acl_get_fd_np` answers
    `EACCES` under `deny readattr,file_inherit` acting on the folder itself) cannot be judged by the allowlist, so it
    refuses like a blocking entry (fails closed), naming the folder."""
    from ledger_engine.ledger.handles import UNREADABLE_ACL
    from tests.fakes.memfs import MemDir

    real = MemDir.acl

    def unreadable(self: MemDir) -> tuple[AclEntry, ...]:
        if self.path() == "/w/L/inc":
            raise PermissionError(errno.EACCES, "Permission denied")
        return real(self)

    monkeypatch.setattr(MemDir, "acl", unreadable)
    _refused_before_anything_is_written(world(), "/w/L/inc", UNREADABLE_ACL)
