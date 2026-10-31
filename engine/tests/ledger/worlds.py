"""The save's test world and the one oracle every state of its disk must satisfy (`design/specs/engine-jobs.md`
§6.1 step 8, §6.4, §8.1, §10, §13; S0060, S0061). Shared by the writer's and the restore's tests.

`check_disk` states what every state of the disk must satisfy, crash or not, before or after a power loss, during a
save, a rollback or a recovery, written from §10's rows and §13's write-protocol and recovery properties:
- every target is whole at its pre or its post bytes (a created file: absent or post): `NoTornLedger`,
  `NoTornLedgerAfterRecovery`; on a `plain` volume (S0065), and only under an `.open` journal, a replaced file may
  also be missing or empty (only in a state a power loss left, `MemFS.lost_power`: a split replace, then recovery's
  empty claim of the name) and a created file may hold a part of its post bytes (a prefix; after a power loss also
  a torn shape of one, `memfs.tears`);
- a target at its post bytes only under a readable `.open` or `.closed` journal whose backups hold the pre bytes:
  `BackupDurableBeforeReplace`, `JournalDurableBeforeFirstReplace`; a `.part` may be torn, an `.open` never;
- a `.closed` journal only with every target at post, a `.resolved` one only with every target at pre, and with
  either no temp, staging file, restore temp or restore staging file of ours left (`temps_after_close`,
  `resolve_before_restores_synced`, `CutTempNeverBlocks`'s "no temp of a save outlives its journal"); beside
  `.resolved`, a folder the save made only when it is empty (an `rmdir` that answered `ENOENT` or `ENOTEMPTY`, §15);
- nothing changed outside the recorded paths, the save's temps and staging names, restore temps and their staging
  names, kept names, its backups and its journal (INV-32, `RecoveryWritesOnlyRecordedPaths`), and nothing that was
  there before removed; `.resolved` never beside a kept copy.
`check_recovered` adds what recovery must end with when no outside program acts: no `.open` journal, every target at
pre or (only under `.closed`) at post, in the disk as it is and in every power-loss state of it; after a try that
answered `Resolved`, nothing of ours left at all (`leftovers`). A try that stops because something stands at a created
path (`created_path_taken`, owner, 2026-10-30: "Never remove it (Recommended)") must have left that entry as it was,
every replaced file durably at pre and no name of ours anywhere (`check_taken`); then the user deletes the file and
presses Retry, which must resolve.
"""

from __future__ import annotations

from dataclasses import dataclass

from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.recovery import EMPTY_DETAIL, Cause
from ledger_engine.ledger.restore import Failed, Resolved, RestoreOutcome, Stopped, recover_journal
from ledger_engine.ledger.writer import Change, Commit, sha256
from tests.fakes.memfs import MemFS, MemLocks, tears

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
    def base(self) -> str:
        return self.path.rpartition("/")[2]

    @property
    def temp(self) -> str:
        return f"{self.folder}/.{self.base}.ft-s1-j7.tmp"

    @property
    def staging(self) -> str:
        """Where the save writes the new bytes before they get the temp name (owner, 2026-10-30: "Staging name")."""
        return f"{self.folder}/.{self.base}.ft-s1-j7.new"

    @property
    def rtemp(self) -> str:
        return f"{self.folder}/.{self.base}.ft-restore-s1-j7.tmp"

    @property
    def rstaging(self) -> str:
        return f"{self.folder}/.{self.base}.ft-restore-s1-j7.new"

    @property
    def scratch(self) -> tuple[str, ...]:
        """Every name of ours beside the file that holds bytes only while a save or a try runs."""
        return (self.temp, self.staging, self.rtemp, self.rstaging)

    @property
    def kept(self) -> str:
        """The first kept name of this file (§6.4 as decided on 2026-10-29): where an outside save we met goes."""
        return f"{self.folder}/{self.base}.kept-s1-j7"


@dataclass(frozen=True)
class World:
    fs: MemFS
    commit: Commit
    targets: tuple[Target, ...]
    before: dict[str, tuple[object, ...]]
    new_dirs: tuple[str, ...] = ()
    volume: str = "swap"  # the ledger folder's volume (S0065): "swap" (APFS), "no_swap" (HFS+), "plain" (exFAT)

    @property
    def plain(self) -> bool:
        """A volume with neither RENAME_SWAP nor RENAME_EXCL: a replace a power loss may split (a file missing,
        never torn), and a created file made in place by an exclusive create (part of it there after a crash)."""
        return self.volume == "plain"


VOLUMES = ("swap", "no_swap", "plain")


def world(*, new_dir: bool = False, mode: int = 0o644, stores_nfd: bool = False, volume: str = "swap") -> World:
    """A ledger set in `/w/L`: `inc/a.bean` (a child, replaced), `prices.bean` (created; with `new_dir`, in a new
    folder `y2026/`), `main.bean` (the root, replaced last) and `other.bean` (in the set, not written). `volume`: the
    drive `/w/L` is on (S0065); the journal and backups stay on the Mac's own disk (INV-27, INV-30)."""
    assert volume in VOLUMES, volume
    fs = MemFS(stores_nfd=stores_nfd)
    for d in (JD, BD):
        fs.outside_mkdirs(d)
    if volume == "swap":
        fs.outside_mkdirs("/w/L/inc")
    else:
        fs.outside_mkdirs("/w/L", no_swap=True, no_excl=volume == "plain")
        fs.outside_mkdirs("/w/L/inc")
    fs.outside_write("/w/L/main.bean", b'include "inc/a.bean"\n', mode=mode)
    fs.outside_write("/w/L/inc/a.bean", b"2026-01-01 open Assets:A\n")
    fs.outside_write("/w/L/other.bean", b"; not written\n")
    created = "/w/L/y2026/prices.bean" if new_dir else "/w/L/prices.bean"
    targets = (
        Target("/w/L/inc/a.bean", b"2026-01-01 open Assets:A\n", b"2026-01-01 open Assets:A\n2026-01-02 *\n"),
        Target(created, None, b"2026-01-03 price X 1 EUR\n"),
        Target("/w/L/main.bean", b'include "inc/a.bean"\n', b'include "inc/a.bean"\ninclude "prices.bean"\n'),
    )
    return World(fs, commit_for(targets), targets, fs.snapshot(), ("/w/L/y2026",) if new_dir else (), volume)


def commit_for(targets: tuple[Target, ...]) -> Commit:
    return Commit(
        session="s1",
        job="j7",
        workspace_key="k1",
        lineage="lin1",
        engine_build="build1",
        post_fingerprint="sha256:" + "f" * 64,
        adopted_digest="sha256:" + "a" * 64,
        changes=tuple(Change(t.path, None if t.pre is None else sha256(t.pre), t.post) for t in targets),
        journal_dir=JD,
        backups_dir=BD,
    )


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


def leftovers(fs: MemFS, w: World) -> list[str]:
    """Every name of ours beside the files and every folder the save made, as found now."""
    snap = fs.snapshot()
    out = [name for t in w.targets for name in t.scratch if name in snap]
    return out + [d for d in w.new_dirs if d in snap]


def check_disk(fs: MemFS, w: World) -> None:
    """The oracle (the module's docstring)."""
    snap = fs.snapshot()
    js = journals(fs)
    assert len(js) <= 1, f"one record per save, found {sorted(js)}"
    state, data = next(iter(js.items())) if js else (None, b"")
    assert state in (None, State.PART, State.OPEN, State.CLOSED, State.RESOLVED), state
    record = journal.decode(data) if state not in (None, State.PART) else None
    if state not in (None, State.PART):
        assert isinstance(record, Record), f"a {state} journal is never torn: {record}"
    at_post = False
    for t in w.targets:
        now = fs.file(t.path)
        if w.plain and t.pre is not None and now in (None, b"") and now != t.post:
            # missing in the middle of a replace a power loss split (exFAT, FAT: no journal), never torn, or the empty
            # file recovery claims the name with before it puts the file back (S0065): only after a power loss, only
            # under an `.open` record whose backup holds the pre bytes, from which recovery restores it
            assert fs.lost_power, f"{t.path} {'missing' if now is None else 'empty'} without a power loss"
            assert state is State.OPEN, f"{t.path} missing under a {state} journal"
            at_post = True  # checked below as a replaced file: its backup must hold the pre bytes
            continue
        if w.plain and t.pre is None and now not in (None, t.post):
            # part of a created file made in place by an exclusive create, cut by a crash (S0065: no exclusive rename
            # on this volume): only under `.open`; recovery never touches it and asks the user (created_path_taken)
            assert state is State.OPEN, f"part of {t.path} under a {state} journal"
            assert snap[t.path][0] == "file", snap[t.path]
            assert part_of(now, t.post, lost_power=fs.lost_power), f"{t.path} holds bytes not ours: {now!r}"
            continue
        assert now in (t.pre, t.post), f"{t.path} is torn or foreign: {now!r}"
        if now == t.post and now != t.pre:
            at_post = True
    if at_post:
        assert state in (State.OPEN, State.CLOSED), f"a target at post under a {state} journal"
        assert isinstance(record, Record)
        for f in record.files:
            if f.backup is not None:
                backup = fs.file(f"{record.backup_dir}/{f.backup}")
                assert backup is not None, "a replaced file whose backup is gone"
                assert sha256(backup) == f.pre, "a replaced file whose backup does not hold its pre bytes"
    if state in (State.CLOSED, State.RESOLVED):
        for t in w.targets:
            want = t.post if state is State.CLOSED else t.pre
            assert fs.file(t.path) == want, f"{state}, but {t.path} is not at {'post' if want == t.post else 'pre'}"
            if state is State.CLOSED:
                for name in t.scratch:
                    assert name not in snap, f"{state} with a temp of ours left: {name}"
            if state is State.RESOLVED:
                assert t.kept not in snap, f"resolved while a kept copy exists: {t.kept}"
    if state is State.RESOLVED:
        for name in leftovers(fs, w):
            assert name in w.new_dirs, f"a name of ours left beside a resolved journal: {name}"
            assert not any(p.startswith(name + "/") for p in snap), f"resolved, but the save's folder holds: {name}"
    ours = {t.path for t in w.targets} | {n for t in w.targets for n in t.scratch}
    ours |= {t.kept for t in w.targets}
    ours |= set(w.new_dirs) | {SAVE_DIR, JD, BD}
    for path, entry in snap.items():
        if path in ours or path.startswith((SAVE_DIR + "/", JD + "/")):
            continue
        assert w.before.get(path) == entry, f"written outside the recorded paths: {path}"
    for path in w.before:
        if w.plain and path in {t.path for t in w.targets}:
            continue  # a missing target was checked above (only under `.open`, its backup holding the pre bytes)
        assert path in snap, f"removed: {path}"


def check_every_power_loss(fs: MemFS, w: World) -> int:
    n = 0
    for state in fs.power_loss_states():
        check_disk(state, w)
        n += 1
    return n


def recover(fs: MemFS, locks: MemLocks | None = None, *, listed: bool = True) -> RestoreOutcome | None:
    """§8.1 for this world's journal: the listing's `durable_sync` of the journal folder first (recover-all's, slice 3:
    it lists only what a power loss keeps; skipped with `listed=False`), then steps 1-4 if the journal is `.open` (a
    `.part` is the listing's to delete: slice 3)."""
    if listed:
        with fs.open_dir(JD) as d:
            d.sync()
    if State.OPEN not in journals(fs):
        return None
    return recover_journal(fs, locks or MemLocks(), JD, NAME)


def created(w: World) -> tuple[Target, ...]:
    return tuple(t for t in w.targets if t.pre is None)


def part_of(now: bytes | None, post: bytes, *, lost_power: bool) -> bool:
    """`now` is what a cut exclusive create of `post` leaves (S0065, `plain`): a prefix of it (empty included); after
    a power loss also a torn shape of a prefix (`memfs.tears`: its data was not yet synced)."""
    if now is None:
        return False
    if post.startswith(now):
        return True
    return lost_power and any(now in tears(post[:n]) for n in range(len(post) + 1))


def assert_our_created_file(fs: MemFS, w: World, path: str) -> None:
    """The file at a created path is the save's own (no outside program acted): its post bytes, or on a `plain` volume
    a part of them a crash in its exclusive create left (S0065; after a power loss perhaps torn: `part_of`)."""
    now = fs.file(path)
    post = next(t.post for t in created(w) if t.path == path)
    if w.plain:
        assert part_of(now, post, lost_power=fs.lost_power), f"not our own file at {path}: {now!r}"
        return
    assert now == post, "not our own file"


def check_taken(fs: MemFS, w: World, out: RestoreOutcome | None) -> None:
    """After `Stopped(created_path_taken)` (the module's docstring), in the disk now and in every power-loss state:
    the journal `.open`, something at that created path, every replaced file at pre, no name of ours left."""
    assert isinstance(out, Stopped), out
    assert out.cause is Cause.CREATED_PATH_TAKEN, out
    assert out.path in {t.path for t in created(w)}, out
    for state in (fs, *fs.power_loss_states()):
        check_disk(state, w)
        assert set(journals(state)) == {State.OPEN}, "the journal stops: it stays .open"
        assert out.path in state.snapshot(), "the entry at the created path was removed"
        for t in w.targets:
            if t.pre is not None:
                assert state.file(t.path) == t.pre, f"{t.path} not at pre beside the taken path"
            for name in t.scratch:
                assert name not in state.snapshot(), f"a name of ours left: {name}"


def undo(fs: MemFS, w: World, locks: MemLocks | None = None) -> RestoreOutcome | None:
    """`recover`; when it stops at a created path holding our own file (owner, 2026-10-30: "Never remove it
    (Recommended)"), `check_taken`, then the user deletes that file and presses Retry: the outcome of that Retry."""
    out = recover(fs, locks)
    if isinstance(out, Stopped) and out.cause is Cause.CREATED_PATH_TAKEN:
        check_taken(fs, w, out)
        assert out.path is not None
        assert_our_created_file(fs, w, out.path)
        fs.outside_remove(out.path)
        out = recover(fs, locks)
    return out


def check_recovered(fs: MemFS, w: World, out: RestoreOutcome | None) -> None:
    """What recovery ends with when no outside program acts (§8.1, §10): resolved, files at pre; or no `.open`
    journal to resolve, files at pre (or, under `.closed`, at post); in the disk now and in every power-loss state.
    A stop at a created path that holds our own file (the save placed it before it was cut) is the owner's choice:
    checked by `check_taken`, then the user deletes it and presses Retry. So is a stop at an empty file at a replaced
    path on a drive without the swap, which only an undo cut right after its claim leaves here (owner, 2026-10-31:
    "Empty = never ours (Recommended)"): the backup holds the pre bytes, the user deletes the empty file and presses
    Retry."""
    if isinstance(out, Stopped) and out.detail == EMPTY_DETAIL:
        assert out.cause is Cause.FOREIGN, out
        assert w.plain, "the claim exists only where no rename is exclusive"
        assert out.path in {t.path for t in w.targets if t.pre is not None}, out
        assert fs.file(out.path) == b"", out
        for state in (fs, *fs.power_loss_states()):
            check_disk(state, w)  # the empty file only after a power loss, under `.open`, its backup good
            assert set(journals(state)) == {State.OPEN}, "the journal stops: it stays .open"
        fs.outside_remove(out.path)
        out = recover(fs)
    if isinstance(out, Stopped) and out.cause is Cause.CREATED_PATH_TAKEN:
        check_taken(fs, w, out)
        assert out.path is not None
        assert_our_created_file(fs, w, out.path)
        fs.outside_remove(out.path)
        out = recover(fs)
        assert isinstance(out, Resolved), f"the user deleted the file and pressed Retry: {out}"
    assert not isinstance(out, Stopped | Failed), f"recovery with no outside program could not finish: {out}"
    for state in (fs, *fs.power_loss_states()):
        check_disk(state, w)
        js = journals(state)
        assert State.OPEN not in js, "recovery left an .open journal"
        if isinstance(out, Resolved):
            assert set(js) == {State.RESOLVED}, f"told resolved, the disk has {sorted(js)}"
            assert leftovers(state, w) == [], "told resolved, but something of ours is left"
        if State.CLOSED not in js:
            for t in w.targets:
                assert state.file(t.path) == t.pre, f"{t.path} not at pre after recovery"
