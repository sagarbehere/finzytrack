"""The model's create and copy replayed through the engine (S0084, BL-0201; `design/specs/engine-jobs.md` §8.2, §13;
ADR-0050; EP-19).

`formal/tla/engine-jobs/traces/corpus.json` holds TLC-simulated traces of `EngineJobs.tla` (`gen_traces.py`), among
them small create worlds (`S_create_*`) and copy worlds (`S_copy*`). Each create and each copy a trace runs is replayed
here through `ledger.create.run` and `ledger.copy.run` over the in-memory file system (`tests/fakes/memfs.py`), as
`test_save_conformance.py` replays the save.

**The create.** The model's target folder `tgt` is `/u/new`; its names are `a` = `main.beancount` (the one file the
plan writes, the ledger's root), `m` = `.finzytrack` (the manifest folder, holding `workspace.json`), `o` = `other.txt`
(a name the job does not write) and `x` = `prior.txt` (what a non-empty folder held). A model value is read from the
fake by name (`values`): `no` (absent), `ours` (the plan's bytes), `frn` (another program's bytes, `FOREIGN`), else
`torn`. The code runs one create in one call; the replay sees it at its **hooks**: each effect on the file system
(`MemFS.on_effect`, classified to the model step it belongs to, `CreateDriver.classify`) and the read-back's read of
the file it wrote (`MemFS.on_read`). At each hook the replay walks the model's create up to the step the code is at:
the files the model's outside program drops there are written to the fake (`EditorDrop`: another program's file at
`a`, `m` or `o`; `EditorOverwrite`: an editor's save by rename over `a`), an engine death made there is raised there
(`Crash`), and the fake is compared with the model's state just before the model's step: whether the folder exists,
what each name holds now, and what a power loss may leave at each name (every state of `MemFS.power_loss_states()`
must give a value the model's `tgt.pl` allows there: the code may be more durable than the model, never less). A model
step passed on the way must change nothing that view shows, so a skipped sync is caught (`CR_SyncA` and
`CR_ManifestSync` change only what a power loss leaves).

What the code answers is checked against the step that ended the model's job: `created` exactly when the model reached
`CR_Register`; otherwise the stop of that step (`STOP_REASON`). After an engine death, the fake must be exactly the
model's target, and `create.outcome` reads `.finzytrack` as ours exactly when the model's `m` is ours.

**Where the model and the code differ** (MAPPING.md §3): the model's manifest step is one step; the code writes
`.finzytrack.ft-<job>/` (not one of the model's names, so the view does not show it), renames it exclusively to
`.finzytrack` and syncs the target: its effects are `CR_ManifestRename`'s, the target's sync after the rename is
`CR_ManifestSync`. The load check and the commit point write nothing and are no model steps. **The cleanup of a stop**
(the code removes what this run made, and only that; S0080) is not in the model, whose stop leaves the folder as it
is: once the code has stopped, a name the model has as ours may be absent in the fake (and the folder the job made may
be gone with nothing in it), and nothing else may differ: another program's entry is exactly as the model has it.

**The copy.** The model's copy of W2 (`R`, `J`) checks that no unresolved journal names a source (`CP_Check`), then
reads and writes (`CP_Load`) and writes its manifest (`CP_Manifest`); its writes have no effect in the model (they are
the create's writer, replayed above). The fake's journals are made the model's at the copy's start (`R` is the source's
root, `J` a file it includes from outside its folder, `N` a file of another ledger); the copy's world is
`test_copy.World` (its sources off the fake, so the copy cannot write them). The hooks: the source's load (`cp_chk`:
the code loads before it reads the journals; both only read), each effect in the target before the manifest's temp
(`cp_load`), the manifest's (`cp_man`). Checked: the code refuses (`recovery_blocked`) exactly when the model's
`CP_Check` ends the job, before anything is written; an effect outside the copy's target is a divergence; after an
engine death no `.finzytrack` of ours stands there; `created` exactly when the model's `CP_Manifest` ran; the journal
folders are the same before and after.

Not replayed: a create or a copy the walk cut before it ended (`STOPS`). A power loss ends the session (its next start
is recover-all's, `test_recover_conformance.py`); the durable layer is compared at every step instead.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final, override

import pytest

from ledger_engine.ledger import copy as cp
from ledger_engine.ledger import create as cr
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import FileEntry
from ledger_engine.ledger.core.journal import State as JState
from ledger_engine.ledger.create import MANIFEST_DIR, Created, CreateOutcome, CreatePlan, NewFile, NotCreated
from ledger_engine.ledger.paths import path_key
from tests.fakes.memfs import Crash, MemFS
from tests.ledger import test_copy as tc
from tests.ledger.test_save_conformance import REPO, Divergence, State, corpus
from tests.ledger.worlds import with_folders

pytestmark = [pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01"), pytest.mark.inv("INV-32")]

MODEL = REPO / "formal" / "tla" / "engine-jobs" / "EngineJobs.tla"

# -- the create's world ----------------------------------------------------------------------------------------------

U: Final = "/u"
T: Final = "/u/new"
JOB: Final = "j1"
TEMP: Final = f"{MANIFEST_DIR}{cr.TEMP_INFIX}{JOB}"
MAIN: Final = b'option "title" "New"\n2026-01-01 open Assets:Cash EUR\n'
WS: Final = b'{"format": 1, "min_reader": 1, "lineage_id": "0b9e3c1a-5d2f-4e7a-8c6b-1a2b3c4d5e6f"}\n'
FOREIGN: Final = b"another program's file\n"
NAME: Final = {"a": "main.beancount", "m": MANIFEST_DIR, "o": "other.txt", "x": "prior.txt"}
NAMES: Final = ("a", "m", "o", "x")
PLAN: Final = CreatePlan(JOB, T, (NewFile(NAME["a"], MAIN),), NAME["a"], (NewFile("workspace.json", WS),))

STOP_REASON: Mapping[str, str] = {
    "CR_Target": "not_empty",
    "CR_WriteA": "taken",
    "CR_ReadBack": "read_back",
    "CR_ManifestRename": "manifest_taken",
}
"""The code's answer for the model step that stops the create (`CrStop`): the target was not empty, a name the job
writes was taken, the file the job wrote is not what it wrote, a `.finzytrack` stood there at the rename."""

STOPS: Mapping[str, str] = {"cut": "the walk ended before the job did (not a difference)"}


def _file_value(data: bytes | None, ours: bytes | None) -> str:
    if data is None:
        return "no"
    if ours is not None and data == ours:
        return "ours"
    return "frn" if data == FOREIGN else "torn"


def values(fs: MemFS) -> tuple[bool, dict[str, str]]:
    """Whether the target exists, and the model value at each of its names."""
    if fs.resolve(T).rest:
        return False, dict.fromkeys(NAMES, "no")
    there = {n.casefold() for n in fs.names_in(T)}
    out: dict[str, str] = {}
    for x in NAMES:
        if NAME[x].casefold() not in there:
            out[x] = "no"
        elif x == "m":
            out[x] = _file_value(fs.file(f"{T}/{MANIFEST_DIR}/workspace.json"), WS)
            out[x] = "torn" if out[x] == "no" else out[x]  # a `.finzytrack` without its file
        else:
            out[x] = _file_value(fs.file(f"{T}/{NAME[x]}"), MAIN if x == "a" else None)
            out[x] = "torn" if out[x] == "no" else out[x]  # a name that holds no file
    return True, out


def durable(fs: MemFS) -> dict[str, set[str]]:
    """The model values a power loss may leave at each name (`MemFS.power_loss_states`)."""
    out: dict[str, set[str]] = {x: set() for x in NAMES}
    for state in fs.power_loss_states():
        for x, v in values(state)[1].items():
            out[x].add(v)
    return out


@dataclass(frozen=True)
class TView:
    ex: bool
    c: tuple[str, ...]
    pl: tuple[frozenset[str], ...]


def model_tview(st: State) -> TView:
    t = st["tgt"]
    return TView(t["ex"], tuple(t["c"][x] for x in NAMES), tuple(frozenset(t["pl"][x]) for x in NAMES))


def tview_problem(fs: MemFS, want: TView, *, cleaned: bool) -> str:
    """How the fake differs from the model's target, or "" (the module's docstring; `cleaned`: the code has stopped
    and removed what it made)."""
    ex, now = values(fs)
    got_c = tuple(now[x] for x in NAMES)
    pl = durable(fs)
    for i, x in enumerate(NAMES):
        w, g = want.c[i], got_c[i]
        if g != w and not (cleaned and w == "ours" and g == "no"):
            return f"{x} ({NAME[x]}) holds {g!r} where the model has {w!r}"
        allowed: set[str] = set(want.pl[i]) | ({"no"} if cleaned and "ours" in want.pl[i] else set[str]())
        if not pl[x] <= allowed:
            return f"a power loss may leave {sorted(pl[x])} at {x} ({NAME[x]}) where the model allows {sorted(allowed)}"
    if ex != want.ex and not (cleaned and not ex and all(v == "no" for v in got_c)):
        return f"the folder {'exists' if ex else 'is absent'} where the model's {'exists' if want.ex else 'is absent'}"
    return ""


# -- episodes --------------------------------------------------------------------------------------------------------


@dataclass
class Event:
    kind: str  # "drop", "overwrite", "kill"
    name: str = ""


@dataclass
class Point:
    """The model's job at one step `pc`: the state just before its step (after its events), the events made there and
    the step that leaves it."""

    pc: str
    last: State
    events: list[Event] = field(default_factory=lambda: list[Event]())
    step: str = ""
    after: State | None = None

    def passable(self, view: Any) -> bool:
        return self.after is None or view(self.last) == view(self.after)

    def ends(self) -> bool:
        """The point's step ends the job (a stop, or the create's last step)."""
        return self.after is not None and self.after["run"] is None


@dataclass
class Episode:
    kind: str
    points: list[Point]
    start: State
    end: State
    killed: bool
    actions: Counter[str]
    stop: str = ""


def _running(st: State, kind: str) -> bool:
    return st["run"] is not None and st["run"]["k"] == kind


def episode(trace: State, kind: str) -> Episode | None:
    """The trace's one create or copy (the model starts each at most once), or None."""
    states: list[State] = trace["states"]
    first = next((i for i, st in enumerate(states) if _running(st, kind)), None)
    if first is None:
        return None
    st = states[first]
    points = [Point(st["ctl"]["pc"], st)]
    actions: Counter[str] = Counter()
    killed = False
    j = first
    prefix = "CR_" if kind == "create" else "CP_"
    while j + 1 < len(states) and _running(states[j], kind):
        a_, b_ = states[j], states[j + 1]
        a = b_["act"]["a"]
        actions[a] += 1
        p = points[-1]
        p.last = a_
        if a == "EditorDrop":
            p.events.append(Event("drop", str(b_["act"]["p"])))
            p.last = b_
        elif a == "EditorOverwrite":
            p.events.append(Event("overwrite", "a"))
            p.last = b_
        elif a == "EngineCrash" and not _running(b_, kind):
            p.events.append(Event("kill"))
            killed = True
        elif a.startswith(prefix):
            p.step, p.after = a, b_
            if _running(b_, kind):
                points.append(Point(b_["ctl"]["pc"], b_))
        else:
            msg = f"{trace['world']} trace {trace['trace']}: the replay cannot place {a} in a {kind}"
            raise AssertionError(msg)
        j += 1
    end = states[j]
    return Episode(kind, points, st, end, killed, actions, "cut" if _running(end, kind) else "")


# -- the create's driver ---------------------------------------------------------------------------------------------


class CreateDriver:
    """Walks the model's create as the code reaches its hooks (the module's docstring)."""

    def __init__(self, fs: MemFS, ep: Episode, where: str) -> None:
        self.fs = fs
        self.ep = ep
        self.where = where
        self.at = -1
        self.landed = False
        self.applied = -1  # the events of the points up to this one are made
        self.renamed = False
        self.cleaning = False
        self.compared = 0
        self.walked: Counter[str] = Counter()

    def pc(self) -> str:
        return self.ep.points[self.at].pc if self.at >= 0 else ""

    def compare(self, i: int, *, cleaned: bool = False) -> None:
        p = self.ep.points[i]
        problem = tview_problem(self.fs, model_tview(p.last), cleaned=cleaned)
        if problem:
            msg = f"{self.where}: at the model's {p.pc}, {problem}"
            raise Divergence(msg)
        self.compared += 1

    def events(self, i: int) -> bool:
        """Make the events of point `i` (once); True when the model's engine died there."""
        if i <= self.applied:
            return False
        self.applied = i
        killed = False
        for e in self.ep.points[i].events:
            if e.kind == "drop":
                write_foreign(self.fs, e.name)
            elif e.kind == "overwrite":
                self.fs.outside_write(f"{T}/{NAME['a']}", FOREIGN, new_inode=True)  # an editor's save by rename
            else:
                killed = True
        return killed

    def prologue(self) -> bool:
        """Before the code runs: the events of the model's first point (its job has started, the code has not reached
        a hook). True when the model's engine died there: the code never runs."""
        return self.events(0)

    def reach(self, pc: str) -> None:
        if self.cleaning:
            msg = f"{self.where}: the code goes on at {pc} after its cleanup"
            raise Divergence(msg)
        if self.at >= 0 and self.pc() == pc:
            return
        if self.landed:
            self.walked[self.ep.points[self.at].step] += 1
        self.landed = False
        for i in range(self.at + 1, len(self.ep.points)):
            p = self.ep.points[i]
            self.at = i
            if self.events(i):
                self.compare(i)
                msg = f"the model's engine ended at {p.pc}"
                raise Crash(msg)
            if p.pc == pc:
                self.compare(i)
                self.landed = True
                return
            if not p.passable(model_tview):
                msg = f"{self.where}: the code is at {pc}, past the model's {p.pc} ({p.step}), which changes the disk"
                raise Divergence(msg)
        msg = f"{self.where}: the code is at {pc}, which the model's create does not reach"
        raise Divergence(msg)

    def cleanup(self) -> None:
        """The code's first cleanup effect: the model's job must have stopped at the point the code is at."""
        if self.cleaning:
            return
        p = self.ep.points[self.at] if self.at >= 0 else None
        if p is None or not p.ends() or p.step not in STOP_REASON:
            where = p.pc if p is not None else "its start"
            msg = f"{self.where}: the code cleans up at {where}, where the model's create goes on"
            raise Divergence(msg)
        if self.landed:
            self.walked[p.step] += 1
        self.landed = False
        self.cleaning = True

    def classify(self, label: str) -> str:
        op, _, rest = label.partition(" ")
        folder, _, names = rest.partition(":")
        if op in ("remove", "rmdir") or self.cleaning:
            return "cleanup"
        if folder == U and (op == "sync" or names == posixpath.basename(T)):
            return "cr_tgt"  # the target made, its parent synced
        if folder == T:
            if names == NAME["a"]:
                return "cr_wa"
            if op == "sync":
                return "cr_ms" if self.renamed else "cr_ws"
            if names.split(",")[0] == TEMP:
                self.renamed = self.renamed or op == "rename"
                return "cr_mr"
        if folder == f"{T}/{TEMP}" or folder.startswith(f"{T}/{TEMP}/"):
            return "cr_mr"
        msg = f"{self.where}: an effect the replay cannot place: {label}"
        raise Divergence(msg)

    def on_effect(self, i: int, label: str) -> None:
        del i
        pc = self.classify(label)
        if pc == "cleanup":
            self.cleanup()
        else:
            self.reach(pc)

    def on_read(self, label: str) -> None:
        """The read-back of the file the job wrote, right after the folders' sync (other reads are the cleanup's and
        the manifest's own checks)."""
        _, _, rest = label.partition(" ")
        if rest == f"{T}:{NAME['a']}" and not self.cleaning and self.pc() == "cr_ws":
            self.reach("cr_rb")

    def finish(self, answered: bool) -> None:
        """The code has ended: every model step left must change nothing the view shows."""
        if answered and self.landed:
            self.walked[self.ep.points[self.at].step] += 1
        self.landed = False
        for i in range(self.at + 1, len(self.ep.points)):
            p = self.ep.points[i]
            self.events(i)
            if p.step and not p.passable(model_tview):
                msg = f"{self.where}: the code ended before the model's {p.pc} ({p.step}), which changes the disk"
                raise Divergence(msg)


def write_foreign(fs: MemFS, x: str) -> None:
    """Another program's entry at the model's name `x` (durably, as the model's `EditorDrop`)."""
    if x == "m":
        fs.outside_mkdirs(f"{T}/{MANIFEST_DIR}")
        fs.outside_write(f"{T}/{MANIFEST_DIR}/workspace.json", FOREIGN)
    else:
        fs.outside_write(f"{T}/{NAME[x]}", FOREIGN)


class LoadCheck:
    """The create's load check (step 5), as Beancount would answer for the plan's one file."""

    def __call__(self, root: str) -> cr.LoadResult:
        return cr.LoadResult(0, (root,))


class Job:
    def progress(self, phase: str) -> None:
        del phase

    def gate(self) -> None:
        pass


def create_world(st: State) -> MemFS:
    """The fake holding the model's target at the create's start."""
    fs = MemFS()
    fs.outside_mkdirs(U)
    t = st["tgt"]
    if t["ex"]:
        fs.outside_mkdirs(T)
    for x in NAMES:
        v = t["c"][x]
        assert v in ("no", "frn"), f"the model's {x} holds {v} before the create"
        if v == "frn":
            write_foreign(fs, x)
    return fs


@dataclass
class Tally:
    jobs: int = 0
    compared: int = 0
    walked: Counter[str] = field(default_factory=lambda: Counter[str]())
    stops: Counter[str] = field(default_factory=lambda: Counter[str]())
    branches: Counter[str] = field(default_factory=lambda: Counter[str]())

    def add(self, other: Tally) -> None:
        self.jobs += other.jobs
        self.compared += other.compared
        self.walked.update(other.walked)
        self.stops.update(other.stops)
        self.branches.update(other.branches)


def replay_create(ep: Episode, where: str, tally: Tally) -> None:
    fs = create_world(ep.start)
    driver = CreateDriver(fs, ep, where)
    out: CreateOutcome | None = None
    if not driver.prologue():
        fs.on_effect, fs.on_read = driver.on_effect, driver.on_read
        try:
            out = cr.run(PLAN, cr.Ports(fs, load=LoadCheck()), Job())
        except Crash:
            assert ep.killed, f"{where}: the code's engine died where the model's did not"
        finally:
            fs.on_effect = fs.on_read = None
    driver.finish(answered=out is not None)
    end = model_tview(ep.end)
    last = ep.points[-1]
    if ep.killed:
        problem = tview_problem(fs, end, cleaned=False)
        assert not problem, f"{where}: after the engine's death, {problem}"
        seen = cr.outcome(fs, PLAN).manifest
        assert (seen == "ours") == (ep.end["tgt"]["c"]["m"] == "ours"), f"{where}: the outcome reads {seen!r}"
    elif last.step == "CR_Register":
        assert out == Created(T), f"{where}: the model's create registers, the code answers {out}"
        problem = tview_problem(fs, end, cleaned=False)
        assert not problem, f"{where}: after the create, {problem}"
    else:
        want = STOP_REASON[last.step]
        assert isinstance(out, NotCreated), f"{where}: the model's create stops at {last.step}, the code answers {out}"
        assert out.reason == want, f"{where}: the model's create stops at {last.step} ({want}), the code: {out}"
        problem = tview_problem(fs, end, cleaned=True)
        assert not problem, f"{where}: after the stop, {problem}"
    tally.jobs += 1
    tally.compared += driver.compared + 1
    tally.walked.update(driver.walked)
    tally.branches.update(create_branches(ep, out, fs))


def create_branches(ep: Episode, out: CreateOutcome | None, fs: MemFS) -> Iterator[str]:
    pcs = [p.pc for p in ep.points]
    drops = [(p.pc, e) for p in ep.points for e in p.events if e.kind in ("drop", "overwrite")]
    if isinstance(out, Created):
        yield "created"
        if ep.start["tgt"]["ex"]:
            yield "created in an empty folder"
        if any(e.kind == "overwrite" and pc in ("cr_mr", "cr_ms", "cr_reg") for pc, e in drops):
            yield "created, the file written over after its read-back"
        if any(e.name == "o" for _, e in drops):
            yield "created beside another program's file"
    if isinstance(out, NotCreated):
        yield f"stopped: {out.reason}"
        if any(v == "frn" for v in values(fs)[1].values()):
            yield "a stop keeps another program's entry"
    if ep.killed:
        yield "an engine death after the manifest's rename" if "cr_ms" in pcs else "an engine death before the manifest"
    if ep.start["tgt"]["c"]["x"] == "frn" or any(v == "frn" for v in ep.start["tgt"]["c"].values()):
        yield "a target that was not empty at the start"


CREATE_BRANCHES: tuple[str, ...] = (
    "created",
    "created in an empty folder",
    "created, the file written over after its read-back",
    "created beside another program's file",
    "stopped: not_empty",
    "stopped: taken",
    "stopped: read_back",
    "stopped: manifest_taken",
    "a stop keeps another program's entry",
    "an engine death before the manifest",
    "an engine death after the manifest's rename",
    "a target that was not empty at the start",
)
"""The create's branches the corpus must walk through the code."""


# -- the copy --------------------------------------------------------------------------------------------------------

FILE_OF: Final = {"R": tc.ROOT, "J": tc.RATES, "N": "/s/books/n.beancount"}
"""The model's files as the copy's world names them: W2 = {R, J} is the copied ledger (its root, and a file it
includes from outside its folder); `N` is a file of W1 only."""
JFILES: Final = {"b2": ("R", "J"), "b3": ("K",), "s1": ("R", "N"), "s2": ("R", "N"), "s3": ("R", "N")}
KEY_OF: Final = {"b2": "k2", "b3": "k3", "s1": "k1", "s2": "k1", "s3": "k1"}
JSTATE: Final = {
    "open": JState.OPEN, "topen": JState.OPEN, "part": JState.PART, "closed": JState.CLOSED,
    "resolved": JState.RESOLVED, "kept": JState.KEPT,
}  # fmt: skip
"""The model's journal states as files (`topen`: an `.open` record that cannot be read)."""


def put_journal(fs: MemFS, j: str, st: str) -> None:
    """The model's journal `j` in state `st` as the engine's record (`ledger_engine.ledger.core.journal`)."""
    if st == "none":
        return
    name = journal.record_name(j, "job")
    files = tuple(
        FileEntry(
            canonical_path=FILE_OF.get(f, f"/s/other/{f}.beancount"),
            path_key=path_key(fs, FILE_OF.get(f, f"/s/other/{f}.beancount")),
            pre="0" * 64,
            post="1" * 64,
            backup=journal.backup_name(i, posixpath.basename(FILE_OF.get(f, f))),
            temp=journal.temp_name(posixpath.basename(FILE_OF.get(f, f)), name),
        )
        for i, f in enumerate(JFILES[j])
    )
    key = KEY_OF[j]
    rec = with_folders(
        session=j,
        job="job",
        workspace_key=key,
        lineage="0d4c1f1e-0b8a-4c2e-9a39-5b1f0f6f2a10",
        engine_build="dev",
        backup_dir=f"{tc.ALD}/local/{key}/backups/{name}",
        files=files,
        created_dirs=(),
    )
    jd = f"{tc.ALD}/local/{key}/journal"
    fs.outside_mkdirs(jd)
    fs.outside_write(f"{jd}/{journal.file_name(name, JSTATE[st])}", b"torn" if st == "topen" else journal.encode(rec))


def open_on_w2(jr: Mapping[str, str]) -> set[str]:
    """The model's `OpenOn(LS(2))`: the journals the copy must refuse over."""
    return {j for j, st in jr.items() if st == "topen" or (st == "open" and {"R", "J"} & set(JFILES[j]))}


def snapshot_journals(fs: MemFS) -> dict[str, tuple[object, ...]]:
    return {p: v for p, v in fs.snapshot().items() if p.startswith(f"{tc.ALD}/")}


class CopyDriver:
    """Walks the model's copy as the code reaches its hooks (the module's docstring)."""

    def __init__(self, fs: MemFS, ep: Episode, where: str) -> None:
        self.fs = fs
        self.ep = ep
        self.where = where
        self.at = -1
        self.landed = False
        self.walked: Counter[str] = Counter()
        self.compared = 0

    def reach(self, pc: str) -> None:
        if self.at >= 0 and self.ep.points[self.at].pc == pc:
            return
        here = self.ep.points[self.at] if self.at >= 0 else None
        if here is not None and here.ends():
            msg = f"{self.where}: the code goes on at {pc}, where the model's {here.pc} ({here.step}) ends the copy"
            raise Divergence(msg)
        if self.landed:
            self.walked[self.ep.points[self.at].step] += 1
        self.landed = False
        for i in range(self.at + 1, len(self.ep.points)):
            p = self.ep.points[i]
            self.at = i
            if any(e.kind == "kill" for e in p.events):
                msg = f"the model's engine ended at {p.pc}"
                raise Crash(msg)
            if p.pc == pc:
                self.landed = True
                self.compared += 1
                return
            if p.ends():
                msg = f"{self.where}: the code goes on at {pc}, where the model's {p.pc} ({p.step}) ends the copy"
                raise Divergence(msg)
        msg = f"{self.where}: the code is at {pc}, which the model's copy does not reach"
        raise Divergence(msg)

    def classify(self, label: str) -> str:
        op, _, rest = label.partition(" ")
        folder, _, names = rest.partition(":")
        target = tc.T
        temp = f"{MANIFEST_DIR}{cr.TEMP_INFIX}{tc.PLAN.job}"
        if op in ("remove", "rmdir"):
            msg = f"{self.where}: the copy removes {label}, where the model's copy goes on"
            raise Divergence(msg)
        if folder == tc.U and names in ("", posixpath.basename(target)):
            return "cp_load"
        if folder == target and names.split(",")[0] == temp:
            return "cp_man"
        if folder == f"{target}/{temp}" or folder.startswith(f"{target}/{temp}/"):
            return "cp_man"
        if folder == target and op == "sync" and self.at >= 0 and self.ep.points[self.at].pc == "cp_man":
            return "cp_man"
        if folder == target or folder.startswith(f"{target}/"):
            return "cp_load"
        msg = f"{self.where}: the copy writes outside its target: {label}"
        raise Divergence(msg)

    def on_effect(self, i: int, label: str) -> None:
        del i
        self.reach(self.classify(label))

    def finish(self, answered: bool) -> None:
        if answered and self.landed:
            self.walked[self.ep.points[self.at].step] += 1
        self.landed = False
        for p in self.ep.points[self.at + 1 :]:
            if p.ends() and p.step != "CP_Check":
                msg = f"{self.where}: the code ended before the model's {p.pc} ({p.step})"
                raise Divergence(msg)


class CopyWorld(tc.World):
    """`test_copy.World`, its source's load the replay's hook at the model's `CP_Check`."""

    driver: CopyDriver | None = None

    @override
    def load(self, root: str) -> overlay.LoadResult:
        if self.driver is not None:
            self.driver.reach("cp_chk")
        return super().load(root)


def replay_copy(ep: Episode, where: str, tally: Tally) -> None:
    w = CopyWorld(tc.new_world().fs)
    jr: Mapping[str, str] = ep.start["jr"]
    for j, st in jr.items():
        put_journal(w.fs, j, st)
    before = snapshot_journals(w.fs)
    driver = CopyDriver(w.fs, ep, where)
    w.driver = driver
    out: CreateOutcome | None = None
    killed_first = any(e.kind == "kill" for e in ep.points[0].events)
    if not killed_first:
        w.fs.on_effect = driver.on_effect
        try:
            out = cp.run(tc.PLAN, w.ports(), tc.Job())
        except Crash:
            assert ep.killed, f"{where}: the code's engine died where the model's did not"
        finally:
            w.fs.on_effect = None
    driver.finish(answered=out is not None)
    assert snapshot_journals(w.fs) == before, f"{where}: the copy changed the journals"
    last = ep.points[-1]
    refused = bool(open_on_w2(jr))
    if ep.killed:
        assert out is None, f"{where}: the model's engine died in the copy, the code answers {out}"
        seen = cp.outcome(w.fs, tc.PLAN).manifest
        assert seen != "ours", f"{where}: after the engine's death the outcome reads {seen!r}"
    elif last.step == "CP_Check":
        assert refused, f"{where}: the model's copy refuses with no open journal on W2: {jr}"
        assert isinstance(out, NotCreated), f"{where}: the code answers {out}"
        assert out.reason == "recovery_blocked", f"{where}: the code answers {out}"
        assert tc.T not in {p for p in w.fs.snapshot()}, f"{where}: a refused copy made its target"
    else:
        assert last.step == "CP_Manifest", where
        assert not refused, f"{where}: the model's copy goes on over {open_on_w2(jr)}"
        assert out == Created(tc.T), f"{where}: the model's copy ends with its manifest, the code answers {out}"
        assert cp.outcome(w.fs, tc.PLAN).manifest == "ours"
    tally.jobs += 1
    tally.compared += driver.compared + 1
    tally.walked.update(driver.walked)
    tally.branches.update(copy_branches(ep, out, jr))


def copy_branches(ep: Episode, out: CreateOutcome | None, jr: Mapping[str, str]) -> Iterator[str]:
    if out == Created(tc.T):
        yield "copied"
        if any(st in ("closed", "resolved", "part", "kept") for st in jr.values()):
            yield "copied, a journal that is not open blocks nothing"
    if isinstance(out, NotCreated) and out.reason == "recovery_blocked":
        blocking = open_on_w2(jr)
        yield "refused: a boot journal is open" if blocking & {"b2", "b3"} else "refused: a journal of this session"
        if any(jr[j] == "topen" for j in blocking):
            yield "refused: an open journal that cannot be read"
    if ep.killed:
        yield "an engine death in the copy"


COPY_BRANCHES: tuple[str, ...] = (
    "copied",
    "copied, a journal that is not open blocks nothing",
    "refused: a boot journal is open",
    "refused: a journal of this session",
    "refused: an open journal that cannot be read",
    "an engine death in the copy",
)
"""The copy's branches the corpus must walk through the code."""


# -- the replay ------------------------------------------------------------------------------------------------------


def worlds(kind: str) -> list[str]:
    """The create's worlds (a target to create in) or the copy's (W2 may be copied)."""
    meta, _ = corpus()
    constants: dict[str, dict[str, str]] = meta["constants"]
    if kind == "create":
        return sorted(w for w, c in constants.items() if c["CreateTarget"] != '"none"')
    return sorted(w for w, c in constants.items() if c["DoCopy"] == "TRUE")


def run_world(world_name: str, kind: str) -> Tally:
    tally = Tally()
    for trace in corpus()[1]:
        if trace["world"] != world_name:
            continue
        ep = episode(trace, kind)
        if ep is None:
            continue
        where = f"{trace['world']} trace {trace['trace']}, {kind}"
        if ep.stop:
            tally.stops[ep.stop] += 1
            continue
        (replay_create if kind == "create" else replay_copy)(ep, where, tally)
    return tally


@cache
def every_world() -> Tally:
    tally = Tally()
    for kind in ("create", "copy"):
        for w in worlds(kind):
            tally.add(run_world(w, kind))
    return tally


# -- the tests -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("world_name", worlds("create"))
def test_the_engine_create_follows_the_model(world_name: str) -> None:
    run_world(world_name, "create")


@pytest.mark.parametrize("world_name", worlds("copy"))
def test_the_engine_copy_follows_the_model(world_name: str) -> None:
    run_world(world_name, "copy")


WALKED: tuple[str, ...] = (
    "CR_Target", "CR_WriteA", "CR_SyncA", "CR_ReadBack", "CR_ManifestRename", "CR_ManifestSync",
    "CP_Check", "CP_Load", "CP_Manifest",
)  # fmt: skip
"""The model's create and copy steps the replay walks through the code: the code reached the step at one of its hooks
and went on (or answered). Each is marked `@tla_action` on that code
(`backend/tests/architecture/test_tla_mapping.py`)."""

NOT_WALKED: Mapping[str, str] = {
    "CR_Register": "the registry's (BL-0102): the engine answers created, the sidecar registers the folder",
}
"""The model's create and copy steps the replay does not walk, each with its reason."""


def test_the_replay_walks_the_create_and_the_copy() -> None:
    """Every step of `WALKED` is walked through the code at least once, and every branch of `CREATE_BRANCHES` and
    `COPY_BRANCHES`."""
    tally = every_world()
    assert set(tally.walked) <= set(WALKED), f"counted as walked, not in WALKED: {dict(tally.walked)}"
    missing = [a for a in WALKED if tally.walked[a] == 0]
    assert not missing, f"the replay never walks {missing}: widen the create and copy worlds in gen_traces.py"
    missing = [b for b in (*CREATE_BRANCHES, *COPY_BRANCHES) if tally.branches[b] == 0]
    assert not missing, f"the replay never walks {missing}: widen the create and copy worlds in gen_traces.py"
    assert tally.jobs >= 40, tally


def test_the_create_and_copy_steps_are_the_models() -> None:
    """`WALKED` and `NOT_WALKED` together are the model's `CR_*` and `CP_*` steps: a step added to the model fails here
    until it is placed."""
    model = MODEL.read_text(encoding="utf-8")
    steps = set(re.findall(r"^((?:CR|CP)_[A-Za-z0-9]+) ==", model, re.MULTILINE))
    assert not set(WALKED) & set(NOT_WALKED)
    assert set(WALKED) | set(NOT_WALKED) == steps


STOP_COUNTS: Mapping[str, int] = {"cut": 0}
"""The jobs of the corpus the replay does not follow, by reason (`STOPS`); pinned."""


def test_the_stops_are_counted() -> None:
    tally = every_world()
    assert set(tally.stops) <= set(STOPS), tally.stops
    assert {k: tally.stops[k] for k in STOP_COUNTS} == STOP_COUNTS, f"followed {tally.jobs}, stopped {tally.stops}"


def test_the_stop_reasons_are_the_models_stops() -> None:
    """`STOP_REASON` names every create step that can stop the job in the model (`CrStop` in its body) and no other."""
    model = MODEL.read_text(encoding="utf-8")
    bodies = re.findall(r"^(CR_[A-Za-z]+) ==(.*?)(?=^\S)", model, re.MULTILINE | re.DOTALL)
    stopping = {name for name, body in bodies if "CrStop" in body} - {"CR_Register"}
    assert set(STOP_REASON) == stopping


# -- the replay is not vacuous: each seeded divergence fails with its own message ---------------------------------


def _first_failure() -> str:
    every_world.cache_clear()
    try:
        every_world()
    except AssertionError as e:
        return str(e)
    finally:
        every_world.cache_clear()
    return ""


def test_a_skipped_folder_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A create that does not sync its folders after its writes: nothing the fake holds now differs, but a power loss
    could drop the file; the model's `CR_SyncA` changes what a power loss leaves, so it cannot be passed."""

    def no_sync(self: cr.Run) -> None:
        del self

    monkeypatch.setattr(cr.Run, "_sync", no_sync)
    assert re.search(r"past the model's cr_ws \(CR_SyncA\), which changes the disk", _first_failure())


def test_a_skipped_manifest_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The target's sync after the rename (`CR_ManifestSync`): without it a power loss may drop `.finzytrack`."""
    manifest = cr.Run._manifest  # pyright: ignore[reportPrivateUsage]

    def manifest_unsynced(self: cr.Run) -> CreateOutcome:
        th: Any = self.handles.at(self.folder)
        sync, renamed = th.sync, [False]
        rename = th.rename_excl

        def rename_excl(a: str, b: str) -> None:
            rename(a, b)
            renamed[0] = True

        def sync_unless_renamed() -> None:
            if not renamed[0]:
                sync()

        th.rename_excl, th.sync = rename_excl, sync_unless_renamed
        try:
            return manifest(self)
        finally:
            del th.rename_excl, th.sync

    monkeypatch.setattr(cr.Run, "_manifest", manifest_unsynced)
    assert re.search(
        r"(code ended before the model's cr_ms \(CR_ManifestSync\)|power loss may leave)", _first_failure()
    )


def test_a_skipped_read_back_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A create that does not read back what it wrote goes on to its manifest over another program's bytes, where the
    model's `CR_ReadBack` stops it."""

    def no_read_back(self: cr.Run) -> None:
        del self

    monkeypatch.setattr(cr.Run, "_read_back", no_read_back)
    failure = _first_failure()
    assert re.search(r"which the model's create does not reach|past the model's cr_rb \(CR_ReadBack\)", failure), (
        failure
    )


def test_a_cleanup_that_removes_another_programs_file_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stop's cleanup is outside the model, but another program's entry must stay as the model has it."""
    cleanup = cr.Run._cleanup  # pyright: ignore[reportPrivateUsage]

    def cleanup_all(self: cr.Run) -> tuple[str, ...] | None:
        h = self.handles.at(self.folder) if not self.fs.resolve(self.folder).rest else None
        if h is not None:
            for name in h.names():
                if name != MANIFEST_DIR and h.stat(name) is not None and name not in self.made:
                    h.remove(name)
        return cleanup(self)

    monkeypatch.setattr(cr.Run, "_cleanup", cleanup_all)
    assert re.search(r"after the stop, [aox] \(.*\) holds 'no' where the model has 'frn'", _first_failure())


def test_a_copy_that_skips_the_journal_check_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy that reads no journal goes on where the model's `CP_Check` refuses (`NoCopyOverOpenJournal`; the model's
    `copy_checks_blocking_only` is the narrower bug, which the mutants run on the source, S0084 Hand-off)."""

    def no_check(self: Any, sources: Any) -> None:
        del self, sources

    monkeypatch.setattr(cp._CopySteps, "_journals", no_check)  # pyright: ignore[reportPrivateUsage]
    assert re.search(
        r"the code goes on at cp_load, where the model's cp_chk \(CP_Check\) ends the copy", _first_failure()
    )


def test_a_copy_that_writes_outside_its_target_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare = cp._CopySteps.prepare  # pyright: ignore[reportPrivateUsage]

    def prepare_and_write(self: Any, run: cr.Run) -> None:
        prepare(self, run)
        with run.fs.open_dir(f"{tc.ALD}/local") as h:
            h.create("stray", b"x", 0o644)

    monkeypatch.setattr(cp._CopySteps, "prepare", prepare_and_write)  # pyright: ignore[reportPrivateUsage]
    assert "the copy writes outside its target" in _first_failure()


def test_a_value_round_trips() -> None:
    fs = MemFS()
    fs.outside_mkdirs(T)
    assert values(fs) == (True, dict.fromkeys(NAMES, "no"))
    for x in ("a", "m", "o"):
        write_foreign(fs, x)
    fs.outside_write(f"{T}/{NAME['x']}", b"cut")
    assert values(fs)[1] == {"a": "frn", "m": "frn", "o": "frn", "x": "torn"}
    fs2 = MemFS()
    fs2.outside_mkdirs(f"{T}/{MANIFEST_DIR}")
    fs2.outside_write(f"{T}/{NAME['a']}", MAIN)
    assert values(fs2)[1] == {"a": "ours", "m": "torn", "o": "no", "x": "no"}
