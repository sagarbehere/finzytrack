"""The model's save path replayed through the engine's save (S0064, BL-0151 slice 5; `design/specs/engine-jobs.md`
§6.1, §6.4, §6.6, §13; ADR-0064; EP-19).

`formal/tla/engine-jobs/traces/corpus.json` holds TLC-simulated traces of `EngineJobs.tla` (`gen_traces.py`). Each
save a trace runs is replayed here through `ledger.save.run` over the in-memory file system (`tests/fakes/memfs.py`),
with the model's world 1 projected onto it: the model's `R` is the root `/w/L/main.bean` (replaced), `N` the file it
includes `/w/L/prices.bean` (created by the first save, replaced by a later one), `I` the include `/w/L/inc.bean`
(in the ledger set, never written); a model value `v` of file `f` is the bytes `; f v`. The plan is the model's
(`ctl.plan`), in the model's order (`WOrd`: children first, the root last).

The code runs one save in one call; the replay sees it at its **hooks**: each effect on the file system
(`MemFS.on_effect`, classified to the model step it belongs to, `Driver.classify`), the ledger-set lock (step 3),
the observations and the load (steps 4-6), `job.gate()` (step 7) and `job.committed()` (step 9). At each hook the
replay walks the model's save up to the step the code is at: the model's outside edits made there are written to
the fake (as an editor would, durably), an engine death is raised there (`Crash`), an I/O error is raised there
(`OSError`, before the effect), and the fake is compared with the model's state just before the model's step
(`view`): the three files, this save's journal record, its temps (the save's and the restore's) and its backup. A
model step the code carries out without an effect (the displaced check, the read-back, the save's own R4 check when
none is owed) is passed only when it changed nothing the view shows; the save's syncs (`SYNC_STEPS`) are never passed:
each must be one of the code's effects. A step counts as walked only when the code carried it out at one of its hooks
(`WALKED`; the rest, with their reasons, `NOT_WALKED`). After the save, what the code answered is
compared with what the model told (`gh.told`): `Saved` is "saved", `Refused` "notsaved", a `NotSaved` before the
journal is `.open` is the re-hash ("unchanged" or "changed"), one after it (the rollback resolved) "notsaved" for an
I/O error and "changed" otherwise, `Unfinished` "unfinished"; and after an engine death (or our end of the engine
by a Cancel), the engine's reading of the disk (`save.outcome`, §6.6) told as the model's `DiskOutcome` tells it
(`DISK`; the sidecar's decision from those facts is checked against the same table in
`backend/tests/platform/test_engine_jobs_conformance.py`).

**Where the model and the code differ** (BL-0161: the model is updated at the M1a closing sweep, not in this slice),
the replay stops that trace there, counted by reason in `STOPS`, and never compares past it:
- the model's displaced checks swap other bytes back (`S_Compare`, `RS_Compare`); the code moves them to a kept name
  and stops (§6.4, the kept-name design of 2026-10-29);
- the model's rollback removes a file the save created, and its temp rule passes or stops by the old rule
  (`RB_Check`); the code never touches anything at a created path and stops `created_path_taken` (ADR-0012
  amendment of 2026-10-30, round 5), with the staging-name temp rule;
- the model runs an owed R4 inside the save (`S_R4Check`); the code refuses `check_pending` until R4 exists (BL-0152);
- the model's gate refuses a stale mirror; the code compares two values of the plan until a mirror is published
  (BL-0125).

Not replayed here: traces with boot journals (recover-all's per-journal tries over their files; the queue replay walks
recover-all's branches), and a save the walk cut before it ended. The durable layer (what a power loss would keep) is
not compared: the writer's and the restore's own tests check every power-loss state against one oracle
(`tests/ledger/worlds.py`).
"""

from __future__ import annotations

import errno
import json
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any, Final, override

import pytest

from ledger_engine.fs.port import LegacyAbsent
from ledger_engine.ledger import overlay
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Absent, Content, FileState, Fingerprint, digest
from ledger_engine.ledger.writer import Change, NotSaved, Step, Unfinished, sha256
from tests.fakes.memfs import Crash, MemFS, MemLock, MemLocks

pytestmark = [pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01")]

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "formal" / "tla" / "engine-jobs" / "traces" / "corpus.json"

type State = dict[str, Any]

ABS: Final = 0
TORN: Final = -1
FILES: Final = ("R", "N", "I")
PATH: Final = {"R": "/w/L/main.bean", "N": "/w/L/prices.bean", "I": "/w/L/inc.bean"}
BASE: Final = {f: p.rpartition("/")[2] for f, p in PATH.items()}
FILE_OF: Final = {b: f for f, b in BASE.items()}
LEDGER: Final = "/w/L"
ALD: Final = "/w/app"
KEY: Final = "k1"
JD: Final = f"{ALD}/local/{KEY}/journal"
BD: Final = f"{ALD}/local/{KEY}/backups"
SESSION: Final = "m"
WORD: Final = ("N", "R")  # the model's WOrd, the plan's order: children first, the root last
RORD: Final = ("R", "N")  # the model's ROrd: the restore, root first
SAVES: Final = ("s1", "s2", "s3")
POST: Final = {"s1": 3, "s2": 4, "s3": 5}
PER_FILE: Final = frozenset({"s_tw1", "s_tw2", "s_ts", "s_sw", "s_cmp", "s_ds", "rs_w", "rs_x", "rs_c"})
RESTORE: Final = frozenset({"rb_chk", "rs_w", "rs_x", "rs_c", "rs_tmp", "rs_sync", "rs_res"})
EARLY_RESTORE: Final = frozenset({"rb_chk", "rs_w", "rs_x", "rs_c", "rs_tmp"})
"""The rollback before the model's `RS_Temps` has run: the code removes each file's names of ours as it settles that
file, root first (spec §8.1 step 3: a replaced file's save temp only after its pre bytes are back), the model removes
them all at `RS_Temps`; so there a save temp of the code's is the model's, or already gone (`same`)."""
SYNC_STEPS: Final = frozenset({"S_BackupSync", "S_JPartSync", "S_JDirSync", "S_TempSync", "S_DirSync", "S_CloseSync"})
"""The save's durable steps: each is a sync of the code's (a file's data or a folder), so the code may never pass one
without it (round 1, verifier F2: a sync changes nothing the view shows, so the passable rule alone let a deleted sync
through)."""
WRITE_PCS: Final = frozenset({"s_tw1", "s_tw2", "s_ts", "s_sw", "s_cmp", "s_ds", "s_rbk", "s_rmt"})

STOPS: Mapping[str, str] = {
    "swap_back": "the model swaps an outside save back; the code moves it to a kept name and stops (BL-0161)",
    "restore_swap_back": "the model's restore swaps an outside save back; the code keeps it and stops (BL-0161)",
    "temp_rule": "the model's rollback stops by the old temp rule; the code's staging-name rule differs (BL-0161)",
    "created_path": (
        "the model's rollback removes a file at a created path; the code never does and stops `created_path_taken` "
        "(BL-0161)"
    ),
    "backup_check": (
        "an outside edit of a replaced file after the re-check: the code's backup step reads the file and stops "
        "`changed` at once; the model copies the plan's bytes unread and finds the edit at the displaced check "
        "(BL-0161)"
    ),
    "r4_in_save": "the model runs an owed R4 inside the save; the code refuses `check_pending` until R4 (BL-0152)",
    "mirror": "the model's gate refuses a stale mirror; the code compares two values of the plan (BL-0125)",
    "cut": "the walk ended before the save did (not a difference)",
    "after_stop": "a later save of a trace stopped before it (the fake no longer follows the model)",
}
"""Why a save of the corpus is not replayed (the module's docstring), each counted by `test_the_stops_are_counted`."""

DISK: Mapping[str, str] = {
    "closed": "saved", "done": "saved", "mismatch": "saved", "resolved": "notsaved", "open": "unfinished",
    "kept": "unfinished",
}  # fmt: skip
"""The model's `DiskOutcome` over the engine's facts (§6.6); no record or a `.part` is the re-hash."""


def content(f: str, v: int) -> bytes:
    assert v > 0, f"the model value {v} of {f} has no bytes"
    return f"; {f} {v}\n".encode()


def value(f: str, data: bytes | None) -> int:
    """The model value of bytes found at a name of file `f`: absent, `; f v`, or anything else (torn)."""
    if data is None:
        return ABS
    found = re.fullmatch(rb"; ([RNI]) (\d+)\n", data)
    return int(found.group(2)) if found is not None and found.group(1).decode() == f else TORN


def record(s: str) -> str:
    return f"{SESSION}-{s}"


# -- the corpus ------------------------------------------------------------------------------------------------------


def undelta(steps: list[State]) -> list[State]:
    """The states of a trace, from the corpus's steps (each the variables that changed, and under "-" the ones gone;
    a copy of `gen_traces.undelta`, which a test may not import)."""
    states: list[State] = []
    for step in steps:
        state = {**(states[-1] if states else {}), **step}
        for k in state.pop("-", []):
            del state[k]
        states.append(state)
    return states


@cache
def corpus() -> tuple[State, tuple[State, ...]]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[1:]]
    return json.loads(lines[0]), tuple({**r, "states": undelta(r["steps"])} for r in rows)


def worlds() -> list[str]:
    meta, _ = corpus()
    return sorted(w for w, c in meta["constants"].items() if c["BootJ"] == "{}")


# -- the views -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    files: tuple[int, ...]
    journal: str
    temps: tuple[int, ...]
    backup: int
    rtemps: tuple[int, ...]


def same(model: View, code: View, pc: str) -> bool:
    """The code's view is the model's, but for the save temps the code has already removed in the rollback
    (`EARLY_RESTORE`)."""
    if pc not in EARLY_RESTORE:
        return model == code
    temps_ok = all(c in (m, ABS) for m, c in zip(model.temps, code.temps, strict=True))
    return temps_ok and replace(model, temps=()) == replace(code, temps=())


def model_view(st: State, s: str) -> View:
    fs = st["fs"]
    return View(
        tuple(fs["cur"][f] for f in FILES),
        st["jr"][s],
        tuple(fs["tc"][f"{s}/{f}"] for f in ("R", "N")),
        fs["bk"][s],
        tuple(fs["rt"][f] for f in ("R", "N")),
    )


def _scratch(fs: MemFS, f: str, tag: str) -> int:
    """What stands at a name of ours beside `f`: its temp, else its staging file (a write cut there is torn)."""
    for suffix in ("tmp", "new"):
        path = f"{LEDGER}/.{BASE[f]}.{tag}.{suffix}"
        if path in fs.names_in(LEDGER) or fs.file(path) is not None:
            return value(f, fs.file(path))
    return ABS


def journal_state(fs: MemFS, s: str) -> str:
    states = sorted(n.rpartition(".")[2] for n in fs.names_in(JD) if n.startswith(record(s) + "."))
    return "+".join(states) if states else "none"


def code_view(fs: MemFS, s: str) -> View:
    backups = f"{BD}/{record(s)}"
    names = fs.names_in(BD)
    backup = value("R", fs.file(f"{backups}/1-{BASE['R']}")) if record(s) in names else ABS
    return View(
        tuple(value(f, fs.file(PATH[f])) for f in FILES),
        journal_state(fs, s),
        tuple(_scratch(fs, f, f"ft-{record(s)}") for f in ("R", "N")),
        backup,
        tuple(_scratch(fs, f, f"ft-restore-{record(s)}") for f in ("R", "N")),
    )


# -- an episode: one save of a trace ---------------------------------------------------------------------------------


@dataclass
class Event:
    kind: str  # "edit", "kill", "io"
    file: str = ""
    value: int = 0


@dataclass
class Point:
    """The model's save at one step (`pc`, file index `fi`): the state just before its step (after its events),
    the events made there, and the step that leaves it."""

    pc: str
    fi: int
    last: State
    events: list[Event] = field(default_factory=lambda: list[Event]())
    step: str = ""
    after: State | None = None

    @property
    def file(self) -> str:
        if self.pc not in PER_FILE:
            return ""
        return (RORD if self.pc in RESTORE else WORD)[self.fi - 1]

    def visible(self) -> bool:
        """The model's own step here changed what the view shows (passed by the code only for `RS_Temps`, whose
        removals the code spreads over the rollback; the next comparison then shows them done)."""
        if self.after is None:
            return False
        s = self.last["sv"]["s"]
        return model_view(self.last, s) != model_view(self.after, s)

    def passable(self) -> bool:
        """The model's own step here changed nothing the view shows (the code may carry it out without an effect)."""
        if self.after is None:
            return True
        s = self.last["sv"]["s"]
        before, after = model_view(self.last, s), model_view(self.after, s)
        if self.pc in EARLY_RESTORE or self.after["ctl"]["pc"] in EARLY_RESTORE | {"rs_sync"}:
            return replace(before, temps=()) == replace(after, temps=())  # `RS_Temps` is spread in the code
        return before == after


@dataclass
class Episode:
    s: str
    plan: Mapping[str, int]
    points: list[Point]
    told: str
    end: State
    killed: bool
    actions: Counter[str]
    start: State
    stop: str = ""


def _saving(st: State, job: int | None = None) -> bool:
    r = st["run"]
    return r is not None and r["k"] == "save" and (job is None or r["id"] == job)


JOB_STEP = re.compile(r"^(S_|RB_|RS_|R4_)|^(FreshEnd|SpawnStep)$")


def _stop(s: State, t: State) -> str:
    """Why the model's step from `s` to `t` cannot be replayed through the code (`STOPS`), or ""."""
    a = t["act"]["a"]
    sv_, fs = s["sv"], s["fs"]
    into_rollback = t["run"] is not None and t["ctl"]["pc"] == "rb_chk" and s["ctl"]["pc"] != "rb_chk"
    if a == "S_R4Check" and t["ctl"]["pc"] != "s_locks":
        return "r4_in_save"
    if a == "S_Gate" and t["run"] is None:
        disk = all(fs["cur"][f] == sv_["plan"][f] for f in FILES)
        gating = any(s["jr"][j] in ("open", "closed", "mismatch") for j in SAVES)
        if disk and not gating:
            return "mirror"
    if a == "S_Compare" and into_rollback:
        return "swap_back"
    if a == "RS_Compare" and (t["run"] is None or t["ctl"]["pc"] not in ("rs_w", "rs_tmp")):
        return "restore_swap_back"
    if a == "RB_Check" and (t["run"] is None or t["ctl"]["pc"] != "rs_w"):
        return "temp_rule"
    if into_rollback and sv_["plan"]["N"] == ABS and fs["cur"]["N"] != ABS:
        return "created_path"  # the save placed the file it creates, or an editor did: the code stops there
    replaced = [f for f in WORD if sv_["plan"][f] != ABS]
    if a == "Editor" and t["act"]["p"] in replaced and s["ctl"]["pc"] in ("s_commit", "s_bk"):
        return "backup_check"
    return ""


def episodes(trace: State) -> Iterator[Episode]:
    states: list[State] = trace["states"]
    i = 0
    while i < len(states):
        st = states[i]
        if not _saving(st) or (i > 0 and _saving(states[i - 1], st["run"]["id"])):
            i += 1
            continue
        job = st["run"]["id"]
        s = st["sv"]["s"]
        points = [Point(st["ctl"]["pc"], st["sv"]["fi"], st)]
        actions: Counter[str] = Counter()
        stop, killed = "", False
        j = i
        while j + 1 < len(states) and _saving(states[j], job):
            a_, b_ = states[j], states[j + 1]
            a = b_["act"]["a"]
            actions[a] += 1
            stop = stop or _stop(a_, b_)
            p = points[-1]
            p.last = a_
            if a == "Editor":
                p.events.append(Event("edit", str(b_["act"]["p"]), b_["fs"]["cur"][b_["act"]["p"]]))
                p.last = b_
            elif a == "IOFail":
                p.events.append(Event("io"))
                p.step, p.after = a, b_
                if _saving(b_, job):
                    points.append(Point(b_["ctl"]["pc"], b_["sv"]["fi"], b_))
            elif a in ("EngineCrash", "UserCancelRunning", "UserCancelQueued") and not _saving(b_, job):
                p.events.append(Event("kill"))
                killed = True
            elif JOB_STEP.match(a):
                p.step, p.after = a, b_
                if _saving(b_, job):
                    points.append(Point(b_["ctl"]["pc"], b_["sv"]["fi"], b_))
            j += 1
        end = states[j]
        if _saving(end, job):
            stop = stop or "cut"
        yield Episode(s, dict(st["sv"]["plan"]), points, end["told"][s], end, killed, actions, st, stop)
        i = j


# -- the code's hooks ------------------------------------------------------------------------------------------------


class Divergence(AssertionError):
    pass


class Driver:
    """Walks the model's points as the code reaches its hooks (the module's docstring)."""

    def __init__(self, fs: MemFS, ep: Episode, where: str) -> None:
        self.fs = fs
        self.ep = ep
        self.where = where
        self.at = -1  # the point the code is at
        self.landed = False  # the code reached `at` at one of its hooks (it carries out that point's step next)
        self.last_pc = ""
        self.rolling = False
        self.compared = 0
        self.walked: Counter[str] = Counter()
        self.locks: MemLocks | None = None

    def key(self, i: int) -> tuple[str, str]:
        p = self.ep.points[i]
        return p.pc, p.file

    def compare(self, i: int) -> None:
        p = self.ep.points[i]
        want, got = model_view(p.last, self.ep.s), code_view(self.fs, self.ep.s)
        if not same(want, got, p.pc):
            msg = (
                f"{self.where}: at the model's {p.pc} {p.file}, the fake holds\n  {got}\nwhere the model has\n  {want}"
            )
            raise Divergence(msg)
        self.compared += 1

    def reach(self, pc: str, file: str = "") -> None:
        """The code is at the model step `pc` (of `file`): walk the model up to it. On the way, the outside edits made
        at each step are written, and an engine death or an I/O error made there is raised here; a step passed on the
        way must be one the code carries out without an effect."""
        self.last_pc = pc
        if self.at >= 0 and self.key(self.at) == (pc, file):
            return
        if self.landed:
            self.walked[self.ep.points[self.at].step] += 1  # the code carried out the step of the point it was at
        self.landed = False
        seen: list[str] = []  # steps passed on the way whose effect the next comparison shows (`RS_Temps`)
        for i in range(self.at + 1, len(self.ep.points)):
            p = self.ep.points[i]
            self.at = i
            for e in p.events:
                if e.kind == "edit":
                    self.fs.outside_write(PATH[e.file], content(e.file, e.value))
                elif e.kind == "kill":
                    self.compare(i)
                    self.walked.update(seen)
                    msg = f"the model's engine ended at {p.pc}"
                    raise Crash(msg)
                else:
                    self.compare(i)
                    self.walked.update([*seen, "IOFail"])  # the I/O error is raised at the code's hook
                    self.rolling = p.pc in WRITE_PCS
                    self.last_pc = p.pc  # the code was cut here, at the model's step (the effect did not happen)
                    raise OSError(errno.ENOSPC, f"the model's I/O error at {p.pc}")
            if self.key(i) == (pc, file):
                self.compare(i)
                self.walked.update(seen)
                self.landed = True
                return
            if p.visible():
                seen.append(p.step)
            if p.step in SYNC_STEPS:
                msg = f"{self.where}: the code is at {pc} {file}, past the model's {p.step} {p.file} without its sync"
                raise Divergence(msg)
            if not p.passable():
                msg = (
                    f"{self.where}: the code is at {pc} {file}, past the model's {p.pc} {p.file} ({p.step}), which "
                    "changes the disk"
                )
                raise Divergence(msg)
        msg = f"{self.where}: the code is at {pc} {file}, which the model's save does not reach"
        raise Divergence(msg)

    def finish(self, answered: bool) -> None:
        """The code has ended (`answered`: it returned, not cut by an engine death): every model step left must be one
        it carries out without an effect; the outside edits made there are written. Only the step of the point the
        code was at when it answered counts as walked: the steps left had no hook of the code's."""
        if answered and self.landed:
            self.walked[self.ep.points[self.at].step] += 1
        self.landed = False
        for i in range(self.at + 1, len(self.ep.points)):
            p = self.ep.points[i]
            for e in p.events:
                if e.kind == "edit":
                    self.fs.outside_write(PATH[e.file], content(e.file, e.value))
            if p.step and not p.passable():
                msg = f"{self.where}: the code ended before the model's {p.pc} ({p.step}), which changes the disk"
                raise Divergence(msg)
            if answered and p.step in SYNC_STEPS:
                msg = f"{self.where}: the code answered before the model's {p.step} {p.file}, without its sync"
                raise Divergence(msg)

    # -- the effects of the file-system port, by the model step they belong to --------------------------------------

    def classify(self, label: str) -> tuple[str, str]:
        op, _, rest = label.partition(" ")
        folder, _, names = rest.partition(":")
        name = record(self.ep.s)
        last = self.last_pc
        if folder == f"{BD}/{name}":  # the model backs up R only (N did not exist in its first save)
            return ("s_bks", "") if op == "sync" else ("s_bk", "")  # the copy (data synced), then its folder's sync
        if op == "sync_file" or ((folder.startswith(ALD) or folder in ("/", "/w")) and folder != JD):
            return "s_bk", ""
        if folder == JD:
            if names == f"{name}.part":
                return ("s_jps", "") if op == "fsync" else ("s_jp", "")
            if names == f"{name}.part,{name}.open":
                return "s_jo", ""
            if names == f"{name}.open,{name}.closed":
                return "s_close", ""
            if names == f"{name}.open,{name}.resolved":
                return "rs_res", ""
            if op == "sync":
                if self.rolling:
                    return last, self._file_of(last)
                return {"s_jo": "s_jds", "s_close": "s_csync"}.get(last, last), self._file_of(last)
        if folder == LEDGER:
            first = names.split(",")[0]
            found = re.fullmatch(rf"\.(.+)\.ft-(restore-)?{re.escape(name)}\.(new|tmp)", first)
            if found is not None:
                f, restore, suffix = FILE_OF[found.group(1)], found.group(2), found.group(3)
                if restore:
                    if op == "swap":
                        return "rs_x", f
                    if op == "remove":
                        return "rs_c", f
                    return "rs_w", f
                if op == "create":
                    return "s_tw1", f
                if op == "write":
                    return "s_tw2", f
                if op in ("fsync", "rename") and suffix == "new":
                    return "s_tw2", f  # written and synced at the staging name, renamed to the temp name...
                if op in ("swap", "rename"):
                    return "s_sw", f
                if op == "remove" and (self.rolling or last in RESTORE):
                    return last, self._file_of(last)  # the rollback settles each file's names of ours as it goes
                if op == "remove":
                    return "s_rmt", ""
            if op == "sync":
                if last == "s_tw2" and not self.rolling:
                    return "s_ts", self._file_of(last)  # ...and that name made durable (`S_TempSync`)
                if last == "s_sw" and not self.rolling:
                    return "s_ds", self._file_of(last)
                if last == "rs_c":
                    return "rs_sync", ""  # the restore's folders synced after its last put-back (`RS_Sync`)
                return last, self._file_of(last)
        msg = f"{self.where}: an effect the replay cannot place: {label}"
        raise Divergence(msg)

    def _file_of(self, pc: str) -> str:
        return self.key(self.at)[1] if pc in PER_FILE and self.at >= 0 else ""

    def on_effect(self, i: int, label: str) -> None:
        pc, f = self.classify(label)
        self.reach(pc, f)


class Locks(MemLocks):
    def __init__(self, driver: Driver) -> None:
        super().__init__()
        self.driver = driver

    @override
    def try_lock(self, key: str) -> MemLock | None:
        if not self.taken:
            self.driver.reach("s_locks")
        return super().try_lock(key)


class NoLegacy:
    def try_lock(self, path: str) -> LegacyAbsent:
        return LegacyAbsent()


class Job:
    def __init__(self, driver: Driver) -> None:
        self.driver = driver

    def progress(self, phase: str) -> None:
        pass

    def gate(self) -> None:
        self.driver.reach("s_commit")

    def committed(self) -> None:
        self.driver.reach("s_saved")


def _state_of(fs: MemFS, path: str) -> FileState:
    data = fs.file(path)
    return Absent() if data is None else Content(sha256(data))


def ports(fs: MemFS, driver: Driver) -> sv.Ports:
    seen = Counter[str]()

    def observe(fp: Fingerprint) -> Fingerprint:
        seen["observe"] += 1
        driver.reach("s_gate" if seen["observe"] == 1 else "s_recheck")
        files = tuple((p, _state_of(fs, p)) for p, _ in fp.files)
        return Fingerprint(fp.root, files, fp.globs, fp.documents, fp.document_files)

    def load(root: str, candidates: Mapping[str, bytes]) -> overlay.LoadResult:
        driver.reach("s_load")
        files = {p: Content(sha256(candidates[p])) if p in candidates else _state_of(fs, p) for p in PATH.values()}
        return overlay.Loaded([], [], {}, Fingerprint.of(PATH["R"], files, {}, {}, {}))

    driver.locks = Locks(driver)
    return sv.Ports(fs, driver.locks, NoLegacy(), load, observe)


def plan_of(ep: Episode) -> sv.SavePlan:
    def state(f: str) -> FileState:
        v = ep.plan[f]
        return Absent() if v == ABS else Content(sha256(content(f, v)))

    def pre(f: str) -> str | None:
        v = ep.plan[f]
        return None if v == ABS else sha256(content(f, v))

    fp = Fingerprint.of(PATH["R"], {PATH[f]: state(f) for f in FILES}, {}, {}, {})
    changes = tuple(Change(PATH[f], pre(f), content(f, POST[ep.s])) for f in WORD)
    return sv.SavePlan(SESSION, ep.s, KEY, "lin1", "b", PATH["R"], changes, fp, digest(fp), (), ALD)


def told_of(out: sv.SaveOutcome | None, fs: MemFS, plan: sv.SavePlan) -> str:
    """What the code tells, in the model's words (the module's docstring)."""
    if out is None:  # the engine ended: §6.6's reading of the disk in a fresh engine
        facts = sv.outcome(fs, ALD, KEY, SESSION, plan.job, plan.changes)
        if facts.record in DISK:
            return DISK[facts.record]
        assert facts.record in ("none", "part"), facts
        return "unchanged" if facts.unchanged else "changed"
    match out:
        case sv.Saved():
            return "saved"
        case sv.Refused():
            return "notsaved"
        case NotSaved() if out.step in (Step.BACKUP, Step.JOURNAL):
            return "unchanged" if out.unchanged else "changed"
        case NotSaved():
            return "notsaved" if out.reason.value == "io_error" else "changed"
        case Unfinished():
            return "unfinished"


# -- the replay ------------------------------------------------------------------------------------------------------


@dataclass
class Tally:
    saves: int = 0
    compared: int = 0
    walked: Counter[str] = field(default_factory=lambda: Counter[str]())
    told: Counter[str] = field(default_factory=lambda: Counter[str]())
    stops: Counter[str] = field(default_factory=lambda: Counter[str]())
    branches: Counter[str] = field(default_factory=lambda: Counter[str]())


def world() -> MemFS:
    fs = MemFS()
    for d in (LEDGER, JD, BD):
        fs.outside_mkdirs(d)
    return fs


def sync_world(fs: MemFS, st: State, where: str) -> None:
    """The fake made to hold what the model holds between two saves: the files (an outside edit while no save ran)
    and each record's state after R4 (`.closed` to `.done` or `.mismatch`: R4 is BL-0152's, so its rename is
    projected); anything else that differs is a divergence."""
    for f in FILES:
        v = st["fs"]["cur"][f]
        if value(f, fs.file(PATH[f])) != v:
            assert v > 0, f"{where}: the model removed {f} between saves"
            fs.outside_write(PATH[f], content(f, v))
    for s in SAVES:
        want, got = st["jr"][s], journal_state(fs, s)
        if want == got:
            continue
        if got == "closed" and want in ("done", "mismatch"):
            data = fs.file(f"{JD}/{record(s)}.closed")
            assert data is not None
            fs.outside_remove(f"{JD}/{record(s)}.closed")
            fs.outside_write(f"{JD}/{record(s)}.{want}", data)
            continue
        msg = f"{where}: between saves the model's record of {s} is {want}, the fake's {got}"
        raise Divergence(msg)


def replay_save(fs: MemFS, ep: Episode, where: str, tally: Tally) -> None:
    driver = Driver(fs, ep, where)
    plan = plan_of(ep)
    fs.on_effect = driver.on_effect
    out: sv.SaveOutcome | None = None
    try:
        out = sv.run(plan, ports(fs, driver), Job(driver))
    except Crash:
        assert ep.killed, f"{where}: the code's engine died where the model's did not"
    finally:
        fs.on_effect = None
    driver.finish(answered=out is not None)
    assert driver.locks is not None
    assert not driver.locks.held, f"{where}: the save left ledger-set locks held: {driver.locks.held}"  # S_Release
    told = told_of(out, fs, plan)
    if isinstance(out, sv.Refused):  # "unchanged" is said only when the targets equal their pre bytes (§11)
        fs_end = ep.end["fs"]["cur"]
        assert out.unchanged == all(fs_end[f] == ep.plan[f] for f in WORD), f"{where}: {out}, the model's {fs_end}"
    if ep.killed and out is not None:  # the model's end came after the code's last hook: read the disk as it does
        told = told_of(None, fs, plan)
    assert told == ep.told, f"{where}: the code tells {told!r} ({out}), the model {ep.told!r}"
    want, got = model_view(ep.end, ep.s), code_view(fs, ep.s)
    assert want == got, f"{where}: after the save the fake holds\n  {got}\nwhere the model has\n  {want}"
    tally.saves += 1
    tally.compared += driver.compared + 1
    tally.walked.update(driver.walked)
    tally.told[ep.told] += 1
    tally.branches.update(branches(ep, out))


def branches(ep: Episode, out: sv.SaveOutcome | None) -> Iterator[str]:
    """The save's branches the replay walked (`BRANCHES`)."""
    pcs = {p.pc for p in ep.points}
    last = ep.points[-1].pc
    if ep.killed:
        yield "an engine death past the commit point" if "s_bk" in pcs else "an engine death before the commit point"
        if "s_csync" in pcs:
            yield "an engine death after the close"
        if last in RESTORE:
            yield "an engine death during the rollback"
    if "IOFail" in ep.actions:
        early = isinstance(out, NotSaved) and out.step in (Step.BACKUP, Step.JOURNAL)
        yield (
            "an I/O error before the journal is open"
            if early
            else "an I/O error after the journal is open: the rollback"
        )
        if early and ep.told == "changed":
            yield "an I/O error before the journal is open, after an outside edit: not saved, changed"
    if ep.actions["RS_Swap"]:
        yield "the rollback puts pre bytes back"
    if isinstance(out, sv.Refused):
        yield f"refused: {out.reason}"
    if isinstance(out, sv.Saved):
        yield "saved"
        if ep.plan["N"] != ABS:
            yield "saved, the second file replaced"
    if ep.actions["Editor"]:
        yield "an outside edit during the save"


BRANCHES: tuple[str, ...] = (
    "saved",
    "saved, the second file replaced",
    "refused: ledger_changed",
    "refused: unfinished_save",
    "an engine death before the commit point",
    "an engine death past the commit point",
    "an engine death after the close",
    "an engine death during the rollback",
    "an I/O error before the journal is open",
    "an I/O error before the journal is open, after an outside edit: not saved, changed",
    "an I/O error after the journal is open: the rollback",
    "the rollback puts pre bytes back",
    "an outside edit during the save",
)
"""The save's branches the corpus must walk through the code (S0064)."""

NOT_WALKED_BRANCHES: Mapping[str, str] = {
    "Unfinished told by the engine itself (the rollback could not resolve)": (
        "the model's rollback stops only by its old temp rule or a restore swap-back (the stops `temp_rule` and "
        "`restore_swap_back`, BL-0161), or after a second I/O error (no world has two)"
    ),
}
"""The save's branches the replay does not walk through the code, each with its reason."""


def replay_trace(trace: State, tally: Tally) -> None:
    fs = world()
    for f in FILES:
        v = trace["states"][0]["fs"]["cur"][f]
        if v != ABS:
            fs.outside_write(PATH[f], content(f, v))
    stopped = False
    for ep in episodes(trace):
        where = f"{trace['world']} trace {trace['trace']}, save {ep.s}"
        if stopped:
            tally.stops["after_stop"] += 1
            continue
        if ep.stop:
            tally.stops[ep.stop] += 1
            stopped = True
            continue
        sync_world(fs, ep.start, where)
        replay_save(fs, ep, where, tally)


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
        tally.saves += t.saves
        tally.compared += t.compared
        tally.walked.update(t.walked)
        tally.told.update(t.told)
        tally.stops.update(t.stops)
        tally.branches.update(t.branches)
    return tally


# -- the tests -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("world_name", worlds())
def test_the_engine_save_follows_the_model(world_name: str) -> None:
    run_world(world_name)


def test_the_replay_walks_the_save_path() -> None:
    """Every step of `WALKED` is walked through the code at least once, and every branch of `BRANCHES`; what the
    replay does not walk is named with its reason (`NOT_WALKED`, `NOT_WALKED_BRANCHES`)."""
    tally = every_world()
    assert set(tally.walked) <= set(WALKED), f"counted as walked, with no hook of the code's: {dict(tally.walked)}"
    missing_steps = [a for a in WALKED if tally.walked[a] == 0]
    assert not missing_steps, f"the replay never walks {missing_steps}: widen the save worlds in gen_traces.py"
    missing = [b for b in BRANCHES if tally.branches[b] == 0]
    assert not missing, f"the replay never walks {missing}: widen the save worlds in gen_traces.py"
    assert tally.saves >= 50, tally


WALKED: tuple[str, ...] = (
    "S_Locks", "S_Gate", "S_Load", "S_Recheck", "S_Commit", "S_Backup", "S_BackupSync", "S_JPart", "S_JPartSync",
    "S_JOpen", "S_JDirSync", "S_TempStart", "S_TempDone", "S_TempSync", "S_Swap", "S_DirSync", "S_RemoveTemps",
    "S_Close", "S_CloseSync", "S_Saved", "RS_Write", "RS_Swap", "RS_Compare", "RS_Temps", "RS_Sync", "RS_Resolve",
    "IOFail",
)  # fmt: skip
"""The model's save steps the replay walks through the code: the code reached the step at one of its hooks and went
on (`Driver.reach`, `finish`), the I/O error raised at a hook, or (`RS_Temps` only) the step's effect shown by the
comparison at the next hook. A step the model passes without a hook of the code's is not counted, so deleting the
code that carries out one of these (a folder's sync, say) fails `test_the_replay_walks_the_save_path`. Each is marked
`@tla_action` on that code (`backend/tests/architecture/test_tla_mapping.py`)."""

NOT_WALKED: Mapping[str, str] = {
    "S_R4Check": "a read of the records, with no hook; an owed R4 runs inside the save with BL-0152 (a stop)",
    "S_WaitR4": "a seeded bug's action only",
    "S_Compare": (
        "a read of what the swap displaced, with no hook; the replay sees only that the code went on when the model "
        "found its own bytes (the other branch is a stop, BL-0161); `test_writer.py` checks the kept-name branch"
    ),
    "S_ReadBack": "a read, with no hook; the model's read-back fails only after a stop (BL-0161); `test_writer.py`",
    "S_Publish": "not built (BL-0113)",
    "S_Release": "no hook; the replay asserts every ledger-set lock released after each save; the queued R4 is BL-0152",
    "RB_Check": "reads, with no hook; its stopping branches are stops (BL-0161); `test_restore.py`",
}
"""The model's save steps the replay does not count as walked, each with its reason (`PARTIAL` or `PLANNED` in the
code map; S0064 round 1, verifier F2)."""


def test_the_save_steps_are_the_models() -> None:
    """`WALKED` and `NOT_WALKED` together are the model's save steps (its `S_*`, `RB_*`, `RS_*` and `IOFail`), each in
    one of them: a step added to the model fails here until it is placed."""
    model = (REPO / "formal" / "tla" / "engine-jobs" / "EngineJobs.tla").read_text(encoding="utf-8")
    steps = set(re.findall(r"^((?:S|RB|RS)_[A-Za-z0-9]+) ==", model, re.MULTILINE)) | {"IOFail"}
    assert not set(WALKED) & set(NOT_WALKED)
    assert set(WALKED) | set(NOT_WALKED) == steps


STOP_COUNTS: Mapping[str, int] = {
    "r4_in_save": 17, "backup_check": 3, "created_path": 2, "swap_back": 1, "cut": 3, "after_stop": 1,
}  # fmt: skip
"""The saves of the corpus the replay does not follow, by reason (`STOPS`); pinned, so a regenerated corpus or a
changed stop rule shows here what it stops."""


def test_the_stops_are_counted() -> None:
    """Every save the replay does not follow is counted under a reason of `STOPS`, as many as `STOP_COUNTS` says."""
    tally = every_world()
    assert set(tally.stops) <= set(STOPS), tally.stops
    assert dict(tally.stops) == STOP_COUNTS, f"followed {tally.saves}, stopped {dict(tally.stops)}"
    assert tally.saves > sum(STOP_COUNTS.values())


def _first_failure() -> str:
    """The replay of every world run afresh: the message of its first failure, or "" (the self-tests below)."""
    every_world.cache_clear()
    try:
        every_world()
    except AssertionError as e:
        return str(e)
    finally:
        every_world.cache_clear()
    return ""


# The replay is not vacuous: each of its four comparisons fails on a change only it sees, with its own message (S0064
# round 1, verifier F1). Must not pass: with that comparison disabled, a later one fails with another message, or none.


def test_a_skipped_step_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The passable-step rule: a writer that leaves its temps beside the saved files skips the model's
    `S_RemoveTemps`, which changes the disk."""
    from ledger_engine.ledger import writer

    def keep_temps(handles: object, targets: object) -> None:
        del handles, targets

    monkeypatch.setattr(writer, "_remove_temps", keep_temps)
    assert re.search(r"past the model's s_rmt .*\(S_RemoveTemps\), which changes the disk", _first_failure())


def test_a_save_that_stops_early_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same rule after the code answered (`Driver.finish`): a save that answers at once leaves the model's backup
    undone."""

    def answer_at_once(plan: sv.SavePlan, ports: sv.Ports, job: object) -> sv.SaveOutcome:
        del plan, ports, job
        return sv.Refused("ledger_changed", True)

    monkeypatch.setattr(sv, "run", answer_at_once)
    assert re.search(r"the code ended before the model's s_bk \(S_Backup\), which changes the disk", _first_failure())


def test_a_skipped_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The save's syncs (`SYNC_STEPS`): a writer that stages its temp without the folder's sync is caught, though
    nothing the view shows differs."""
    from ledger_engine.ledger import writer

    stage = writer._stage  # pyright: ignore[reportPrivateUsage]

    class NoSync:
        def __init__(self, h: Any) -> None:
            self.h = h

        def __getattr__(self, name: str) -> Any:
            return getattr(self.h, name)

        def sync(self) -> None:
            pass

    def stage_unsynced(h: Any, t: Any, mode: int, like: str | None) -> None:
        unsynced: Any = NoSync(h)
        stage(unsynced, t, mode, like)

    monkeypatch.setattr(writer, "_stage", stage_unsynced)
    assert re.search(r"past the model's S_TempSync [RN] without its sync", _first_failure())


def test_a_wrong_step_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison at each step: a writer that stages other bytes than the plan's is caught at the step after its
    write, before the read-back or the answer could tell."""
    from dataclasses import replace as copy

    from ledger_engine.ledger import writer

    stage = writer._stage  # pyright: ignore[reportPrivateUsage]

    def stage_other(h: Any, t: Any, mode: int, like: str | None) -> None:
        stage(h, copy(t, change=copy(t.change, post=t.change.post + b"; other\n")), mode, like)

    monkeypatch.setattr(writer, "_stage", stage_other)
    assert re.search(r"at the model's s_ts [RN], the fake holds", _first_failure())


def test_a_wrong_end_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison after the save: a save that leaves a temp behind after its last hook is caught there."""
    run = sv.run

    def leave_temp(plan: sv.SavePlan, ports: sv.Ports, job: Any) -> sv.SaveOutcome:
        out = run(plan, ports, job)
        if isinstance(out, sv.Saved):
            fs = ports.fs
            assert isinstance(fs, MemFS)
            fs.outside_write(f"{LEDGER}/.{BASE['R']}.ft-{record(plan.job)}.tmp", content("R", POST[plan.job]))
        return out

    monkeypatch.setattr(sv, "run", leave_temp)
    assert "after the save the fake holds" in _first_failure()


def test_a_wrong_answer_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer against what the model told: a writer whose "not saved" always says the files are unchanged (no
    re-hash) is caught where an outside edit came before an I/O error (`S_save_io_edit`)."""
    from ledger_engine.ledger import writer

    def always_unchanged(handles: object, targets: object) -> bool:
        del handles, targets
        return True

    monkeypatch.setattr(writer, "_unchanged", always_unchanged)
    assert "the code tells 'unchanged'" in _first_failure()


def test_the_disk_outcome_table_is_the_models() -> None:
    """`DISK` restates the model's `DiskOutcome` (a `.closed`, `.done` or `.mismatch` record is saved, `.resolved` not
    saved, `.open` unfinished); the engine's `kept` is an `.open` journal whose kept copy stops it (§6.4)."""
    model = (REPO / "formal" / "tla" / "engine-jobs" / "EngineJobs.tla").read_text(encoding="utf-8")
    assert 'DiskOutcome(s) == CASE jr[s].st \\in CL -> "saved"' in model
    assert 'CL       == {"closed","done","mismatch"}' in model
    assert '[] jr[s].st = "resolved" -> "notsaved"' in model
    assert {k for k, v in DISK.items() if v == "saved"} == {"closed", "done", "mismatch"}
    assert DISK["resolved"] == "notsaved"
    assert DISK["open"] == DISK["kept"] == "unfinished"


def _pair(a: str, pc: str, next_pc: str | None, **over: Any) -> tuple[State, State]:
    """A synthetic model step of a running save `s1` (plan: R at 1, N absent), for the stop rules' own tests."""
    run = {"id": 2, "k": "save"}
    s: State = {
        "run": run, "ctl": {"pc": pc}, "jr": dict.fromkeys(SAVES, "none"),
        "fs": {"cur": {"R": 1, "N": 0, "I": 1}}, "sv": {"plan": {"R": 1, "N": 0, "I": 1}},
    }  # fmt: skip
    s["fs"]["cur"].update(over.get("cur", {}))
    s["jr"].update(over.get("jr", {}))
    t: State = {
        "run": run if next_pc else None,
        "ctl": {"pc": next_pc or "none"},
        "act": {"a": a, "p": over.get("p", 0)},
    }
    return s, t


@pytest.mark.parametrize(
    ("step", "want"),
    [
        (_pair("S_R4Check", "s_r4chk", "r4_a"), "r4_in_save"),
        (_pair("S_R4Check", "s_r4chk", "s_locks"), ""),
        (_pair("S_Gate", "s_gate", None), "mirror"),
        (_pair("S_Gate", "s_gate", None, cur={"I": 11}), ""),  # the disk moved: the code refuses too
        (_pair("S_Gate", "s_gate", None, jr={"s2": "open"}), ""),  # an `.open` journal: the code refuses too
        (_pair("S_Compare", "s_cmp", "rb_chk", cur={"N": 3}), "swap_back"),
        (_pair("S_Compare", "s_cmp", "s_ds", cur={"N": 3}), ""),
        (_pair("RS_Compare", "rs_c", None), "restore_swap_back"),
        (_pair("RS_Compare", "rs_c", "rs_tmp"), ""),
        (_pair("RB_Check", "rb_chk", None), "temp_rule"),
        (_pair("RB_Check", "rb_chk", "rs_w"), ""),
        (_pair("IOFail", "s_tw1", "rb_chk", cur={"N": 3}), "created_path"),
        (_pair("IOFail", "s_tw1", "rb_chk"), ""),  # nothing at the created path yet: the code resolves too
        (_pair("Editor", "s_bk", "s_bk", p="R"), "backup_check"),
        (_pair("Editor", "s_bk", "s_bk", p="I"), ""),  # not a file the save replaces
        (_pair("Editor", "s_jp", "s_jp", p="R"), ""),  # after the backup: the displaced check's (or a stop of its own)
    ],
)
def test_a_stop_rule_fires_on_its_case_only(step: tuple[State, State], want: str) -> None:
    """Each rule of `_stop` must name only the model behaviour the code does not have; a broader rule would hide a
    real difference from the replay (must not pass: each rule's neighbour is replayed)."""
    assert _stop(*step) == want


def test_a_model_value_round_trips() -> None:
    for f in FILES:
        for v in (1, 3, 12, 25):
            assert value(f, content(f, v)) == v
        assert value(f, None) == ABS
        assert value(f, b"") == TORN
        assert value(f, b"; X 3\n") == TORN
    assert value("R", content("N", 3)) == TORN
