"""The save's durable order (`design/specs/engine-jobs.md` §6.1 step 8, §6.4, §10, §13, §14 "Save, durable order";
ADR-0008 steps 5-6, ADR-0012, ADR-0013; S0060), over the in-memory file system with its durable layer.

One oracle (`check_disk`) states what every state of the disk must satisfy, crash or not, before or after a power
loss, written from §10's rows and §13's write-protocol properties:
- every target is whole at its pre or its post bytes (a created file: absent or post): `NoTornLedger`;
- a target at its post bytes only under a readable `.open` or `.closed` journal whose backups hold the pre bytes:
  `BackupDurableBeforeReplace`, `JournalDurableBeforeFirstReplace`; a `.part` may be torn, an `.open` never;
- a `.closed` journal only with every target at post and no temp of ours left (`temps_after_close`);
- nothing changed outside the recorded paths, the save's temps, its backups and its journal (INV-32).
Then each injected failure checks the outcome told against the disk: `NotSaved` only with every target at pre and no
`.open` journal, `Unfinished` only with a readable `.open` journal, `Landed` only with a `.closed` journal that every
power loss keeps (`SavedSurvivesPowerLoss`).

Rollback is the next slice (BL-0151): a failure after the journal's rename leaves it `.open`, the spec's state of a
failed rollback (§6.4), which this oracle accepts and recovery resolves.
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest

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

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

JD = "/w/local/journal"
BD = "/w/local/backups"
NAME = "s1-j7"
SAVE_DIR = f"{BD}/{NAME}"


@dataclass(frozen=True)
class Target:
    path: str  # canonical
    pre: bytes | None
    post: bytes

    @property
    def folder(self) -> str:
        return self.path.rpartition("/")[0]

    @property
    def temp(self) -> str:
        base = self.path.rpartition("/")[2]
        return f"{self.folder}/.{base}.ft-j7.tmp"


@dataclass(frozen=True)
class World:
    fs: MemFS
    commit: Commit
    targets: tuple[Target, ...]
    before: dict[str, tuple[object, ...]]
    new_dirs: tuple[str, ...] = ()


def world(*, new_dir: bool = False, mode: int = 0o644) -> World:
    """A ledger set in `/w/L`: `inc/a.bean` (a child, replaced), `prices.bean` (created; with `new_dir`, in a new
    folder `y2026/`), `main.bean` (the root, replaced last) and `other.bean` (in the set, not written)."""
    fs = MemFS()
    for d in (JD, BD, "/w/L/inc"):
        fs.outside_mkdirs(d)
    fs.outside_write("/w/L/main.bean", b'include "inc/a.bean"\n', mode=mode)
    fs.outside_write("/w/L/inc/a.bean", b"2026-01-01 open Assets:A\n")
    fs.outside_write("/w/L/other.bean", b"; not written\n")
    created = "/w/L/y2026/prices.bean" if new_dir else "/w/L/prices.bean"
    targets = (
        Target("/w/L/inc/a.bean", b"2026-01-01 open Assets:A\n", b"2026-01-01 open Assets:A\n2026-01-02 *\n"),
        Target(created, None, b"2026-01-03 price X 1 EUR\n"),
        Target("/w/L/main.bean", b'include "inc/a.bean"\n', b'include "inc/a.bean"\ninclude "prices.bean"\n'),
    )
    commit = Commit(
        session="s1",
        job="j7",
        workspace_key="k1",
        lineage="lin1",
        engine_build="build1",
        post_fingerprint="sha256:" + "f" * 64,
        adopted_digest="sha256:" + "a" * 64,
        parse_digest="sha256:" + "b" * 64,
        changes=tuple(Change(t.path, None if t.pre is None else sha256(t.pre), t.post) for t in targets),
        journal_dir=JD,
        backups_dir=BD,
    )
    return World(fs, commit, targets, fs.snapshot(), ("/w/L/y2026",) if new_dir else ())


def journals(fs: MemFS) -> dict[State, bytes]:
    out: dict[State, bytes] = {}
    for path, entry in fs.snapshot().items():
        if path.startswith(JD + "/"):
            parsed = journal.parse_file_name(path.rpartition("/")[2])
            assert parsed is not None, f"a foreign name in the journal folder: {path}"
            assert parsed[0] == NAME, f"another save's record: {path}"
            assert entry[0] == "file"
            out[parsed[1]] = entry[1]  # type: ignore[assignment]
    return out


def check_disk(fs: MemFS, w: World) -> None:
    """The oracle (the module's docstring)."""
    snap = fs.snapshot()
    js = journals(fs)
    assert len(js) <= 1, f"one record per save, found {sorted(js)}"
    state, data = next(iter(js.items())) if js else (None, b"")
    record = journal.decode(data) if state in (State.OPEN, State.CLOSED) else None
    if state in (State.OPEN, State.CLOSED):
        assert isinstance(record, Record), f"a {state} journal is never torn: {record}"
    if state is not None:
        assert state in (State.PART, State.OPEN, State.CLOSED)
    at_post = False
    for t in w.targets:
        now = fs.file(t.path)
        assert now in (t.pre, t.post), f"{t.path} is torn or foreign: {now!r}"
        if now == t.post and now != t.pre:
            at_post = True
    if at_post:
        assert isinstance(record, Record), "a target at post with no readable .open or .closed journal"
        for f in record.files:
            if f.backup is not None:
                backup = fs.file(f"{record.backup_dir}/{f.backup}")
                assert backup is not None, "a replaced file whose backup is gone"
                assert sha256(backup) == f.pre, "a replaced file whose backup does not hold its pre bytes"
    if state is State.CLOSED:
        for t in w.targets:
            assert fs.file(t.path) == t.post, "closed, but a target is not at post"
            assert t.temp not in snap, "closed with a temp of ours left"
    ours = {t.path for t in w.targets} | {t.temp for t in w.targets} | set(w.new_dirs) | {SAVE_DIR, JD, BD}
    for path, entry in snap.items():
        if path in ours or path.startswith((SAVE_DIR + "/", JD + "/")):
            continue
        assert w.before.get(path) == entry, f"written outside the recorded paths: {path}"
    for path in w.before:
        assert path in snap, f"removed: {path}"


def check_every_power_loss(fs: MemFS, w: World) -> int:
    n = 0
    for state in fs.power_loss_states():
        check_disk(state, w)
        n += 1
    return n


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


@pytest.mark.parametrize("at", range(len(clean_run()[0].fs.effects)))
def test_an_io_error_at_every_effect_is_told_truthfully_and_every_power_loss_after_it_is_recoverable(at: int) -> None:
    w = world()
    w.fs.inject(at, crash=False, err=errno.ENOSPC)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    js = journals(w.fs)
    match out:
        case NotSaved():
            assert out.reason is Reason.IO_ERROR
            assert out.unchanged, "an I/O error before the first replace leaves every target at pre"
            for t in w.targets:
                assert w.fs.file(t.path) == t.pre
            assert State.CLOSED not in js
            if State.OPEN in js:
                assert out.open_journal, "an .open journal left behind is always told (writes stay paused)"
        case Unfinished():
            ((state, data),) = js.items()
            # `.closed` only when the close's folder sync failed: not durable, so not "saved" (§6.1 step 8.5)
            assert state in (State.OPEN, State.CLOSED), "unfinished means a readable journal on disk"
            assert isinstance(journal.decode(data), Record)
            if state is State.CLOSED:
                assert w.fs.effects[at] == f"sync {JD}:"
                assert at == len(w.fs.effects) - 1
        case Landed():
            pytest.fail(f"an I/O error at effect {at} ({w.fs.effects[at]}) cannot land")


def test_a_failure_before_the_first_replace_is_not_saved_and_one_after_it_is_unfinished() -> None:
    """The step rule: the first swap or exclusive rename of a ledger file divides `NotSaved` (true whatever the
    journal's state: nothing was replaced) from `Unfinished`; `open_journal` from the journal's rename to `.open` on."""
    base, _ = clean_run()
    e = base.fs.effects
    rename = _index(e, f"rename {JD}:{NAME}.part,{NAME}.open")
    first_replace = min(i for i, x in enumerate(e) if x.startswith(("swap /w/L", "rename /w/L")))
    for at in range(len(e)):
        w = world()
        w.fs.inject(at, crash=False)
        out = durable_commit(w.fs, w.commit)
        if at < first_replace:
            assert isinstance(out, NotSaved), (at, e[at], out)
            assert out.open_journal == (at >= rename), (at, e[at], out)  # a failed rename may have happened
        else:
            assert isinstance(out, Unfinished), (at, e[at], out)


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
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/prices.bean")
    assert w.fs.file("/w/L/prices.bean") == b"x"
    assert set(journals(w.fs)) == {State.OPEN}


def test_a_displaced_outside_save_at_a_swap_is_swapped_back_synced_and_kept() -> None:
    """§6.4: the swap hashes what it displaced; other bytes are put back at once and the folder synced (F3), so every
    power loss keeps the outside save in place."""
    w = world()
    foreign = b"; saved by an editor\n"
    _hook(w.fs, "swap /w/L:.main.bean.ft-j7.tmp,main.bean", lambda: w.fs.outside_write("/w/L/main.bean", foreign))
    out = durable_commit(w.fs, w.commit)
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/main.bean")
    assert w.fs.file("/w/L/main.bean") == foreign
    assert w.fs.effects[-1] == "sync /w/L:"  # the swap-back synced before anything else
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
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/y2026")
    assert w.fs.snapshot()["/w/L/y2026"] == ("dir",)
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
    assert out == NotSaved(Step.REPLACE, Reason.CHANGED, False, open_journal=True, path="/w/L/prices.bean")
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
    assert out == Unfinished(Step.REPLACE, Reason.CHANGED, "/w/L/main.bean")
    assert w.fs.file("/w/L/main.bean") == pre


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
