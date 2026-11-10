"""The mirror-publish model replayed through the code (S0091, BL-0210; `design/specs/mirror/publish.md` §4-§7;
`formal/tla/mirror-publish/MAPPING.md`; EP-19).

`formal/tla/mirror-publish/traces/corpus.json` holds TLC-simulated traces of `MirrorPublish.tla` (`gen_traces.py`).
Each trace is replayed here over the in-memory file system (`tests/fakes/memfs.py`): every publish job of the model is
the code's rebuild job, `ledger_engine.ledger.rebuild.run` (the folder, the cleanup, the load, `mirror.publish.publish`
and its swap), with the ports a test gives it (`Ports`): the fake, a store that writes what the fake store writes and
the rows the load read (`ReplayStore`), an observation and a load of the model's two ledger files, and the tag of the
model's generation; a cut save's recovery is `ledger_engine.ledger.restore.recover_journal`; a reader's resolve is the
gate's `mirror.publish.read_published`, its reads open the pinned name as the sidecar's reader does (§7: the store's
`meta`, refused with a journal beside it).

**The world.** The model's files 1 and 2 are `/L/main.bean` and `/L/two.bean`, a version `v` the bytes `; i v`. The
mirror folder is `/cache/workspaces/k1/mirror`; the model's generation `g` is `gen-<N>-<g as 16 hex digits>.db`
(the tag names the model's name: `N` is the code's own). A cut save (`SaveCut`) is its `.open` record in
`/w/local/k1/journal` naming file 1 (pre the version it had, post the new one, its backup) and file 1 at the new
version; an outside edit a durable write.

**The walk.** As the engine-jobs replays do: each effect of the code is classified to the model step it belongs to
(`Driver.classify`; the folder and the cleanup are the cleanup's block, the store's create and journal `P_Create`, its
write and the journal's removal `P_Commit`, the file's sync `P_SyncFile`, the folder's `P_DirSync`, the link's
`P_Swap`); the observation before the load, the load and the re-check's observation are hooks too (`P_Obs`, `P_Read`,
`P_ReObs`). At each hook the model is walked up to the code's step: the outside edits, a save cut and readers' steps
made on the way are carried out, an engine death raised there, and the fake is compared with the model just before its
step (`View`: each generation's content, its rows and stamp, its journal; the link; the ledger). A step passed on the
way must change nothing the view shows; the syncs (`SYNC_STEPS`) are never passed without the code's. The model's
cleanup is one block (`whole_cleanup`: it removes all it does not keep, as the code's does once its grace period is
over), compared at its end. After each job its answer must be the model's: published the generation the model swapped
in, refused incomplete, moved, paused, or ended by a death. Each reader's resolve must pin the model's generation, and
each read read exactly the model's snapshot (or be refused where the model's is).

**Where the model and the code differ** (MAPPING.md §3): the rebuild looks for an `.open` journal right after the
cleanup and ends `paused` before it loads (engine-jobs §7, S0088), where the model's job builds a generation and
refuses at `P_JCheck`: the model's unpublished generation of such a job has no file in the code (`Replay.ghosts`) until
the model's next cleanup removes it. The code skips a build when the published generation is fresh (§8): each job here
runs on another day, so none is. The code observes the whole set before and after its load (the model observes file
by file, then reads): the corpus has no outside edit inside the load (`edit_outside_the_load`).

Not replayed: a power loss and the OS's purge (`gen_traces.py` `OUT`; `test_publish_crash.py` checks every power-loss
state at every effect, `test_publish.py` the purge), and a job the walk cut before it ended.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Final, override

import pytest

from ledger_engine.fs.port import DirHandle, Kind
from ledger_engine.ledger import publishing
from ledger_engine.ledger import rebuild as rb
from ledger_engine.ledger import restore as rs
from ledger_engine.ledger.beancount_port import ParseResult
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import FileEntry, FolderEntry, Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, FileState, Fingerprint, LedgerSet, decode
from ledger_engine.ledger.handles import drive_of, known_birth
from ledger_engine.ledger.writer import sha256
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.build import BuildFailed, Built, Stamp
from ledger_engine.mirror.core import schema
from tests.fakes.memfs import Crash, MemDir, MemFS
from tests.mirror.fake_store import MAGIC, FakeStore

pytestmark = [pytest.mark.inv("INV-05")]

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "formal" / "tla" / "mirror-publish" / "traces" / "corpus.json"
MODEL = REPO / "formal" / "tla" / "mirror-publish" / "MirrorPublish.tla"

type St = dict[str, Any]

CACHE: Final = "/cache"
KEY: Final = "k1"
LINEAGE: Final = "lin1"
ALD: Final = "/w"
MIRROR: Final = f"{CACHE}/workspaces/{KEY}/mirror"
JD: Final = f"{ALD}/local/{KEY}/journal"
BD: Final = f"{ALD}/local/{KEY}/backups"
LEDGER: Final = "/L"
PATH: Final = {1: f"{LEDGER}/main.bean", 2: f"{LEDGER}/two.bean"}
NOW_NS: Final = 10**18  # long after every modification time of the fake: no generation is in its grace period
FIRST_DAY: Final = date(2026, 1, 1)
SESSION: Final = "cut"
PCS_BEFORE_BUILD: Final = frozenset({"clean", "obs1", "obs2", "read1", "read2", "create"})
SYNC_STEPS: Final = frozenset({"P_SyncFile", "P_DirSync"})
"""The publish's syncs: the file's data (step 5) and the folder (step 6). The code may never pass one without it."""


class Divergence(AssertionError):
    pass


def content(i: int, v: int) -> bytes:
    return f"; {i} {v}\n".encode()


def version(i: int, digest: str) -> int:
    """The model version of file `i` whose bytes hash to `digest` (0 when none of the first versions does)."""
    return next((v for v in range(1, 64) if sha256(content(i, v)) == digest), 0)


# -- the corpus --------------------------------------------------------------------------------------------------------


def undelta(steps: list[St]) -> list[St]:
    """The states of a trace from its deltas (a copy of `gen_traces.undelta`, which a test may not import)."""
    states: list[St] = []
    for step in steps:
        state = {**(states[-1] if states else {}), **step}
        for k in state.pop("-", []):
            del state[k]
        states.append(state)
    return states


@cache
def corpus() -> tuple[St, tuple[St, ...]]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[1:]]
    return json.loads(lines[0]), tuple({**r, "states": undelta(r["steps"])} for r in rows)


def worlds() -> list[str]:
    meta, _ = corpus()
    return sorted(meta["constants"])


# -- the store, the observation and the load ---------------------------------------------------------------------------


class ReplayStore(FakeStore):
    """The fake store's build (the same effects in the same order) with the rows the load read written beside the
    stamp, so a reader can tell the rows from the stamp as the model does (`rows`, `lab`)."""

    @override
    def build(
        self, d: DirHandle, name: str, result: ParseResult, today: date, stamp: Stamp
    ) -> Built | BuildFailed | pb.Replaced:
        assert isinstance(d, MemDir)
        facts = {
            "surface_version": str(schema.SURFACE_VERSION),
            "ledger_version": stamp.ledger_version,
            "fingerprint": stamp.fingerprint,
            "export_version": schema.EXPORT_VERSION,
            "built_at": stamp.built_at,
            "build_complete": "1" if self.complete else "0",
            "engine_build": stamp.engine_build,
            "as_of": today.isoformat(),
            "published_by": stamp.published_by,
            "rows": result[2]["ft_rows"],
        }
        journal_ = f"{name}-journal"
        made = d.create(name, b"", 0o600)
        d.create(journal_, b"hot journal", 0o600)
        d.write_in(name, MAGIC + json.dumps(facts, sort_keys=True).encode())
        d.remove(journal_)
        st = d.stat(name)
        if st is None or st.identity != made:
            return pb.Replaced()
        return Built(name, {}, self.complete)


def g_of(name: str) -> int:
    gen = pb.gen_of(name)
    assert gen is not None, name
    return int(name[len(f"gen-{gen.number}-") : -len(".db")], 16)


def tag_of(g: int) -> str:
    return f"{g:016x}"


def raw_observe(fs: MemFS, ledger_set: LedgerSet) -> Fingerprint:
    def state(p: str) -> FileState:
        data = fs.file(p)
        return Absent() if data is None else Content(sha256(data))

    return Fingerprint.of(ledger_set.root, {p: state(p) for p in ledger_set.files}, {}, {}, {})


SET: Final = LedgerSet(PATH[1], (PATH[1], PATH[2]), (), (), ())


# -- the views ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Gen:
    k: str
    rows: tuple[int, ...]
    lab: tuple[int, ...]
    jl: bool


@dataclass(frozen=True)
class View:
    gens: tuple[tuple[int, Gen], ...]
    lk: int
    disk: tuple[int, ...]
    jopen: bool


def model_gen(c: St, jl: bool) -> Gen:
    return Gen(c["k"], tuple(c["rows"]), tuple(c["lab"]), jl)


def model_view(st: St, ghosts: set[int]) -> View:
    gens = tuple(
        (g, model_gen(x, x["jl"]))
        for g, x in enumerate(st["gens"], start=1)
        if g not in ghosts and (x["k"] != "abs" or x["jl"])
    )
    return View(gens, st["lk"], tuple(st["disk"]), st["jopen"])


def file_content(data: bytes | None) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    """A generation file's content in the model's words: absent, a build still writing it (no whole mirror yet), or
    a whole one, complete or not, with its rows and the versions its stamp names."""
    if data is None:
        return "abs", (0, 0), (0, 0)
    if not data.startswith(MAGIC):
        return "part", (0, 0), (0, 0)
    facts = json.loads(data[len(MAGIC) :])
    fp = decode(str(facts["fingerprint"]).encode("ascii"))
    states = dict(fp.files)
    lab = tuple(version(i, s.sha256) if isinstance(s := states.get(PATH[i]), Content) else 0 for i in (1, 2))
    kind = "done" if facts["build_complete"] == "1" else "inc"
    return kind, tuple(int(v) for v in facts["rows"]), lab


def code_view(fs: MemFS) -> View:
    gens: dict[int, Gen] = {}
    lk = 0
    names = fs.names_in(MIRROR) if not fs.resolve(MIRROR).rest else []
    for n in names:
        base = n.removesuffix("-journal")
        if pb.gen_of(base) is None:
            assert n.startswith(pb.CURRENT), f"a name the model has no counterpart for: {n}"
            continue
        g = g_of(base)
        if g in gens:
            continue
        k, rows, lab = file_content(fs.file(f"{MIRROR}/{base}"))
        gens[g] = Gen(k, rows, lab, f"{base}-journal" in names)
    if pb.CURRENT in names:
        with fs.open_dir(MIRROR) as d:
            target = d.readlink(pb.CURRENT)
        lk = g_of(target) if target is not None and pb.gen_of(target) is not None else -1
    disk = tuple(version(i, sha256(fs.file(PATH[i]) or b"")) for i in (1, 2))
    jopen = journal.file_name(journal.record_name(SESSION, "j"), State.OPEN) in (fs.names_in(JD))
    return View(tuple(sorted(gens.items())), lk, disk, jopen)


# -- a job -------------------------------------------------------------------------------------------------------------


@dataclass
class Event:
    kind: str  # "edit", "kill", "reader"
    act: str = ""
    arg: Any = None
    want: St | None = None  # a reader's state after its step, or an edit's disk


@dataclass
class Point:
    pc: str
    last: St
    events: list[Event] = field(default_factory=lambda: list[Event]())
    step: str = ""
    after: St | None = None
    steps: list[str] = field(default_factory=lambda: list[str]())  # every step of a block (the cleanup's)


READER_ACTS: Final = frozenset({"R_Resolve", "R_Read1", "R_Read2", "R_End"})


def event_of(a: St, b: St) -> Event | None:
    act = b["act"]["a"]
    if act == "Editor":
        return Event("edit", act, int(b["act"]["p"]), {"disk": b["disk"]})
    if act in READER_ACTS:
        r = str(b["act"]["p"])
        return Event("reader", act, r, {"before": a["rd"][r], "after": b["rd"][r]})
    return None


def job_points(states: list[St], i: int) -> tuple[list[Point], int]:
    """The points of the publish job that starts at `states[i]` (pc `clean`) and goes on while its pc is not `none`;
    the cleanup's steps are one point (its block); the index of the first state past the job."""
    points = [Point("clean", states[i])]
    j = i
    while j + 1 < len(states):
        a, b = states[j], states[j + 1]
        act = b["act"]["a"]
        p = points[-1]
        ev = event_of(a, b)
        if ev is not None:
            p.events.append(ev)
            if p.pc == "clean" and p.step:  # inside the cleanup's block: its removals are compared at its end
                p.last = {**p.last, "disk": b["disk"], "jopen": b["jopen"]}
            else:
                p.last = b
            j += 1
            continue
        if act == "EngineCrash":
            p.events.append(Event("kill"))
            p.step, p.after = act, b
            return points, j + 1
        assert act not in ("SaveCut", "Recover", "AppCrash"), f"{act} inside a job"
        p.step, p.after = act, b
        p.steps.append(act)
        if b["ctl"]["pc"] == "none":
            return points, j + 1
        if not (p.pc == "clean" and b["ctl"]["pc"] == "clean"):
            points.append(Point(b["ctl"]["pc"], b))
        j += 1
    return points, len(states)


class Cut(Exception):
    """The walk ended inside a job (not a difference)."""


class Driver:
    def __init__(self, replay: Replay, points: list[Point], where: str, job: int) -> None:
        self.replay = replay
        self.fs = replay.fs
        self.points = points
        self.where = where
        self.at = -1
        self.landed = False
        self.ops: set[str] = set()
        self.built = False  # the store created this job's generation file
        self.journalled = False  # ... and its journal
        self.loaded = False
        self.compared = 0
        self.walked: Counter[str] = Counter()
        self.killed = False
        self.job = job

    def compare(self, i: int) -> None:
        p = self.points[i]
        want, got = model_view(p.last, self.replay.ghosts), code_view(self.fs)
        if want != got:
            msg = f"{self.where}: at the model's {p.pc}, the fake holds\n  {got}\nwhere the model has\n  {want}"
            raise Divergence(msg)
        self.compared += 1

    def reach(self, pc: str) -> None:
        if self.at >= 0 and self.points[self.at].pc == pc:
            return
        if self.landed:
            p = self.points[self.at]
            if p.step in SYNC_STEPS and "sync" not in self.ops and "sync_file" not in self.ops:
                msg = f"{self.where}: the code is at {pc}, past the model's {p.step} without its sync"
                raise Divergence(msg)
            self.walked[p.step] += 1
        self.landed = False
        self.ops = set()
        for i in range(self.at + 1, len(self.points)):
            p = self.points[i]
            self.at = i
            for e in p.events:
                if e.kind == "kill":
                    self.compare(i)
                    self.killed = True
                    msg = f"the model's engine ended at {p.pc}"
                    raise Crash(msg)
                self.replay.outside(e, self.where)
            if p.pc == pc:
                self.compare(i)
                self.landed = True
                return
            if p.step in SYNC_STEPS:
                msg = f"{self.where}: the code is at {pc}, past the model's {p.step} without its sync"
                raise Divergence(msg)
            if p.after is not None and model_view(p.last, self.replay.ghosts) != model_view(
                p.after, self.replay.ghosts
            ):
                msg = f"{self.where}: the code is at {pc}, past the model's {p.pc} ({p.step}), which changes the disk"
                raise Divergence(msg)
            if p.after is None:
                raise Cut
        msg = f"{self.where}: the code is at {pc}, which the model's job does not reach"
        raise Divergence(msg)

    def finish(self, answered: bool, *, ghost: bool = False) -> None:
        """The code has ended. Every model step left must change nothing the view shows (with `ghost`: the code's job
        ended `paused` before its load, so what the model's job builds is the model's alone); the events left are
        carried out."""
        if answered and self.landed:
            self.walked[self.points[self.at].step] += 1
        self.landed = False
        for i in range(self.at + 1, len(self.points)):
            p = self.points[i]
            for e in p.events:
                if e.kind == "kill":
                    if not ghost:
                        msg = f"{self.where}: the model's engine ended at {p.pc}, after the code's answer"
                        raise Divergence(msg)
                    continue
                self.replay.outside(e, self.where)
            if ghost and p.step == "P_Create" and p.after is not None:
                self.replay.ghosts.add(p.after["ctl"]["g"])
            if p.after is None:
                raise Cut
            if not ghost and model_view(p.last, self.replay.ghosts) != model_view(p.after, self.replay.ghosts):
                msg = f"{self.where}: the code ended before the model's {p.pc} ({p.step}), which changes the disk"
                raise Divergence(msg)
            if answered and not ghost and p.step in SYNC_STEPS:
                msg = f"{self.where}: the code answered before the model's {p.step}, without its sync"
                raise Divergence(msg)

    # -- the hooks ------------------------------------------------------------------------------------------------

    def classify(self, label: str) -> str:
        op, _, rest = label.partition(" ")
        folder, _, names_ = rest.partition(":")
        name = names_.split(",")[0]
        if folder != MIRROR:
            if MIRROR.startswith(folder) or folder == MIRROR.rpartition("/")[0]:
                return "clean"  # the folder made (the first job), its parent synced: before the cleanup's removals
            msg = f"{self.where}: an effect outside the mirror folder: {label}"
            raise Divergence(msg)
        gen = pb.gen_of(name.removesuffix("-journal"))
        jnl = name.endswith("-journal")
        if gen is not None and jnl and op in ("create", "write", "fsync") and self.built:
            self.journalled = True
            return "create"  # the build's journal beside its file: `P_Create` makes both
        if gen is not None and not jnl and op in ("create", "write", "fsync") and not self.journalled:
            self.built = True
            return "create"
        if gen is not None and not jnl and op == "write" and self.journalled:
            return "commit"
        if gen is not None and jnl and op == "remove" and self.journalled:
            return "commit"
        if op == "sync_file":
            return "sync"
        if op == "sync" and self.built:
            return "dsync"
        if op in ("touch", "symlink", "move", "rename") or (op == "remove" and self.built):
            return "swap"
        if op in ("remove", "rmdir") and not self.built:
            return "clean"
        msg = f"{self.where}: an effect the replay cannot place: {label}"
        raise Divergence(msg)

    def on_effect(self, i: int, label: str) -> None:
        del i
        self.reach(self.classify(label))
        self.ops.add(label.partition(" ")[0])

    def observed(self) -> None:
        """An observation of the ledger set: the one before the load is the model's `P_Obs`, the one after the
        build (the re-check, step 7) its `P_ReObs`; the code's observations after the load and before the build read
        the same disk (no edit is made there) and are not steps."""
        if not self.loaded:
            self.reach("obs1")
        elif self.built:
            self.reach("re1")

    def load(self) -> ParseResult:
        self.reach("read1")
        self.loaded = True
        rows = [version(i, sha256(self.fs.file(PATH[i]) or b"")) for i in (1, 2)]
        return [], [], {"filename": PATH[1], "include": [PATH[1], PATH[2]], "ft_rows": rows}


# -- the replay --------------------------------------------------------------------------------------------------------


@dataclass
class Tally:
    jobs: int = 0
    compared: int = 0
    reads: int = 0
    walked: Counter[str] = field(default_factory=lambda: Counter[str]())
    branches: Counter[str] = field(default_factory=lambda: Counter[str]())
    stops: Counter[str] = field(default_factory=lambda: Counter[str]())


class Job:
    def progress(self, phase: rb.Phase) -> None:
        del phase


def model_end(points: list[Point]) -> str:
    """How the model's job ended: by its last step."""
    last = points[-1]
    match last.step:
        case "P_Swap":
            return "published"
        case "P_Check":
            return "incomplete"
        case "P_ReObs":
            return "moved"
        case "P_JCheck":
            return "paused"
        case "EngineCrash":
            return "crash"
        case _:
            msg = f"a job that ends at {last.step}"
            raise AssertionError(msg)


def code_end(out: rb.Answer | None) -> str:
    if out is None:
        return "crash"
    if out.outcome == "loaded":
        return "published" if isinstance(out.published, pb.Published) else "incomplete"
    return out.outcome


class Replay:
    def __init__(self, trace: St, tally: Tally) -> None:
        self.states: list[St] = trace["states"]
        self.where0 = f"{trace['world']} trace {trace['trace']}"
        self.tally = tally
        self.ghosts: set[int] = set()
        self.pins: dict[str, str] = {}
        self.jobs = 0
        self.fs = MemFS()
        self.fs.outside_mkdirs(LEDGER)
        self.fs.outside_mkdirs(CACHE)
        self.fs.outside_mkdirs(JD)
        self.fs.outside_mkdirs(BD)
        for i in (1, 2):
            self.fs.outside_write(PATH[i], content(i, self.states[0]["disk"][i - 1]))
        self.store = ReplayStore()

    # -- outside the engine's jobs: an editor, a reader, a save cut, its recovery --------------------------------

    def outside(self, e: Event, where: str) -> None:
        if e.kind == "edit":
            i = int(e.arg)
            assert e.want is not None
            self.fs.outside_write(PATH[i], content(i, e.want["disk"][i - 1]))
        elif e.kind == "reader":
            self.reader(e, where)

    def reader(self, e: Event, where: str) -> None:
        """A reader's step (§7): resolve `current.db` once (the gate's `read_published`), then open the pinned name
        by its name and read it, or be refused (a journal beside it, a build still writing it, no file)."""
        assert e.want is not None
        r, after = str(e.arg), e.want["after"]
        if e.act == "R_Resolve":
            cur = pb.read_published(self.fs, CACHE, KEY, self.store)
            assert cur is not None, f"{where}: the reader resolves nothing, the model pins {after['g']}"
            assert g_of(cur.gen.name) == after["g"], f"{where}: the reader pins {cur.gen.name}, the model {after['g']}"
            self.pins[r] = cur.gen.name
            self.tally.walked["R_Resolve"] += 1
        elif e.act in ("R_Read1", "R_Read2"):
            name = self.pins[r]
            with self.fs.open_dir(MIRROR) as d:
                facts = self.store.meta(d, name)
                data = d.read(name) if facts is not None else None
            if after["st"] == "idle":
                assert facts is None, f"{where}: the reader read {name}, the model's is refused"
                self.pins.pop(r)
                self.tally.branches["a read refused"] += 1
                return
            k, rows, lab = file_content(data)
            snap = after["s1"] if e.act == "R_Read1" else after["s2"]
            got, want = (k, rows, lab), (snap["k"], tuple(snap["rows"]), tuple(snap["lab"]))
            assert got == want, f"{where}: the reader read {got} at {name}, the model's snapshot is {want}"
            self.tally.reads += 1
            if e.act == "R_Read2":
                self.tally.branches["a request read twice, the same generation"] += 1
            if g_of(name) != self.current_g():
                self.tally.branches["a read of an older generation after a newer one is current"] += 1
        else:
            self.pins.pop(r, None)

    def current_g(self) -> int:
        with self.fs.open_dir(MIRROR) as d:
            cur = pb.current(d)
        return 0 if cur is None else g_of(cur.name)

    def save_cut(self, after: St) -> None:
        """A save of ours ended by an engine death after its first file: its `.open` record (naming file 1, its old
        version backed up) and file 1 at its new version, as the save leaves them (engine-jobs §6.6)."""
        pre, post = after["jpre"][0], after["jpost"]
        name = journal.record_name(SESSION, "j")
        backup = journal.backup_name(0, "main.bean")
        self.fs.outside_mkdirs(f"{BD}/{name}")
        self.fs.outside_write(f"{BD}/{name}/{backup}", content(1, pre))
        with self.fs.open_dir(LEDGER) as h:
            mark = h.mark()
        volume = drive_of(mark)
        assert volume is not None
        entry = FileEntry(
            canonical_path=PATH[1],
            path_key=core_paths.path_key(self.fs.resolve(PATH[1])),
            pre=sha256(content(1, pre)),
            post=sha256(content(1, post)),
            backup=backup,
            temp=journal.temp_name("main.bean", name),
        )
        record = Record(
            session=SESSION, job="j", workspace_key=KEY, lineage=LINEAGE, engine_build="b",
            backup_dir=f"{BD}/{name}", files=(entry,), created_dirs=(),
            folders=(FolderEntry(LEDGER, volume, mark.fs_type, known_birth(mark)),),
        )  # fmt: skip
        self.fs.outside_write(PATH[1], content(1, post))
        self.fs.outside_write(f"{JD}/{journal.file_name(name, State.OPEN)}", journal.encode(record))

    def run(self) -> None:
        i = 0
        while i < len(self.states) - 1:
            a, b = self.states[i], self.states[i + 1]
            act = b["act"]["a"]
            where = f"{self.where0}, step {i + 1} ({act})"
            if act == "StartPublish":
                i = self.job(i + 1)
                continue
            if act == "SaveCut":
                self.save_cut(b)
                self.tally.branches["a save of ours cut with its `.open` journal"] += 1
            elif act == "Recover":
                out = rs.recover_journal(self.fs, JD, journal.record_name(SESSION, "j"))
                assert isinstance(out, rs.Resolved), f"{where}: recovery answered {out}"
                self.tally.walked["Recover"] += 1
            elif act == "AppCrash":
                self.pins.clear()
            else:
                ev = event_of(a, b)
                assert ev is not None, f"{where}: {act} outside a job"
                self.outside(ev, where)
            self.check(b, where)
            i += 1

    def check(self, st: St, where: str) -> None:
        self.ghosts = {g for g in self.ghosts if st["gens"][g - 1]["k"] != "abs" or st["gens"][g - 1]["jl"]}
        want, got = model_view(st, self.ghosts), code_view(self.fs)
        assert want == got, f"{where}: the fake holds\n  {got}\nwhere the model has\n  {want}"

    def job(self, i: int) -> int:
        """One publish job from `states[i]` (pc `clean`): the code's rebuild with the model's world as its ports."""
        points, nxt = job_points(self.states, i)
        self.jobs += 1
        self.tally.jobs += 1
        where = f"{self.where0}, the job at step {i}"
        driver = Driver(self, points, where, self.jobs)
        creates = [p.after for p in points if p.step == "P_Create" and p.after is not None]
        g = creates[0]["ctl"]["g"] if creates else 99
        commit = next((p.after for p in points if p.step == "P_Commit" and p.after is not None), None)
        self.store.complete = commit is None or commit["gens"][commit["ctl"]["g"] - 1]["k"] == "done"

        def observe(ledger_set: LedgerSet) -> Fingerprint:
            driver.observed()
            return raw_observe(self.fs, ledger_set)

        # The driver's load names files of the fake disk; the rounding warnings (S0098) read no file of it.
        ports = rb.Ports(load=lambda root, parsed: driver.load(), warnings=lambda entries, options, parsed: [],
                         observe=observe,
                         fs=self.fs, store=self.store, tag=lambda: tag_of(g))  # fmt: skip
        target = publishing.Target(
            app_cache=CACHE, workspace_key=KEY, app_local_data=ALD, lineage=LINEAGE, floor=0,
            today=FIRST_DAY + timedelta(days=self.jobs), built_at="2026-01-01T00:00:00Z", now_ns=NOW_NS,
            engine_build="b",
        )  # fmt: skip
        hint = raw_observe(self.fs, SET)
        self.fs.on_effect = driver.on_effect
        out: rb.Answer | None = None
        try:
            out = rb.run(ports, hint, Job(), target)
        except Crash:
            assert driver.killed, f"{where}: the code's engine died where the model's did not"
        except Cut:
            self.tally.stops["cut"] += 1
            return len(self.states)
        finally:
            self.fs.on_effect = None
        early = out is not None and out.outcome == "paused" and not driver.loaded
        try:
            driver.finish(out is not None, ghost=early)
        except Cut:
            self.tally.stops["cut"] += 1
            return len(self.states)
        want, got = model_end(points), code_end(out)
        if early:
            assert want != "published", f"{where}: the code paused before its load, the model's job published"
            self.tally.branches["paused before the load: an `.open` journal at the start"] += 1
            self.tally.walked["P_JCheck"] += int(want == "paused")
        else:
            assert got == want, f"{where}: the code's job ends {got} ({out}), the model's {want}"
            if isinstance(out, rb.Answer) and isinstance(out.published, pb.Published):
                assert g_of(out.published.gen.name) == self.states[nxt]["lk"]
            self.tally.branches[f"the job ends {got}"] += 1
            if got in ("moved", "paused", "incomplete"):
                self.tally.walked[{"moved": "P_ReObs", "paused": "P_JCheck", "incomplete": "P_Check"}[got]] += 1
        self.tally.compared += driver.compared
        self.tally.walked.update(driver.walked)
        if driver.at >= 0 and any(a in ("P_CleanDb", "P_CleanJl") for a in points[0].steps):
            self.tally.walked["P_CleanDb"] += 1  # the cleanup's block (its removals) was the code's
        self.check(self.states[nxt], where)
        return nxt


def replay_trace(trace: St, tally: Tally) -> None:
    Replay(trace, tally).run()


def run_world(world_name: str) -> Tally:
    tally = Tally()
    for trace in corpus()[1]:
        if trace["world"] == world_name:
            replay_trace(trace, tally)
    return tally


@cache
def every_world() -> Tally:
    tally = Tally()
    for w in worlds():
        t = run_world(w)
        tally.jobs += t.jobs
        tally.compared += t.compared
        tally.reads += t.reads
        tally.walked.update(t.walked)
        tally.branches.update(t.branches)
        tally.stops.update(t.stops)
    return tally


# -- the tests ---------------------------------------------------------------------------------------------------------


def test_the_corpus_is_current() -> None:
    """The corpus was made from the model, the worlds and the generator as they are (`gen_traces.py --check`'s
    header), so a changed model fails here until it is regenerated."""
    meta, _ = corpus()
    model = MODEL.read_text(encoding="utf-8").encode()
    import hashlib

    assert meta["header"]["model"]["sha256"] == hashlib.sha256(model).hexdigest(), (
        f"the trace corpus is stale: {meta['header']['command']}"
    )


@pytest.mark.parametrize("world_name", worlds())
def test_the_publish_follows_the_model(world_name: str) -> None:
    run_world(world_name)


WALKED: tuple[str, ...] = (
    "StartPublish", "P_CleanDb", "P_CleanDone", "P_Obs", "P_Read", "P_Create", "P_Commit", "P_Check", "P_SyncFile",
    "P_DirSync", "P_ReObs", "P_JCheck", "P_Swap", "Recover", "R_Resolve",
)  # fmt: skip
"""The model's steps the replay walks through the code (a hook of the code's at the step, the code's answer that ends
the job there, or the code's own call: the cleanup's removals as one block, `Recover`, the gate's resolve)."""

NOT_WALKED: Mapping[str, str] = {
    "P_CleanJl": "a journal left without its file: only a death or a power loss inside the build leaves one, and the "
    "next cleanup removes it in the same block as `P_CleanDb` (counted there)",
    "SaveCut": "a cut save's effects are the save replay's (`engine/tests/ledger/test_save_conformance.py`, an engine "
    "death past the commit point); here its record and its file are written as it leaves them",
    "R_Read1": "the sidecar's read (`app.mirror.files.open_mirror`): the replay opens the pinned name as it does, with "
    "the store's `meta`; the sidecar's own code is `backend/tests/mirror/`'s",
    "R_Read2": "as `R_Read1`",
    "R_End": "no step of ours (the map's `CODELESS`)",
}


def test_the_replay_walks_the_publish() -> None:
    tally = every_world()
    walked = Counter(tally.walked)
    walked["StartPublish"] = tally.jobs  # each job of the model is a call of `rebuild.run`
    missing = [a for a in WALKED if walked[a] == 0]
    assert not missing, f"the replay never walks {missing}: widen the worlds in gen_traces.py ({dict(walked)})"
    missing_b = [b for b in BRANCHES if tally.branches[b] == 0]
    assert not missing_b, f"the replay never walks {missing_b}: {dict(tally.branches)}"
    assert tally.reads >= 20, tally


BRANCHES: tuple[str, ...] = (
    "the job ends published",
    "the job ends incomplete",
    "the job ends moved",
    "the job ends crash",
    "paused before the load: an `.open` journal at the start",
    "a save of ours cut with its `.open` journal",
    "a request read twice, the same generation",
    "a read of an older generation after a newer one is current",
)


def test_the_steps_are_the_models() -> None:
    """`WALKED` and `NOT_WALKED` together are the model's actions of ours (its `EngineStep` and `ReaderStep`)."""
    model = MODEL.read_text(encoding="utf-8")
    body = model[model.index("EngineStep ==") : model.index("EnvStep ==")]
    steps = set(re.findall(r"\b((?:P|R)_[A-Za-z0-9]+|StartPublish|SaveCut|Recover)\b", body))
    assert not set(WALKED) & set(NOT_WALKED)
    assert set(WALKED) | set(NOT_WALKED) == steps


def test_no_job_is_cut() -> None:
    assert every_world().stops["cut"] == 0


def _returns[T](value: T) -> Callable[..., T]:
    def stand_in(*args: object) -> T:
        del args
        return value

    return stand_in


def _first_failure() -> str:
    every_world.cache_clear()
    try:
        every_world()
    except AssertionError as e:
        return str(e)
    finally:
        every_world.cache_clear()
    return ""


# The replay is not vacuous: each seeded divergence is caught.


def test_a_publish_without_the_file_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """§4 step 5 (the model's `no_file_sync`): the generation's data not synced before the swap."""
    sync_file = MemDir.sync_file

    def skipped(self: MemDir, name: str) -> None:
        if pb.gen_of(name) is None:
            sync_file(self, name)

    monkeypatch.setattr(MemDir, "sync_file", skipped)
    assert re.search(r"past the model's P_SyncFile without its sync", _first_failure())


def test_a_publish_without_the_recheck_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """§4 step 7 (the model's `no_recheck`, `no_open_check`): the re-check skipped, so a moved or paused build is
    published."""
    monkeypatch.setattr(publishing, "recheck", _returns(_returns("same")))
    assert re.search(
        r"the code's job ends published .*the model's (moved|paused)|swap, which the model's job does not "
        r"reach",
        _first_failure(),
    )


def test_an_incomplete_build_published_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """§4 step 4 (the model's `publish_incomplete`)."""
    build = ReplayStore.build

    def says_complete(self: ReplayStore, *args: Any) -> Any:
        out = build(self, *args)
        return Built(out.path, {}, True) if isinstance(out, Built) else out

    monkeypatch.setattr(ReplayStore, "build", says_complete)
    assert re.search(
        r"the code's job ends published .*the model's incomplete|sync, which the model's job does not "
        r"reach",
        _first_failure(),
    )


def test_a_cleanup_that_removes_the_current_generation_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """§6: the current generation is kept."""
    monkeypatch.setattr(pb, "_keep", _returns(set[str]()))
    assert re.search(r"the fake holds|does not reach|the reader", _first_failure())


def test_a_swap_skipped_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """§4 step 8: a publish that answers published without its swap."""
    monkeypatch.setattr(pb, "swap", _returns(None))
    assert re.search(r"the code ended before the model's swap \(P_Swap\)|the fake holds|the reader", _first_failure())


def test_the_generation_name_is_the_models() -> None:
    """The tag names the model's generation both ways."""
    for g in (1, 2, 9, 255):
        assert g_of(pb.new_name(3, 0, tag_of(g)).name) == g


def test_a_model_value_round_trips() -> None:
    for i in (1, 2):
        for v in (1, 2, 7):
            assert version(i, sha256(content(i, v))) == v
    assert file_content(None) == ("abs", (0, 0), (0, 0))
    assert file_content(b"")[0] == "part"
    with contextlib.suppress(Exception):
        assert Kind.FILE
