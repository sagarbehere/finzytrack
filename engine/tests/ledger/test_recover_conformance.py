"""The model's recover-all replayed through the engine (S0091, BL-0164; `design/specs/engine-jobs.md` §8.1, §13;
ADR-0063, ADR-0064; EP-19).

`formal/tla/engine-jobs/traces/corpus.json` holds TLC-simulated traces of `EngineJobs.tla` (`gen_traces.py`); its
recover worlds (`jobs: ["recover"]`) walk recover-all over the boot journals with no save, create or copy. Each trace is
replayed here over the in-memory file system (`tests/fakes/memfs.py`), the replay playing the sidecar's part
(`app.platform.engine_jobs.recovery`: the listing, then each `.open` journal tried in the listing's order, a death
marking the journal in hand failed, a record that cannot be read blocking without a try) and the engine's ops carried
out by the code itself: `recover_all.list_journals` (`RC_Init`), `restore.recover_journal` (`RC_Next`'s try and the
restore `RS_*`), `recover_all.keep` (Keep).

**The world.** The model's boot journal b2 names R and J (`/w/L/main.bean`, `/w/M/j.bean`), b3 names K
(`/v/K/k.bean`, on a second drive, dev 2); their records are `boot-b2` and `boot-b3` in `/w/local/k2/journal` and
`/w/local/k3/journal`, each with the drive marks a save would record (journal format 6, S0089) and backups holding the
pre bytes. A model value `v` of file `f` is the bytes `; f v`; anything else read is torn. b3 torn (`BootTorn`): its
`.open` holds bytes that do not decode. What a crashed save left beside a file (`BootTemps`) is at its temp
(`.<name>.ft-boot-<j>.tmp`) and staging name (`.new`). K's disk unreachable (`Unreach`): its folder moved away from
`/v/K`, and back. R's folder replaced by a link (`BootLink`, `PathChange`): `/w/L` moved away and a link put in its
place to a copy of it (`/w/Lc`). An outside edit of R: a durable write of the model's bytes.

**The walk.** As the save replay does (`test_save_conformance.py`): each effect of the code (`MemFS.on_effect`) is
classified to the model step it belongs to (`Driver.classify`), and the model is walked up to that step: the outside
acts made on the way are written, an engine death or an app crash raised there (`Crash`), an I/O error raised there
(`OSError`, before the effect), and at the step the fake is compared with the model's state just before it (`View`:
the three files, each boot journal's record, the names of ours and the kept copies beside each file). A model step
passed on the way must change nothing the view shows; the model's sync steps (`SYNC_STEPS`: the listing's journal
folder syncs, the restore's folder syncs) are never passed without a sync of the code's, and a step that includes its
folder's sync (`FUSED_SYNC`) must see one before the code moves on. After each try the code's answer must be the
model's (`resolved`, `stopped`, `failed`; a death the sidecar sees is `failed`), and after a resolve every state a
power loss may leave holds the record `.resolved` over files at their pre bytes (`NoTornLedgerAfterRecovery`). The
listing must name exactly the model's unresolved journals, one that cannot be read exactly where the model's is torn;
the sidecar's order is the model's (`RC_Next`'s `JOrder`). An outside edit's bytes are never lost: in every state, and
every power-loss state at a try's end, they stand at R, at a name of ours beside it or in a kept copy
(`ExternalEditNeverSilentlyLost`). Nothing is ever written through R's link (`RecoveryWritesOnlyRecordedPaths`).

**Where the model and the code differ** (MAPPING.md §3): the code checks every folder's drive first (S0089:
`restore._drives`, the path guard included), the model file by file; the code's evidence rule for a missing file
(`missing_unconfirmed`) and its drive stops have no counterpart (the model's files are never missing and its drives
never swapped); the code refuses Keep while a folder the journal names is a link (`path_moved`, red team r1 R1-3), the
model's Keep does not: such a Keep stops the trace (`STOPS`), never compared past it.

Not replayed: a power loss (`gen_traces.py` `OUT`: the fake cannot be steered to the model's pick; the restore's own
sweeps check every power-loss state at every effect, `test_restore.py`), and a run the walk cut before it ended.
"""

from __future__ import annotations

import contextlib
import errno
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final

import pytest

from ledger_engine.ledger import recover_all as ra
from ledger_engine.ledger import restore as rs
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import FileEntry, FolderEntry, Record, State, needed_folders
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.handles import drive_of, known_birth
from ledger_engine.ledger.writer import sha256
from tests.fakes.memfs import Crash, MemFS
from tests.ledger.test_save_conformance import REPO, Divergence, corpus

pytestmark = [pytest.mark.inv("INV-32"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01")]

MODEL = REPO / "formal" / "tla" / "engine-jobs" / "EngineJobs.tla"

type State_ = dict[str, Any]

ABS: Final = 0
TORN: Final = -1
V0: Final = 1
BPOST: Final = 2
CUT: Final = b"; cut"  # a staging write a crash cut off (the model's TORN at a staging name)
ALD: Final = "/w"
SESSION: Final = "boot"
JOURNALS: Final = ("b2", "b3")  # the model's `JOrder` restricted to boot journals
KEY: Final = {"b2": "k2", "b3": "k3"}
FILES: Final = ("R", "J", "K")
PATH: Final = {"R": "/w/L/main.bean", "J": "/w/M/j.bean", "K": "/v/K/k.bean"}
FOLDER: Final = {f: p.rpartition("/")[0] for f, p in PATH.items()}
BASE: Final = {f: p.rpartition("/")[2] for f, p in PATH.items()}
FILE_OF: Final = {b: f for f, b in BASE.items()}
RECORD_ORDER: Final = {"b2": ("J", "R"), "b3": ("K",)}  # the model's `RecOrd`: the record lists the root last
ROOT_FIRST: Final = {"b2": ("R", "J"), "b3": ("K",)}  # the model's `ROrd`: the restore puts back root first
SLOTS: Final = (("b2", "R"), ("b2", "J"), ("b3", "K"))
K_AWAY: Final = "/v/K-away"  # K's folder while its disk is unreachable
L_MOVED: Final = "/w/L-moved"  # R's folder once a link stands in its place
L_COPY: Final = "/w/Lc"  # the link's target: a copy of R's folder
PER_FILE: Final = frozenset({"rs_w", "rs_x", "rs_c", "rs_t"})
SYNC_STEPS: Final = frozenset({"RC_Init", "RS_Sync", "RS_All"})
"""The model's sync steps: the listing's sync of each journal folder, the restore's syncs of every folder the record
names (before the first removal, and after the last put-back). The code may never pass one without a sync."""
FUSED_SYNC: Final = frozenset({"RS_Write", "RS_Swap", "RS_Resolve"})
"""Model steps that include their folder's sync (the restore temp's rename, the restore swap, the record's
`.resolved` rename): the code must make that sync before its next step."""


def name(j: str) -> str:
    return journal.record_name(SESSION, j)


def jdir(j: str) -> str:
    return f"{ALD}/local/{KEY[j]}/journal"


def bdir(j: str) -> str:
    return f"{ALD}/local/{KEY[j]}/backups/{name(j)}"


def content(f: str, v: int) -> bytes:
    assert v > 0, f"the model value {v} of {f} has no bytes"
    return f"; {f} {v}\n".encode()


def value(f: str, data: bytes | None) -> int:
    if data is None:
        return ABS
    found = re.fullmatch(rb"; ([RJK]) (\d+)\n", data)
    return int(found.group(2)) if found is not None and found.group(1).decode() == f else TORN


def worlds() -> list[str]:
    """The recover worlds of the corpus (their states carry `rc`)."""
    meta, _ = corpus()
    return sorted(w for w, c in meta["header"]["worlds"].items() if "recover" in c.get("jobs", []))


# -- the world ---------------------------------------------------------------------------------------------------------


@dataclass
class Place:
    """Where the fake holds what the model calls a file's folder now."""

    k_away: bool = False
    linked: bool = False
    copy: dict[str, tuple[object, ...]] = field(default_factory=lambda: dict[str, tuple[object, ...]]())

    def folder(self, f: str) -> str:
        return K_AWAY if f == "K" and self.k_away else FOLDER[f]


def scratch(f: str, j: str) -> tuple[str, str, str, str]:
    return journal.scratch_names(BASE[f], name(j))


def _write_value(fs: MemFS, path: str, f: str, v: int) -> None:
    if v == ABS:
        return
    fs.outside_write(path, CUT if v == TORN else content(f, v))


def build(st: State_, consts: Mapping[str, str]) -> tuple[MemFS, Place]:
    """The fake holding the model's first state (`Init`)."""
    rc = st["rc"]
    fs = MemFS()
    for f in FILES:
        fs.outside_mkdirs(FOLDER[f])
    fs.outside_mount("/v", 2)
    boot = set(re.findall(r'"(b\d)"', consts["BootJ"]))
    for f in FILES:
        fs.outside_write(PATH[f], content(f, rc["cur"][f]))
    for j, f in SLOTS:
        temp, staging, rtemp, _ = scratch(f, j)
        _write_value(fs, f"{FOLDER[f]}/{temp}", f, rc["tc"][f"{j}/{f}"])
        _write_value(fs, f"{FOLDER[f]}/{staging}", f, rc["sg"][f"{j}/{f}"])
        _write_value(fs, f"{FOLDER[f]}/{rtemp}", f, rc["rt"][f"{j}/{f}"])
    for j in JOURNALS:
        if j not in boot:
            continue
        fs.outside_mkdirs(jdir(j))
        fs.outside_mkdirs(bdir(j))
        if rc["jr"][j] == "topen":
            fs.outside_write(f"{jdir(j)}/{journal.file_name(name(j), State.OPEN)}", b"torn {")
            continue
        for i, f in enumerate(RECORD_ORDER[j]):
            fs.outside_write(f"{bdir(j)}/{journal.backup_name(i, BASE[f])}", content(f, V0))
        data = journal.encode(record(fs, j))
        fs.outside_write(f"{jdir(j)}/{journal.file_name(name(j), State.OPEN)}", data)
    place = Place()
    if consts["BootLink"] == "TRUE":
        put_link(fs, place)
    return fs, place


def record(fs: MemFS, j: str) -> Record:
    """The record a crashed save of the journal `j` left: every file replaced (pre `V0`, post `BPOST`), its backups,
    and the folders it wrote in, each with the drive marks a save records (S0089)."""
    files = tuple(
        FileEntry(
            canonical_path=PATH[f],
            path_key=core_paths.path_key(fs.resolve(PATH[f])),
            pre=sha256(content(f, V0)),
            post=sha256(content(f, BPOST)),
            backup=journal.backup_name(i, BASE[f]),
            temp=journal.temp_name(BASE[f], name(j)),
        )
        for i, f in enumerate(RECORD_ORDER[j])
    )
    folders: list[FolderEntry] = []
    for p in sorted(needed_folders(files, ())):
        with fs.open_dir(p) as h:
            mark = h.mark()
        volume = drive_of(mark)
        assert volume is not None
        folders.append(FolderEntry(p, volume, mark.fs_type, known_birth(mark)))
    return Record(
        session=SESSION,
        job=j,
        workspace_key=KEY[j],
        lineage=f"lin{j}",
        engine_build="b",
        backup_dir=bdir(j),
        files=files,
        created_dirs=(),
        folders=tuple(folders),
    )


def put_link(fs: MemFS, place: Place) -> None:
    """R's folder replaced by a link to a copy of it (the model's path changer)."""
    fs.outside_move(FOLDER["R"], L_MOVED)
    fs.outside_mkdirs(L_COPY)
    for n in fs.names_in(L_MOVED):
        data = fs.file(f"{L_MOVED}/{n}")
        assert data is not None
        fs.outside_write(f"{L_COPY}/{n}", data)
    fs.outside_symlink(FOLDER["R"], L_COPY)
    place.linked = True
    place.copy = _tree(fs, L_COPY)


def _tree(fs: MemFS, folder: str) -> dict[str, tuple[object, ...]]:
    return {p: v for p, v in fs.snapshot().items() if p.startswith(folder + "/")}


def unreach(fs: MemFS, place: Place) -> None:
    """K's disk becomes unreachable, or answers again (the model's `Unreach`)."""
    if place.k_away:
        fs.outside_move(K_AWAY, FOLDER["K"])
    else:
        fs.outside_move(FOLDER["K"], K_AWAY)
    place.k_away = not place.k_away


def edit(fs: MemFS, f: str, v: int) -> None:
    fs.outside_write(PATH[f], content(f, v))


# -- the views ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    files: tuple[int, ...]
    journals: tuple[str, ...]
    temps: tuple[int, ...]  # per slot: its temp, else its staging file
    rtemps: tuple[int, ...]
    kept: tuple[tuple[int, ...], ...]


def model_view(st: State_) -> View:
    rc = st["rc"]

    def jstate(j: str) -> str:
        return "open" if rc["jr"][j] == "topen" else rc["jr"][j]

    return View(
        tuple(rc["cur"][f] for f in FILES),
        tuple(jstate(j) for j in JOURNALS),
        tuple(rc["tc"][f"{j}/{f}"] or rc["sg"][f"{j}/{f}"] for j, f in SLOTS),
        tuple(rc["rt"][f"{j}/{f}"] for j, f in SLOTS),
        tuple(tuple(sorted(rc["kp"][f"{j}/{f}"])) for j, f in SLOTS),
    )


def journal_state(fs: MemFS, j: str) -> str:
    try:
        names = fs.names_in(jdir(j))
    except AssertionError:
        return "none"
    states = sorted(n.rpartition(".")[2] for n in names if n.startswith(name(j) + "."))
    return "+".join(states) if states else "none"


def _at(fs: MemFS, folder: str, n: str, f: str) -> int:
    return value(f, fs.file(f"{folder}/{n}"))


def _kept(fs: MemFS, folder: str, f: str, j: str) -> tuple[int, ...]:
    head = f"{BASE[f]}.kept-{name(j)}"
    names = [n for n in fs.names_in(folder) if n == head or n.startswith(head + "-")]
    return tuple(sorted(value(f, fs.file(f"{folder}/{n}")) for n in names))


def code_view(fs: MemFS, place: Place) -> View:
    def scratch_value(j: str, f: str) -> int:
        temp, staging, _, _ = scratch(f, j)
        folder = place.folder(f)
        there = set(fs.names_in(folder))
        return _at(fs, folder, temp, f) if temp in there else _at(fs, folder, staging, f)

    return View(
        tuple(_at(fs, place.folder(f), BASE[f], f) for f in FILES),
        tuple(journal_state(fs, j) for j in JOURNALS),
        tuple(scratch_value(j, f) for j, f in SLOTS),
        tuple(_at(fs, place.folder(f), scratch(f, j)[2], f) for j, f in SLOTS),
        tuple(_kept(fs, place.folder(f), f, j) for j, f in SLOTS),
    )


# -- a segment of a run: the listing, or one journal's try -------------------------------------------------------------


@dataclass
class Event:
    kind: str  # "edit", "unreach", "kill", "io"
    file: str = ""
    value: int = 0
    app: bool = False  # a kill: the app's crash (the run ends), not the engine's


@dataclass
class Point:
    """The model at one step of the segment: the state just before it (after its events), the events made there, and
    the step that leaves it."""

    pc: str
    file: str
    last: State_
    events: list[Event] = field(default_factory=lambda: list[Event]())
    step: str = ""
    after: State_ | None = None

    def passable(self) -> bool:
        return self.after is None or model_view(self.last) == model_view(self.after)


def point_of(st: State_) -> Point:
    pc = st["ctl"]["pc"]
    rc = st["rc"]
    file: str = ROOT_FIRST[str(rc["rj"])][int(rc["fi"]) - 1] if pc in PER_FILE and rc["rj"] != "none" else ""
    return Point(pc, file, st)


ENV: Final = frozenset({"Editor", "Unreach", "EngineCrash", "AppCrash", "IOFail"})


def segment(states: list[State_], i: int, inside: Callable[[State_], bool]) -> tuple[list[Point], int]:
    """The points of the segment that starts at `states[i]` and goes on while `inside` holds (the states it
    covers, and the step that leaves it); the index of the first state past it. A kill ends it."""
    points = [point_of(states[i])]
    j = i
    while j + 1 < len(states):
        a, b = states[j], states[j + 1]
        act = b["act"]["a"]
        p = points[-1]
        p.last = a
        if act == "Editor":
            p.events.append(Event("edit", str(b["act"]["p"]), b["rc"]["cur"][b["act"]["p"]]))
            p.last = b
        elif act == "Unreach":
            p.events.append(Event("unreach"))
            p.last = b
        elif act in ("EngineCrash", "AppCrash"):
            p.events.append(Event("kill", app=act == "AppCrash"))
            p.step, p.after = act, b
            return points, j + 1
        elif act == "IOFail":
            p.events.append(Event("io"))
            p.step, p.after = act, b
            if not inside(b):
                return points, j + 1
            points.append(point_of(b))
        else:
            p.step, p.after = act, b
            if not inside(b):
                return points, j + 1
            points.append(point_of(b))
        j += 1
    return points, len(states)


class Cut(Exception):
    """The walk ended inside the segment (not a difference)."""


class Driver:
    """Walks the model's points of one segment as the code reaches its hooks (the module's docstring)."""

    def __init__(self, fs: MemFS, place: Place, points: list[Point], where: str, j: str) -> None:
        self.fs, self.place, self.points, self.where, self.j = fs, place, points, where, j
        self.at = -1
        self.landed = False
        self.last_pc = ""
        self.kept_pc = ""
        self.ops: set[str] = set()
        self.synced_first: set[str] = set()  # folders the try's first round of syncs has synced (`RS_Sync`)
        self.wrote = False  # the try removed, made or swapped something (its first round of syncs is over)
        self.compared = 0
        self.walked: Counter[str] = Counter()
        self.killed = False
        self.app_crashed = False
        self.edits: list[int] = []  # the outside editor's bytes of R written, in order

    def key(self, i: int) -> tuple[str, str]:
        p = self.points[i]
        return p.pc, p.file

    def compare(self, i: int) -> None:
        p = self.points[i]
        want, got = model_view(p.last), code_view(self.fs, self.place)
        if want != got:
            msg = (
                f"{self.where}: at the model's {p.pc} {p.file}, the fake holds\n  {got}\nwhere the model has\n  {want}"
            )
            raise Divergence(msg)
        self.compared += 1

    def outside(self, e: Event) -> None:
        if e.kind == "edit":
            edit(self.fs, e.file, e.value)
            self.edits.append(e.value)
        elif e.kind == "unreach":
            unreach(self.fs, self.place)

    def start(self) -> None:
        """The outside acts made at the segment's first point, before its first step: written before the code starts
        (the code reads before its first effect, so they are what its first look sees). A death or an I/O error there
        stays for the code's first hook."""
        p = self.points[0]
        for e in [e for e in p.events if e.kind in ("edit", "unreach")]:
            self.outside(e)
        p.events = [e for e in p.events if e.kind not in ("edit", "unreach")]

    def reach(self, pc: str, file: str = "") -> None:
        """The code is at the model step `pc` (of `file`): walk the model up to it (as `test_save_conformance`)."""
        self.last_pc = pc
        if self.at >= 0 and self.key(self.at) == (pc, file):
            return
        if self.landed:
            self.synced(pc, file)
            self.walked[self.points[self.at].step] += 1
        self.landed = False
        self.ops = set()
        for i in range(self.at + 1, len(self.points)):
            p = self.points[i]
            self.at = i
            for e in p.events:
                if e.kind == "kill":
                    self.compare(i)
                    self.killed, self.app_crashed = True, e.app
                    msg = f"the model's {'app' if e.app else 'engine'} ended at {p.pc}"
                    raise Crash(msg)
                if e.kind == "io":
                    self.compare(i)
                    self.walked["IOFail"] += 1
                    raise OSError(errno.EIO, f"the model's I/O error at {p.pc}")
                self.outside(e)
            if self.key(i) == (pc, file):
                self.compare(i)
                self.landed = True
                return
            if p.step in SYNC_STEPS:
                msg = f"{self.where}: the code is at {pc} {file}, past the model's {p.step} {p.file} without its sync"
                raise Divergence(msg)
            if not p.passable():
                msg = (
                    f"{self.where}: the code is at {pc} {file}, past the model's {p.pc} {p.file} ({p.step}), which "
                    "changes the disk"
                )
                raise Divergence(msg)
            if p.after is None:
                raise Cut
        msg = f"{self.where}: the code is at {pc} {file}, which the model's {self.j or 'listing'} does not reach"
        raise Divergence(msg)

    def synced(self, pc: str, file: str) -> None:
        p = self.points[self.at]
        if p.step in FUSED_SYNC and "sync" not in self.ops:
            msg = f"{self.where}: the code is at {pc} {file}, past the model's {p.step} {p.file} without its sync"
            raise Divergence(msg)

    def finish(self, answered: bool) -> bool:
        """The code has ended (`answered`: it returned). Every model step left must be one it carries out without an
        effect; the outside acts made there are written. True when the model's engine (or app) ended in a step left
        before any step that changes the disk: the code's answer is then lost to the sidecar (a death after its last
        effect and before its answer is read). The step the code answered at counts as walked."""
        if answered and self.landed:
            self.synced("its answer", "")
            self.walked[self.points[self.at].step] += 1
        self.landed = False
        for i in range(self.at + 1, len(self.points)):
            p = self.points[i]
            for e in p.events:
                if e.kind == "kill":
                    self.killed, self.app_crashed = True, e.app
                    return True
                if e.kind == "io":
                    msg = f"{self.where}: the model's I/O error at {p.pc}, where the code makes no effect"
                    raise Divergence(msg)
                self.outside(e)
            if p.step and not p.passable():
                msg = f"{self.where}: the code ended before the model's {p.pc} ({p.step}), which changes the disk"
                raise Divergence(msg)
            if answered and p.step in SYNC_STEPS:
                msg = f"{self.where}: the code answered before the model's {p.step} {p.file}, without its sync"
                raise Divergence(msg)
            if p.after is None:
                raise Cut
        return False

    # -- the effects, by the model step they belong to ------------------------------------------------------------

    def classify(self, label: str) -> tuple[str, str]:
        op, _, rest = label.partition(" ")
        folder, _, names_ = rest.partition(":")
        names = names_.split(",")
        if not self.j:  # the listing
            if op == "sync" and folder in {jdir(j) for j in JOURNALS}:
                return "rc_init", ""
            msg = f"{self.where}: a listing effect the replay cannot place: {label}"
            raise Divergence(msg)
        rec = name(self.j)
        if folder == jdir(self.j):
            if op == "rename" and names == [f"{rec}.open", f"{rec}.resolved"]:
                return "rs_res", ""
            if op == "sync" and self.last_pc == "rs_res":
                return "rs_res", ""
        if folder in {FOLDER[f] for f in RECORD_ORDER[self.j]} | {L_MOVED, K_AWAY}:  # a handle opened before the
            # folder moved keeps writing there: the model's unreachable disk stops only the check (`Stops`), not a try
            # past it
            return self._ledger(op, names, folder)
        msg = f"{self.where}: an effect the replay cannot place: {label}"
        raise Divergence(msg)

    def _ledger(self, op: str, names: list[str], folder: str) -> tuple[str, str]:
        rec = name(self.j)
        last = self.last_pc
        if op == "sync":
            if self.kept_pc:  # the folder's sync after a move to a kept name: the same step
                pc, self.kept_pc = self.kept_pc, ""
                return pc, self.key(self.at)[1] if pc in PER_FILE else ""
            if last in ("rs_w", "rs_x") and "sync" not in self.ops:
                return last, self.key(self.at)[1]
            if not self.wrote and folder not in self.synced_first:
                self.synced_first.add(folder)
                return "rs_sync", ""
            return "rs_all", ""
        self.wrote = True
        found = re.fullmatch(rf"\.(.+)\.ft-(restore-)?{re.escape(rec)}\.(new|tmp)", names[0])
        f = FILE_OF.get(found.group(1), "?") if found is not None else ""
        if len(names) > 1 and f".kept-{rec}" in names[1]:  # an outside save moved to its kept name
            pc = {"rs_x": "rs_c", "rs_c": "rs_t"}.get(last, "rs_chk")
            self.kept_pc = pc
            kf = FILE_OF.get(names[1].partition(".kept-")[0], "?")
            return pc, kf if pc in PER_FILE else ""
        if found is not None and found.group(2):  # a restore name
            if op == "swap":
                return "rs_x", f
            if op == "remove" and last == "rs_x":
                return "rs_c", f
            if op in ("create", "write", "fsync", "rename"):
                return "rs_w", f
        if op == "remove" and found is not None and last in ("rs_c", "rs_x") and found.group(3) == "tmp":
            return "rs_t", f
        if op == "remove":
            return "rs_clean", ""
        msg = f"{self.where}: a restore effect the replay cannot place: {op} {names} at {last}"
        raise Divergence(msg)

    def on_effect(self, i: int, label: str) -> None:
        del i
        pc, f = self.classify(label)
        self.reach(pc, f)
        self.ops.add(label.partition(" ")[0])


# -- the replay --------------------------------------------------------------------------------------------------------


@dataclass
class Tally:
    runs: int = 0
    tries: int = 0
    compared: int = 0
    walked: Counter[str] = field(default_factory=lambda: Counter[str]())
    branches: Counter[str] = field(default_factory=lambda: Counter[str]())
    stops: Counter[str] = field(default_factory=lambda: Counter[str]())


STOPS: Mapping[str, str] = {
    "cut": "the walk ended inside a run or a try (not a difference)",
}


class StopTrace(Exception):
    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


def outcome_of(out: rs.RestoreOutcome | None) -> str:
    match out:
        case None:
            return "failed"  # the sidecar: the engine died (`engine_stopped`)
        case rs.Resolved():
            return "resolved"
        case rs.Stopped():
            return "stopped"
        case rs.Failed():
            return "failed"


def model_outcome(st: State_, j: str) -> str:
    rc = st["rc"]
    return "resolved" if rc["jr"][j] == "resolved" else rc["why"][j]


class Replay:
    def __init__(self, trace: State_, consts: Mapping[str, str], tally: Tally) -> None:
        self.states: list[State_] = trace["states"]
        self.where0 = f"{trace['world']} trace {trace['trace']}"
        self.fs, self.place = build(self.states[0], consts)
        self.tally = tally
        self.edited: int | None = None  # the outside editor's latest bytes of R
        self.listing: ra.Listing | None = None
        self.queue: list[str] = []

    def check_edit_kept(self, fs: MemFS, where: str) -> None:
        """`ExternalEditNeverSilentlyLost`: the editor's latest bytes of R stand at R, at a name of ours beside it or
        in one of its kept copies."""
        if self.edited is None:
            return
        folder = L_COPY if self.place.linked else FOLDER["R"]
        held = {value("R", fs.file(f"{folder}/{n}")) for n in fs.names_in(folder)}
        assert self.edited in held, f"{where}: the outside edit {self.edited} of R is gone: {sorted(held)}"

    def note_edits(self, driver: Driver) -> None:
        if driver.edits:
            self.edited = driver.edits[-1]

    def check_link(self, where: str) -> None:
        """`RecoveryWritesOnlyRecordedPaths`: nothing written through R's link."""
        if self.place.linked:
            assert _tree(self.fs, L_COPY) == self.place.copy, f"{where}: something was written through R's link"

    def run(self) -> None:
        i = 0
        while i < len(self.states) - 1:
            st = self.states[i]
            if st["app"] == "recover":
                i = self.recover_all(i)
                continue
            nxt = self.states[i + 1]
            act = nxt["act"]["a"]
            where = f"{self.where0}, step {i + 1} ({act})"
            if act == "Editor":
                edit(self.fs, "R", nxt["rc"]["cur"]["R"])
                self.edited = nxt["rc"]["cur"]["R"]
            elif act == "Unreach":
                unreach(self.fs, self.place)
            elif act == "PathChange":
                put_link(self.fs, self.place)
                self.tally.branches["the path changer: a link after the app's crash"] += 1
            elif act == "Keep":
                self.keep(str(nxt["act"]["p"]), nxt, where)
            elif act == "StartRecover":
                self.tally.branches["Retry: recover-all again"] += 1
            elif act == "Relaunch":
                self.tally.branches["the next start after the app's crash"] += 1
            assert act in {"Editor", "Unreach", "PathChange", "Keep", "StartRecover", "Relaunch", "AppCrash"}, act
            want, got = model_view(nxt), code_view(self.fs, self.place)
            assert want == got, f"{where}: the fake holds\n  {got}\nwhere the model has\n  {want}"
            i += 1

    def keep(self, j: str, after: State_, where: str) -> None:
        out = ra.keep(self.fs, jdir(j), name(j))  # the model's Keep is never over R's link since S0100 (BL-0224)
        assert isinstance(out, ra.Done), f"{where}: Keep answered {out}, the model kept {j}"
        assert after["rc"]["jr"][j] == "kept"
        self.tally.walked["Keep"] += 1
        torn = self.states[0]["rc"]["jr"][j] == "topen"
        self.tally.branches["Keep of a record that cannot be read" if torn else "Keep"] += 1
        for s in self.fs.power_loss_states():  # `KeptFilesDurable`: what Keep released is what a power loss keeps
            assert journal_state(s, j) == "kept", f"{where}: a power loss takes Keep back"
            assert code_view(s, self.place).files == code_view(self.fs, self.place).files, where

    def recover_all(self, i: int) -> int:
        """One run of recover-all from `states[i]` (its start); the index of the first state past it."""
        self.tally.runs += 1
        tried: list[str] = []
        while i < len(self.states) - 1:
            st = self.states[i]
            if st["app"] != "recover":
                break
            pc, rj = st["ctl"]["pc"], st["rc"]["rj"]
            if pc == "rc_init":
                i = self.listing_segment(i)
            elif rj != "none":
                i = self.try_segment(i, rj, tried)
            else:
                i = self.between(i, tried)
        return i

    def listing_segment(self, i: int) -> int:
        points, nxt = segment(self.states, i, lambda s: s["app"] == "recover" and s["ctl"]["pc"] == "rc_init")
        where = f"{self.where0}, the listing at step {i}"
        driver = Driver(self.fs, self.place, points, where, "")
        driver.start()
        self.fs.on_effect = driver.on_effect
        listing: ra.Listing | None = None
        try:
            listing = ra.list_journals(self.fs, ALD)
        except Crash:
            assert driver.killed, f"{where}: the code's engine died where the model's did not"
        except Cut:
            raise StopTrace("cut") from None
        finally:
            self.fs.on_effect = None
        try:
            driver.finish(answered=listing is not None)
        except Cut:
            raise StopTrace("cut") from None
        self.note_edits(driver)
        self.tally.compared += driver.compared
        self.tally.walked.update(driver.walked)
        if driver.killed:
            if not driver.app_crashed:
                self.tally.branches["an engine death in the listing: listed again"] += 1
            return nxt
        assert listing is not None
        after = self.states[nxt]
        assert not listing.unlisted, f"{where}: {listing.unlisted}"
        found = {j.name: j.record for j in listing.journals}
        want = {name(j): after["rc"]["jr"][j] for j in JOURNALS if after["rc"]["jr"][j] in ("open", "topen")}
        assert set(found) == set(want), f"{where}: the code lists {sorted(found)}, the model {want}"
        for n, record_ in found.items():
            assert (record_ is None) == (want[n] == "topen"), f"{where}: {n} read {record_}, the model {want[n]}"
        self.listing = listing
        self.queue = [j.name for j in listing.journals]
        return nxt

    def between(self, i: int, tried: list[str]) -> int:
        """A step of the run between two tries: the model picks the next journal, marks a torn one, or ends; an
        outside act or a death between two tries."""
        st, nxt = self.states[i], self.states[i + 1]
        act = nxt["act"]["a"]
        where = f"{self.where0}, step {i + 1} ({act})"
        if act == "Editor":
            edit(self.fs, "R", nxt["rc"]["cur"]["R"])
            self.edited = nxt["rc"]["cur"]["R"]
        elif act == "Unreach":
            unreach(self.fs, self.place)
        elif act == "RC_Next":
            left = [n for n in self.queue if n not in tried]
            if nxt["app"] != "recover":
                assert not left, f"{where}: the model ends the run with {left} untried"
            elif nxt["rc"]["rj"] != "none":  # a try
                j = nxt["rc"]["rj"]
                assert left[:1] == [name(j)], f"{where}: the sidecar tries {left[:1]}, the model {j}"
            else:  # a journal whose record cannot be read: stopped without a try
                j = next(x for x in JOURNALS if st["rc"]["why"][x] == "" and nxt["rc"]["why"][x] == "stopped")
                assert left[:1] == [name(j)], f"{where}: the sidecar takes {left[:1]}, the model {j}"
                assert self.listing is not None
                listed = next(x for x in self.listing.journals if x.name == name(j))
                assert listed.record is None, f"{where}: {j}'s record was read, the model's is torn"
                tried.append(name(j))
                self.tally.branches["a record that cannot be read blocks without a try"] += 1
            self.tally.walked["RC_Next"] += 1
        else:
            assert act in ("EngineCrash", "AppCrash"), f"{where}: {act}"
        want, got = model_view(nxt), code_view(self.fs, self.place)
        assert want == got, f"{where}: the fake holds\n  {got}\nwhere the model has\n  {want}"
        return i + 1

    def try_segment(self, i: int, j: str, tried: list[str]) -> int:
        """One journal's try (`restore.recover_journal`), from the model's `rs_chk` to the step that ends it."""
        points, nxt = segment(self.states, i, lambda s: s["app"] == "recover" and s["rc"]["rj"] == j)
        where = f"{self.where0}, the try of {j} at step {i}"
        driver = Driver(self.fs, self.place, points, where, j)
        driver.start()
        self.fs.on_effect = driver.on_effect
        out: rs.RestoreOutcome | None = None
        try:
            out = rs.recover_journal(self.fs, jdir(j), name(j))
        except Crash:
            assert driver.killed, f"{where}: the code's engine died where the model's did not"
        except Cut:
            raise StopTrace("cut") from None
        finally:
            self.fs.on_effect = None
        try:
            lost = driver.finish(answered=out is not None)
        except Cut:
            raise StopTrace("cut") from None
        self.note_edits(driver)
        tried.append(name(j))
        self.tally.tries += 1
        self.tally.compared += driver.compared
        after = self.states[nxt]
        got = "failed" if lost else outcome_of(out)
        if driver.app_crashed:
            self.tally.branches["the app's crash during a try"] += 1
        else:
            want = model_outcome(after, j)
            assert got == want, f"{where}: the code's try is {got} ({out}), the model's {want}"
            if not driver.killed and out is not None:
                last = next((p for p in reversed(points) if p.step), None)
                if last is not None and not driver.walked:
                    self.tally.walked[last.step] += 1  # the code's answer is the model's step that ends the try
        self.tally.walked.update(driver.walked)
        want_v, got_v = model_view(after), code_view(self.fs, self.place)
        assert want_v == got_v, f"{where}: after the try the fake holds\n  {got_v}\nwhere the model has\n  {want_v}"
        self.check_edit_kept(self.fs, where)
        self.check_link(where)
        self.ends(j, out, driver, where)
        return nxt

    def ends(self, j: str, out: rs.RestoreOutcome | None, driver: Driver, where: str) -> None:
        """The durable layer after a try, and the branches it walked."""
        for s in self.fs.power_loss_states():
            self.check_edit_kept(s, f"{where} (after a power loss)")
            if isinstance(out, rs.Resolved):  # `NoTornLedgerAfterRecovery`'s `ResolveDurable`
                assert journal_state(s, j) == "resolved", f"{where}: a power loss takes `.resolved` back"
                for f in RECORD_ORDER[j]:
                    v = value(f, s.file(PATH[f]))
                    assert v not in (BPOST, TORN), f"{where}: after a power loss {f} holds {v} under `.resolved`"
        b = self.tally.branches
        if driver.killed and not driver.app_crashed:
            b["an engine death marks the journal in hand failed"] += 1
        if driver.walked["IOFail"]:
            b["an I/O error fails the try"] += 1
        if isinstance(out, rs.Resolved):
            b["resolved"] += 1
        if isinstance(out, rs.Stopped):
            b[f"stopped: {out.cause.value}"] += 1
        if driver.walked["RS_Swap"]:
            b["pre bytes put back"] += 1
        if driver.walked["RS_Clean"]:
            b["names of ours removed (a cut staging write, a temp)"] += 1
        if driver.walked["RS_Temp"]:
            b["the old object at the temp name removed after the put-back"] += 1


BRANCHES: tuple[str, ...] = (
    "resolved",
    "pre bytes put back",
    "names of ours removed (a cut staging write, a temp)",
    "the old object at the temp name removed after the put-back",
    f"stopped: {Cause.FOREIGN.value}",
    f"stopped: {Cause.UNREACHABLE.value}",
    f"stopped: {Cause.PATH_MOVED.value}",
    f"stopped: {Cause.DISPLACED.value}",
    f"stopped: {Cause.KEPT_COPY.value}",
    "a record that cannot be read blocks without a try",
    "an engine death marks the journal in hand failed",
    "an engine death in the listing: listed again",
    "an I/O error fails the try",
    "the app's crash during a try",
    "Keep",
    "Keep of a record that cannot be read",
    "Retry: recover-all again",
    "the next start after the app's crash",
    "the path changer: a link after the app's crash",
)
"""The branches the corpus must walk through the code."""


def replay_trace(trace: State_, consts: Mapping[str, str], tally: Tally) -> None:
    replay = Replay(trace, consts, tally)
    try:
        replay.run()
    except StopTrace as e:
        tally.stops[e.why] += 1


def run_world(world_name: str) -> Tally:
    meta, traces = corpus()
    tally = Tally()
    for trace in traces:
        if trace["world"] == world_name:
            replay_trace(trace, meta["constants"][world_name], tally)
    return tally


@cache
def every_world() -> Tally:
    tally = Tally()
    for w in worlds():
        t = run_world(w)
        tally.runs += t.runs
        tally.tries += t.tries
        tally.compared += t.compared
        tally.walked.update(t.walked)
        tally.branches.update(t.branches)
        tally.stops.update(t.stops)
    return tally


# -- the tests ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("world_name", worlds())
def test_recover_all_follows_the_model(world_name: str) -> None:
    run_world(world_name)


WALKED: tuple[str, ...] = (
    "RC_Init", "RC_Next", "RS_Check", "RS_Sync", "RS_Clean", "RS_Write", "RS_Swap", "RS_Compare", "RS_Temp", "RS_All",
    "RS_Resolve", "IOFail", "Keep",
)  # fmt: skip
"""The model's recover-all steps the replay walks through the code: the code reached the step at one of its hooks and
went on, the I/O error raised at a hook, the code's answer that is the step ending the try (`RS_Check`'s stops), the
sidecar's choice of the next journal checked against the code's listing (`RC_Next`), or the code's Keep. Each engine
step is marked `@tla_action` (`backend/tests/architecture/test_tla_mapping.py`)."""

NOT_WALKED: Mapping[str, str] = {
    "RS_Late": "a seeded bug's step only (`resolve_before_restores_synced`): the folders' sync after the resolve",
}


def test_the_replay_walks_recover_all() -> None:
    """Every step of `WALKED` is walked through the code at least once, and every branch of `BRANCHES`."""
    tally = every_world()
    missing_steps = [a for a in WALKED if tally.walked[a] == 0]
    assert not missing_steps, f"the replay never walks {missing_steps}: widen the recover worlds in gen_traces.py"
    assert set(tally.walked) <= set(WALKED), dict(tally.walked)
    missing = [b for b in BRANCHES if tally.branches[b] == 0]
    assert not missing, f"the replay never walks {missing}: widen the recover worlds in gen_traces.py"
    assert tally.tries >= 50, tally


def test_the_recover_steps_are_the_models() -> None:
    """`WALKED` and `NOT_WALKED` together are the model's recover-all steps (`RC_*`, `RS_*`, `IOFail` and Keep)."""
    model = MODEL.read_text(encoding="utf-8")
    steps = set(re.findall(r"^((?:RC|RS)_[A-Za-z0-9]+) ==", model, re.MULTILINE)) | {"IOFail", "Keep"}
    assert not set(WALKED) & set(NOT_WALKED)
    assert set(WALKED) | set(NOT_WALKED) == steps


STOP_COUNTS: Mapping[str, int] = {"cut": 0}
"""The traces the replay does not follow to their end, by reason (`STOPS`); pinned."""


def test_the_stops_are_counted() -> None:
    tally = every_world()
    assert set(tally.stops) <= set(STOPS), tally.stops
    assert {k: tally.stops[k] for k in STOP_COUNTS} == STOP_COUNTS, dict(tally.stops)


def _returns[T](value: T) -> Callable[..., T]:
    def stand_in(*args: object) -> T:
        del args
        return value

    return stand_in


def _nothing(*args: object) -> None:
    del args


def _first_failure() -> str:
    every_world.cache_clear()
    try:
        every_world()
    except AssertionError as e:
        return str(e)
    finally:
        every_world.cache_clear()
    return ""


# The replay is not vacuous: each seeded divergence below is caught, with its own message. Must not pass: with the
# comparison that catches it disabled, the replay passes or fails with another message.


def test_a_listing_without_its_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RC_Init`'s sync (model finding F1): a listing that reads a journal folder without syncing it first."""
    from ledger_engine.ledger import handles

    at = handles.Handles.at

    def unsynced(self: handles.Handles, folder: str) -> Any:
        h = at(self, folder)
        if folder.endswith("/journal"):
            h.sync = lambda: None
        return h

    monkeypatch.setattr(handles.Handles, "at", unsynced)
    assert re.search(r"(past|before) the model's RC_Init .*without its sync", _first_failure())


def test_a_skipped_restore_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RS_Sync` and `RS_All`: a restore that syncs no folder of the record."""
    monkeypatch.setattr(rs, "_sync_folders", _nothing)
    assert re.search(r"the model's RS_(Sync|All) .*without its sync", _first_failure())


def test_a_swap_without_its_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RS_Swap` includes its folder's sync (`FUSED_SYNC`)."""
    put_back = rs._put_back  # pyright: ignore[reportPrivateUsage]

    def unsynced(handles: Any, record_: Any, x: Any) -> None:
        h = handles.at(x.parent)
        swap, sync = h.swap, h.sync
        swapped = [False]

        def swap_once(a: str, b: str) -> None:
            swap(a, b)
            swapped[0] = True

        def sync_unless_swapped() -> None:
            if not swapped[0]:
                sync()
            swapped[0] = False

        h.swap, h.sync = swap_once, sync_unless_swapped
        try:
            put_back(handles, record_, x)
        finally:
            del h.swap, h.sync

    monkeypatch.setattr(rs, "_put_back", unsynced)
    assert re.search(r"past the model's RS_Swap [RJK] without its sync", _first_failure())


def test_a_resolve_before_the_put_back_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A try that resolves without putting anything back (`RS_Write`, `RS_Swap` skipped)."""
    monkeypatch.setattr(rs, "_put_back", _nothing)
    assert re.search(r"past the model's rs_w [RJK] \(RS_Write\), which changes the disk", _first_failure())


def test_an_outside_save_removed_at_the_swap_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RS_Compare`: what a restore swap displaced always taken for ours and removed (the model's
    `no_displaced_check`)."""
    from ledger_engine.ledger import handles

    monkeypatch.setattr(rs, "displaced", _returns(handles.Displaced.OURS))
    # S0100 (BL-0225 F5): the try goes on writing J where the model's stops at the displaced outside save
    assert re.search(r"the code is at rs_w J, which the model's b2 does not reach", _first_failure())


def test_a_kept_copy_ignored_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RS_Check`'s kept-copy stop: a try that goes on while a kept copy of the journal stands beside a file."""
    monkeypatch.setattr(journal, "is_kept_name", _returns(False))
    # S0100 (BL-0225 F5): the try goes on to its syncs where the model's stops at the kept copy
    assert re.search(r"the code is at rs_sync , which the model's b2 does not reach", _first_failure())


def test_an_io_error_swallowed_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`IOFail`: a restore that goes on past an I/O error at a folder's sync (and resolves) where the model's try
    fails."""
    sync_folders = rs._sync_folders  # pyright: ignore[reportPrivateUsage]

    def swallowed(handles: Any, folders: list[str]) -> None:
        with contextlib.suppress(OSError):
            sync_folders(handles, folders)

    monkeypatch.setattr(rs, "_sync_folders", swallowed)
    # S0100 (BL-0225 F5): in the world with an I/O error, the try goes on writing R where the model's has failed
    assert re.search(
        r"S_recover_temps trace \d+, .*the code is at rs_w R, which the model's b2 does not reach", _first_failure()
    )


def test_the_disk_outcome_words_are_the_models() -> None:
    """The model's reasons are `stopped` and `failed`, and a try ends `resolved`; the code's three outcomes map onto
    them one to one (`outcome_of`)."""
    model = MODEL.read_text(encoding="utf-8")
    assert 'RestoreStop("stopped")' in model
    assert 'RestoreStop("failed")' in model
    assert '![RJn].st = "resolved"' in model
    assert {outcome_of(rs.Resolved("x")), outcome_of(rs.Stopped(Cause.FOREIGN, None))} == {"resolved", "stopped"}
    assert outcome_of(rs.Failed(Cause.IO_ERROR, None)) == outcome_of(None) == "failed"


def test_a_model_value_round_trips() -> None:
    for f in FILES:
        for v in (1, 2, 11, 21):
            assert value(f, content(f, v)) == v
        assert value(f, None) == ABS
        assert value(f, CUT) == TORN
    assert value("R", content("J", 1)) == TORN
