"""The model's save path replayed through the engine's save (S0064, BL-0151 slice 5; `design/specs/engine-jobs.md`
§6.1, §6.4, §6.6, §13; ADR-0064; EP-19).

`formal/tla/engine-jobs/traces/corpus.json` holds TLC-simulated traces of `EngineJobs.tla` (`gen_traces.py`). Each
save a trace runs is replayed here through `ledger.save.run` over the in-memory file system (`tests/fakes/memfs.py`),
with the model's world 1 projected onto it: the model's `R` is the root `/w/L/main.bean` (replaced), `N` the file it
includes `/w/L/prices.bean` (created by the first save, replaced by a later one), `I` the include `/w/L/inc.bean`
(in the ledger set, never written); a model value `v` of file `f` is the bytes `; f v`. The plan is the model's
(`ctl.plan`), in the model's order (`WOrd`: children first, the root last).

The code runs one save in one call; the replay sees it at its **hooks**: each effect on the file system
(`MemFS.on_effect`, classified to the model step it belongs to, `Driver.classify`), the observation (step 4, the
model's `S_Gate`) and `job.committed()` (step 9). At each hook the replay walks the model's save up to the step the
code is at: the model's outside edits made there are written to the fake (as an editor would, durably), an engine death
is raised there (`Crash`), an I/O error is raised there (`OSError`, before the effect, or at a swap or exclusive rename
after it, where the model's `IOFail` took the swap's effect too), and the fake is compared with
the model's state just before the model's step (`view`): the three files, this save's journal record, its temps (the
save's and the restore's, or their staging files), its kept copies and its backup. A
model step the code carries out without an effect (the displaced check, the read-back)
is passed only when it changed nothing the view shows; the save's syncs (`SYNC_STEPS`) are never passed:
each must be one of the code's effects, and a model step that includes its folder's sync (`FUSED_SYNC`) must see that
sync among the code's effects before the code moves on. A step counts as walked only when the code carried it out at
one of its hooks (`WALKED`; the rest, with their reasons, `NOT_WALKED`).

After the save, what the code answered is checked against the model's disk at the save's end, never against what the
model told (`gh.told`: no property of the durability model checks that it is true, only that a "saved" is durable,
EP-19, ADR-0068 D5; S0072 verifier r1 F1). The disk's answer is §6.6's table over the save's record (`DISK`, the
model's `DiskOutcome`), else the re-hash, and the code's answer must be one that disk allows (`told_on_disk`): "saved"
only over a `.closed` record; Unfinished (the cautious answer) over any record, as `writer.Unfinished` says; "not
saved" or "changed" only with the record resolved, or `.open` with no post bytes of ours in place (the rollback stopped
before anything was replaced), or with no record (a refusal); the re-hash's "unchanged" or "changed" only as the files
are. After an engine death (or our end of the engine by a Cancel) the engine's reading of the disk (`save.outcome`)
must be the disk's answer itself (the sidecar's decision from those facts is its own table test,
`backend/tests/platform/test_save_job.py` `test_save_outcome_is_the_disks_answer`). The wording between "not saved"
and "changed", and whether a rollback that stopped says Unfinished, are the writer's tests' (`test_writer.py`:
`check_told` in its sweeps, `test_an_io_error_at_the_first_swap_with_an_outside_save_waiting_is_unfinished`).

**Where the model and the code differ** (MAPPING.md §3 lists them): the model's `RS_Clean` removes every name of ours of
the journal at once, the code file by file as one run of effects, compared once at the next hook; the model's I/O
error comes before a step's effect except at the swap or exclusive rename (the code's sweeps inject one after every
effect, `test_writer.py`); the restore's staging write, rename and sync are one model step (`RS_Write`), as its swap
and sync are (`RS_Swap`), so their syncs are checked as `FUSED_SYNC`. The replay stops a save only where its walk was
cut (`STOPS`), and never compares past it.

Not replayed here: traces with boot journals (recover-all's per-journal tries over their files: S0091,
`test_recover_conformance.py`), and a save
the walk cut before it ended. The durable layer (what a power loss would keep) is
not compared: the writer's and the restore's own tests check every power-loss state against one oracle
(`tests/ledger/worlds.py`).
"""

from __future__ import annotations

import errno
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

import pytest

from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Absent, Content, FileState, Fingerprint
from ledger_engine.ledger.writer import Change, NotSaved, Reason, Step, Unfinished, sha256
from tests.fakes.memfs import Crash, MemFS
from tests.mirror.fake_store import FakeStore, outside_publish

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
PER_FILE: Final = frozenset({"s_tw1", "s_tw2", "s_ts", "s_sw", "s_cmp", "s_ds", "rs_w", "rs_x", "rs_c", "rs_t"})
RESTORE: Final = frozenset(
    {"rs_chk", "rs_sync", "rs_clean", "rs_w", "rs_x", "rs_c", "rs_t", "rs_all", "rs_fl", "rs_res"}
)
SYNC_STEPS: Final = frozenset(
    {
        "S_BackupSync", "S_JPartSync", "S_JDirSync", "S_TempSync", "S_DirSync", "S_CloseSync", "RS_Sync", "RS_All",
        "RS_Flush",
    }
)  # fmt: skip
"""The durable steps: each is a sync of the code's (a file's data or a folder), so the code may never pass one
without it (round 1, verifier F2: a sync changes nothing the view shows, so the passable rule alone let a deleted sync
through)."""
FUSED_SYNC: Final = frozenset({"RS_Write", "RS_Swap"})
"""The model steps that include their folder's sync (the restore temp's rename, the restore swap): the code must make
that sync while at the step, before its next one (S0072 verifier r1 F6: a deleted sync after the restore swap
passed)."""
WRITE_PCS: Final = frozenset({"s_tw1", "s_tw2", "s_ts", "s_sw", "s_cmp", "s_ds", "s_rbk", "s_rmt"})

STOPS: Mapping[str, str] = {
    "cut": "the walk ended before the save did (not a difference)",
    "after_stop": "a later save of a trace stopped before it (the fake no longer follows the model)",
}
"""Why a save of the corpus is not replayed (the module's docstring), each counted by `test_the_stops_are_counted`."""

DISK: Mapping[str, str] = {
    "closed": "saved", "resolved": "notsaved", "open": "unfinished",
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
    """The save worlds: no boot journal (recover-all's tries, `test_recover_conformance.py`, S0091), no create and no
    copy (their replay is `test_create_conformance.py`, S0084)."""
    meta, _ = corpus()
    return sorted(
        w
        for w, c in meta["constants"].items()
        if c["BootJ"] == "{}" and c["CreateTarget"] == '"none"' and c["DoCopy"] == "FALSE"
    )


# -- the views -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    files: tuple[int, ...]
    journal: str
    temps: tuple[int, ...]
    backup: int
    rtemps: tuple[int, ...]
    kept: tuple[tuple[int, ...], ...]


def _slot(fs: State, key: str, s: str, f: str) -> Any:
    return fs[key][f"{s}/{f}"]


def model_view(st: State, s: str) -> View:
    """The model's state as the fake shows it: at a name of ours its temp, else its staging file (`_scratch`)."""
    fs = st["fs"]
    return View(
        tuple(fs["cur"][f] for f in FILES),
        st["jr"][s],
        tuple(_slot(fs, "tc", s, f) or _slot(fs, "sg", s, f) for f in ("R", "N")),
        fs["bk"][s],
        tuple(_slot(fs, "rt", s, f) for f in ("R", "N")),
        tuple(tuple(sorted(_slot(fs, "kp", s, f))) for f in ("R", "N")),
    )


def _scratch(fs: MemFS, f: str, tag: str) -> int:
    """What stands at a name of ours beside `f`: its temp, else its staging file (a write cut there is torn)."""
    for suffix in ("tmp", "new"):
        path = f"{LEDGER}/.{BASE[f]}.{tag}.{suffix}"
        if path in fs.names_in(LEDGER) or fs.file(path) is not None:
            return value(f, fs.file(path))
    return ABS


def _kept(fs: MemFS, f: str, s: str) -> tuple[int, ...]:
    """The kept copies beside `f` of the save `s` (`journal.kept_name`: `<name>.kept-<session>-<job>[-n]`)."""
    head = f"{BASE[f]}.kept-{record(s)}"
    names = [n.rpartition("/")[2] for n in fs.names_in(LEDGER)]
    return tuple(sorted(value(f, fs.file(f"{LEDGER}/{n}")) for n in names if n == head or n.startswith(head + "-")))


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
        tuple(_kept(fs, f, s) for f in ("R", "N")),
    )


# -- an episode: one save of a trace ---------------------------------------------------------------------------------


@dataclass
class Event:
    kind: str  # "edit", "kill", "io"
    file: str = ""
    value: int = 0
    after: bool = False  # an I/O error answered by a swap or exclusive rename that took place (the model's `IOFail`)


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

    def passable(self) -> bool:
        """The model's own step here changed nothing the view shows (the code may carry it out without an effect)."""
        if self.after is None:
            return True
        s = self.last["sv"]["s"]
        return model_view(self.last, s) == model_view(self.after, s)


@dataclass
class Episode:
    s: str
    plan: Mapping[str, int]
    points: list[Point]
    end: State
    killed: bool
    actions: Counter[str]
    start: State
    stop: str = ""


def _saving(st: State, job: int | None = None) -> bool:
    r = st["run"]
    return r is not None and r["k"] == "save" and (job is None or r["id"] == job)


JOB_STEP = re.compile(r"^(S_|RS_)")


def _stop(s: State, t: State) -> str:
    """Why the model's step from `s` to `t` cannot be replayed through the code (`STOPS`), or "": since S0072 the
    model's save path is the code's, so no step stops a save (the walk's cut is found at its end)."""
    del s, t
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
                p.events.append(Event("io", after=a_["ctl"]["pc"] == "s_sw" and b_["fs"]["cur"] != a_["fs"]["cur"]))
                p.step, p.after = a, b_
                if _saving(b_, job):
                    points.append(Point(b_["ctl"]["pc"], b_["sv"]["fi"], b_))
            elif a == "EngineCrash" and not _saving(b_, job):
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
        yield Episode(s, dict(st["sv"]["plan"]), points, end, killed, actions, st, stop)
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
        self.kept_pc = ""  # the step of a move to a kept name, whose folder sync follows
        self.effect = -1  # the number of the code's effect at hand (`on_effect`)
        self.ops: set[str] = set()  # the code's operations at the point it is at (`FUSED_SYNC`)
        self.compared = 0
        self.walked: Counter[str] = Counter()

    def key(self, i: int) -> tuple[str, str]:
        p = self.ep.points[i]
        return p.pc, p.file

    def compare(self, i: int) -> None:
        p = self.ep.points[i]
        want, got = model_view(p.last, self.ep.s), code_view(self.fs, self.ep.s)
        if want != got:
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
            self.synced(pc, file)
            self.walked[self.ep.points[self.at].step] += 1  # the code carried out the step of the point it was at
        self.landed = False
        self.ops = set()
        for i in range(self.at + 1, len(self.ep.points)):
            p = self.ep.points[i]
            self.at = i
            for e in p.events:
                if e.kind == "edit":
                    self.fs.outside_write(PATH[e.file], content(e.file, e.value))
                elif e.kind == "kill":
                    self.compare(i)
                    msg = f"the model's engine ended at {p.pc}"
                    raise Crash(msg)
                else:
                    self.compare(i)
                    self.walked["IOFail"] += 1  # the I/O error is raised at the code's hook
                    self.rolling = p.pc in WRITE_PCS
                    self.last_pc = p.pc  # the code was cut here, at the model's step
                    if e.after:  # the operation takes place, then answers the error
                        self.fs.inject(self.effect, crash=False, err=errno.EIO, after=True)
                        return
                    raise OSError(errno.ENOSPC, f"the model's I/O error at {p.pc}")
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
        msg = f"{self.where}: the code is at {pc} {file}, which the model's save does not reach"
        raise Divergence(msg)

    def synced(self, pc: str, file: str) -> None:
        """The code leaves the point it landed at: a step of `FUSED_SYNC` must have had its folder's sync."""
        p = self.ep.points[self.at]
        if p.step in FUSED_SYNC and "sync" not in self.ops:
            msg = f"{self.where}: the code is at {pc} {file}, past the model's {p.step} {p.file} without its sync"
            raise Divergence(msg)

    def finish(self, answered: bool) -> None:
        """The code has ended (`answered`: it returned, not cut by an engine death): every model step left must be one
        it carries out without an effect; the outside edits made there are written. Only the step of the point the
        code was at when it answered counts as walked: the steps left had no hook of the code's."""
        if answered and self.landed:
            self.synced("its answer", "")
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

    def restoring(self) -> bool:
        """The code is in the rollback: an I/O error was raised in the save's writes, the model's last step reached
        was a restore step, or the model's save goes on into its rollback from the point the code is at."""
        if self.rolling or self.last_pc in RESTORE:
            return True
        nxt = self.ep.points[self.at + 1] if self.at + 1 < len(self.ep.points) else None
        return nxt is not None and nxt.pc in RESTORE

    def classify(self, label: str) -> tuple[str, str]:
        op, _, rest = label.partition(" ")
        folder, _, names = rest.partition(":")
        name = record(self.ep.s)
        last = self.last_pc
        if folder == f"{BD}/{name}":  # the model backs up R only (N did not exist in its first save)
            return ("s_bks", "") if op == "sync" else ("s_bk", "")  # the copy (data synced), then its folder's sync
        if op == "sync_file" and folder == LEDGER and self.restoring():
            return "rs_fl", ""  # step 4's flush of each file found at pre (S0101, BL-0243)
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
                if self.restoring():
                    return last, self._file_of(last)
                return {"s_jo": "s_jds", "s_close": "s_csync"}.get(last, last), self._file_of(last)
        if folder == LEDGER:
            return self._ledger(op, names.split(","), name, last)
        msg = f"{self.where}: an effect the replay cannot place: {label}"
        raise Divergence(msg)

    def _ledger(self, op: str, names: list[str], name: str, last: str) -> tuple[str, str]:
        """An effect in the ledger folder, by the model step it belongs to."""
        if op == "sync" and self.kept_pc:  # the folder's sync after a move to a kept name: the same step
            pc, self.kept_pc = self.kept_pc, ""
            return pc, self._file_of(pc)
        kept = len(names) > 1 and f".kept-{name}" in names[1]
        if kept:  # an outside save moved to its kept name (§6.4): by the step that met it
            if not self.restoring():
                pc = "s_cmp" if last == "s_sw" else "s_rmt"
            else:
                pc = {"rs_x": "rs_c", "rs_c": "rs_t"}.get(last, "rs_chk")
            self.kept_pc = pc
            return pc, self._file_of(pc) if pc in PER_FILE else ""
        found = re.fullmatch(rf"\.(.+)\.ft-(restore-)?{re.escape(name)}\.(new|tmp)", names[0])
        if self.restoring():
            return self._restore_effect(op, found, last)
        if found is not None:
            f, suffix = FILE_OF[found.group(1)], found.group(3)
            if op == "create":
                return "s_tw1", f
            if op == "write":
                return "s_tw2", f
            if op in ("fsync", "rename") and suffix == "new":
                return "s_tw2", f  # written and synced at the staging name, renamed to the temp name...
            if op in ("swap", "rename"):
                return "s_sw", f
            if op == "remove":
                return "s_rmt", ""
        if op == "sync":
            if last == "s_tw2":
                return "s_ts", self._file_of(last)  # ...and that name made durable (`S_TempSync`)
            if last == "s_sw":
                return "s_ds", self._file_of(last)
            return last, self._file_of(last)
        msg = f"{self.where}: a ledger-folder effect the replay cannot place: {op} {names}"
        raise Divergence(msg)

    def _restore_effect(self, op: str, found: re.Match[str] | None, last: str) -> tuple[str, str]:
        """An effect of the rollback's restore (`ledger.restore`) in the ledger folder: the folders synced first
        (`RS_Sync`), the names of ours removed (`RS_Clean`), per file put back the restore temp written (`RS_Write`),
        swapped (`RS_Swap`) and what it displaced settled (`RS_Compare`), the save's temp removed (`RS_Temp`), the
        folders synced (`RS_All`)."""
        if op == "sync":
            if last in ("rs_w", "rs_x"):
                return last, self._file_of(last)
            if last in ("rs_clean", "rs_c", "rs_t", "rs_sync"):
                return "rs_all", ""
            return "rs_sync", ""
        if found is not None and found.group(2):  # a restore name
            if op == "swap":
                return "rs_x", self._file_of("rs_w")
            if op == "remove" and last == "rs_x":
                return "rs_c", self._file_of(last)
            if op in ("create", "write", "fsync", "rename"):
                return "rs_w", self._next_put()
        if op == "remove" and last == "rs_c":
            return "rs_t", self._file_of(last)
        if op == "remove":
            return "rs_clean", ""
        msg = f"{self.where}: a restore effect the replay cannot place: {op} at {last}"
        raise Divergence(msg)

    def _next_put(self) -> str:
        """The file of the model's next put-back (`rs_w`) from the point the code is at."""
        if self.last_pc == "rs_w":
            return self.key(self.at)[1]
        return next((p.file for p in self.ep.points[self.at + 1 :] if p.pc == "rs_w"), "")

    def _file_of(self, pc: str) -> str:
        return self.key(self.at)[1] if pc in PER_FILE and self.at >= 0 else ""

    def on_effect(self, i: int, label: str) -> None:
        self.effect = i
        pc, f = self.classify(label)
        self.reach(pc, f)
        self.ops.add(label.partition(" ")[0])


class Job:
    def __init__(self, driver: Driver) -> None:
        self.driver = driver

    def progress(self, phase: str) -> None:
        pass

    def gate(self) -> None:
        """The commit point, in the model the end of `S_Gate` (reached at the observation): the code's next effect
        is the model's backup step (an I/O error the model makes there is raised at that effect, inside the save)."""

    def committed(self) -> None:
        self.driver.reach("s_saved")


def _state_of(fs: MemFS, path: str) -> FileState:
    data = fs.file(path)
    return Absent() if data is None else Content(sha256(data))


def ports(fs: MemFS, driver: Driver) -> sv.Ports:
    seen = Counter[str]()

    def observe(fp: Fingerprint) -> Fingerprint:
        """The save's observations at its gate (step 4): the plan's set (the model's `S_Gate`), then the published
        mirror's own set (S0087; the engine-jobs model has no mirror: a generation of this ledger's root published
        from no file, so it is always fresh and the check never decides a replay)."""
        seen["observe"] += 1
        if not fp.files:  # the published generation's own set (`world`): no file, always fresh
            return fp
        driver.reach("s_gate")
        files = tuple((p, _state_of(fs, p)) for p, _ in fp.files)
        return Fingerprint(fp.root, files, fp.globs, fp.documents, fp.document_files)

    return sv.Ports(fs, observe, store=FakeStore())


def plan_of(ep: Episode, fs: MemFS) -> sv.SavePlan:
    def state(f: str) -> FileState:
        v = ep.plan[f]
        return Absent() if v == ABS else Content(sha256(content(f, v)))

    def pre(f: str) -> str | None:
        v = ep.plan[f]
        return None if v == ABS else sha256(content(f, v))

    fp = Fingerprint.of(PATH["R"], {PATH[f]: state(f) for f in FILES}, {}, {}, {})
    changes = tuple(Change(PATH[f], pre(f), content(f, POST[ep.s])) for f in WORD)
    identity = fs.resolve(ALD).identity  # the app folder of the start (S0102)
    assert identity is not None
    return sv.SavePlan(SESSION, ep.s, KEY, "lin1", "b", PATH["R"], changes, fp, "/cache", ALD, identity)


def disk_answer(end: State, s: str, plan: Mapping[str, int]) -> str:
    """§6.6's answer from the model's disk at the save's end: its record by `DISK` (the model's `DiskOutcome`), else
    the re-hash of the files against the plan's pre bytes."""
    st = end["jr"][s]
    if st in DISK:
        return DISK[st]
    return "unchanged" if all(end["fs"]["cur"][f] == plan[f] for f in WORD) else "changed"


def told_on_disk(told: str, end: State, s: str, plan: Mapping[str, int]) -> bool:
    """The code's answer `told` is one the model's disk at the save's end allows (the module's docstring)."""
    disk = disk_answer(end, s, plan)
    ours = any(end["fs"]["cur"][f] == POST[s] for f in WORD)  # post bytes of ours in place
    match disk:
        case "saved":  # .closed (Unfinished too, the cautious answer, when the close's sync failed: `Unfinished`)
            return told in ("saved", "unfinished")
        case "notsaved":  # resolved: the rollback put everything back (an I/O error, or a change met); Unfinished
            return told in ("notsaved", "changed", "unfinished")  # when only the rollback's last sync failed
        case "unfinished":  # .open: Unfinished, or the rollback stopped before anything was replaced
            return told == "unfinished" or (told in ("notsaved", "changed") and not ours)
        case _:  # no record or a .part: the re-hash, or a refusal (whose `unchanged` `replay_save` checks)
            return told in (disk, "notsaved")


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
    outside_publish(fs, Fingerprint(PATH["R"], (), (), (), ()), KEY)
    return fs


def sync_world(fs: MemFS, st: State, where: str) -> None:
    """The fake made to hold what the model holds between two saves: the files (an outside edit while no save ran);
    a record that differs is a divergence (nothing renames a closed record)."""
    for f in FILES:
        v = st["fs"]["cur"][f]
        if value(f, fs.file(PATH[f])) != v:
            assert v > 0, f"{where}: the model removed {f} between saves"
            fs.outside_write(PATH[f], content(f, v))
    for s in SAVES:
        want, got = st["jr"][s], journal_state(fs, s)
        if want == got:
            continue
        msg = f"{where}: between saves the model's record of {s} is {want}, the fake's {got}"
        raise Divergence(msg)


def replay_save(fs: MemFS, ep: Episode, where: str, tally: Tally) -> None:
    driver = Driver(fs, ep, where)
    plan = plan_of(ep, fs)
    fs.on_effect = driver.on_effect
    out: sv.SaveOutcome | None = None
    try:
        out = sv.run(plan, ports(fs, driver), Job(driver))
    except Crash:
        assert ep.killed, f"{where}: the code's engine died where the model's did not"
    finally:
        fs.on_effect = None
    driver.finish(answered=out is not None)
    told = told_of(out, fs, plan)
    if isinstance(out, sv.Refused):  # "unchanged" is said only when the targets equal their pre bytes (§11)
        fs_end = ep.end["fs"]["cur"]
        assert out.unchanged == all(fs_end[f] == ep.plan[f] for f in WORD), f"{where}: {out}, the model's {fs_end}"
    disk = disk_answer(ep.end, ep.s, ep.plan)
    if ep.killed:  # §6.6: the engine's reading of the disk is the disk's answer
        if out is not None:  # the model's end came after the code's last hook: read the disk as it does
            told = told_of(None, fs, plan)
        assert told == disk, f"{where}: the code tells {told!r} ({out}), the disk {disk!r} ({ep.end['jr'][ep.s]})"
    else:
        assert told_on_disk(told, ep.end, ep.s, ep.plan), (
            f"{where}: the code tells {told!r} ({out}), which the disk does not allow: {disk!r} "
            f"({ep.end['jr'][ep.s]}, files {ep.end['fs']['cur']})"
        )
    want, got = model_view(ep.end, ep.s), code_view(fs, ep.s)
    assert want == got, f"{where}: after the save the fake holds\n  {got}\nwhere the model has\n  {want}"
    tally.saves += 1
    tally.compared += driver.compared + 1
    tally.walked.update(driver.walked)
    tally.told[told] += 1
    tally.branches.update(branches(ep, out, told))


def branches(ep: Episode, out: sv.SaveOutcome | None, told: str) -> Iterator[str]:
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
        if early and told == "changed":
            yield "an I/O error before the journal is open, after an outside edit: not saved, changed"
    if ep.actions["RS_Swap"]:
        yield "the rollback puts pre bytes back"
    end_kept = any(ep.end["fs"]["kp"][f"{ep.s}/{f}"] for f in ("R", "N"))
    if end_kept:
        yield "an outside save moved to its kept name"
    if isinstance(out, Unfinished) or (out is None and told == "unfinished"):
        yield "Unfinished: the rollback could not resolve"
    swap_io = [e for p in ep.points if p.pc == "s_sw" for e in p.events if e.kind == "io"]
    if any(e.after for e in swap_io):
        yield "an I/O error answered by a swap or exclusive rename that took place"
    if swap_io and isinstance(out, Unfinished):
        yield "an I/O error at a swap or exclusive rename, the rollback stopped: Unfinished"
    if out is not None and getattr(out, "cause", None) == "created_path_taken":
        yield "a file at the path the save creates is never touched"
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
    "an outside save moved to its kept name",
    "Unfinished: the rollback could not resolve",
    "a file at the path the save creates is never touched",
    "an I/O error answered by a swap or exclusive rename that took place",
    "an I/O error at a swap or exclusive rename, the rollback stopped: Unfinished",
)
"""The save's branches the corpus must walk through the code (S0064)."""


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
    replay does not walk is named with its reason (`NOT_WALKED`)."""
    tally = every_world()
    assert set(tally.walked) <= set(WALKED), f"counted as walked, with no hook of the code's: {dict(tally.walked)}"
    missing_steps = [a for a in WALKED if tally.walked[a] == 0]
    assert not missing_steps, f"the replay never walks {missing_steps}: widen the save worlds in gen_traces.py"
    missing = [b for b in BRANCHES if tally.branches[b] == 0]
    assert not missing, f"the replay never walks {missing}: widen the save worlds in gen_traces.py"
    assert tally.saves >= 50, tally


WALKED: tuple[str, ...] = (
    "S_Gate", "S_Backup", "S_BackupSync", "S_JPart", "S_JPartSync", "S_JOpen", "S_JDirSync", "S_TempStart",
    "S_TempDone", "S_TempSync", "S_Swap", "S_Compare", "S_DirSync", "S_RemoveTemps", "S_Close", "S_CloseSync",
    "S_Saved", "RS_Check", "RS_Sync", "RS_Clean", "RS_Write", "RS_Swap", "RS_Compare", "RS_Temp", "RS_All",
    "RS_Flush", "RS_Resolve", "IOFail",
)  # fmt: skip
"""The model's save steps the replay walks through the code: the code reached the step at one of its hooks and went
on (`Driver.reach`, `finish`), or the I/O error raised at a hook. A step the model passes without a hook of the code's
is not counted, so deleting the code that carries out one of these (a folder's sync, say) fails
`test_the_replay_walks_the_save_path`. Each is marked `@tla_action` on that code
(`backend/tests/architecture/test_tla_mapping.py`). `S_Compare` and `RS_Check` have a hook only when they move an
outside save to its kept name (otherwise they only read): the kept-name worlds walk them so."""

NOT_WALKED: Mapping[str, str] = {
    "S_ReadBack": "a read, with no hook; `test_writer.py` checks its stopping branch",
    "RS_Late": "a seeded bug's step only (`resolve_before_restores_synced`): the folders' sync after the resolve",
}
"""The model's save steps the replay does not count as walked, each with its reason (`PARTIAL` or `PLANNED` in the
code map; S0064 round 1, verifier F2)."""


def test_the_save_steps_are_the_models() -> None:
    """`WALKED` and `NOT_WALKED` together are the model's save steps (its `S_*`, `RS_*` and `IOFail`), each in
    one of them: a step added to the model fails here until it is placed."""
    model = (REPO / "formal" / "tla" / "engine-jobs" / "EngineJobs.tla").read_text(encoding="utf-8")
    steps = set(re.findall(r"^((?:S|RS)_[A-Za-z0-9]+) ==", model, re.MULTILINE)) | {"IOFail"}
    assert not set(WALKED) & set(NOT_WALKED)
    assert set(WALKED) | set(NOT_WALKED) == steps


STOP_COUNTS: Mapping[str, int] = {}  # S0101: the regenerated corpus has no save the walk cuts
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


def test_a_rollback_without_its_flush_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RS_Flush` (S0101, BL-0243): a rollback that resolves without flushing the data of a file it found at pre goes
    past the model's flush without its sync. Must not pass with the step left out of `SYNC_STEPS`."""
    from ledger_engine.ledger import restore

    def unflushed(*args: object) -> None:
        del args

    monkeypatch.setattr(restore, "_flush", unflushed)
    assert re.search(r"the model's RS_Flush .*without its sync", _first_failure())


def test_a_skipped_restore_sync_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The restore's syncs inside one model step (`FUSED_SYNC`; S0072 verifier r1 F6): a restore that does not sync
    the folder after its swap is caught, though nothing the view shows differs. Must not pass on 95580fa2, where only
    `test_restore.py`'s sweep caught it."""
    from ledger_engine.ledger import restore

    put_back = restore._put_back  # pyright: ignore[reportPrivateUsage]

    def put_back_unsynced(handles: Any, record: Any, x: Any) -> None:
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
            put_back(handles, record, x)
        finally:
            del h.swap, h.sync

    monkeypatch.setattr(restore, "_put_back", put_back_unsynced)
    assert re.search(r"past the model's RS_Swap [RN] without its sync", _first_failure())


def test_what_the_model_told_is_not_compared(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer is checked against the model's disk, never against `gh.told`, whose truth no property of the
    durability model checks (EP-19; S0072 verifier r1 F1): with every told value of the corpus made "saved", the
    replay still passes. Must not pass on 95580fa2, which compared the code's answer with `gh.told`."""
    meta, traces = corpus()

    def forge(st: State) -> State:
        return {**st, "told": dict.fromkeys(st["told"], "saved")} if "told" in st else st

    forged = tuple({**t, "states": [forge(st) for st in t["states"]]} for t in traces)
    assert any(st != forge(st) for t in traces for st in t["states"])
    monkeypatch.setattr(sys.modules[__name__], "corpus", lambda: (meta, forged))
    assert _first_failure() == ""


def test_an_answer_the_disk_refutes_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer against the model's disk (`told_on_disk`): a save that says "saved" where it ended Unfinished is
    caught, with the record still `.open`."""
    run = sv.run

    def unfinished_said_saved(plan: sv.SavePlan, ports: sv.Ports, job: Any) -> sv.SaveOutcome:
        out = run(plan, ports, job)
        if isinstance(out, Unfinished):
            return sv.Saved(cast(Any, None))
        return out

    monkeypatch.setattr(sv, "run", unfinished_said_saved)
    assert re.search(r"the code tells 'saved' .*which the disk does not allow: 'unfinished'", _first_failure())


def test_a_landed_save_told_not_saved_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A save that landed (its record `.closed`) and says "not saved" is caught."""
    run = sv.run

    def saved_said_not_saved(plan: sv.SavePlan, ports: sv.Ports, job: Any) -> sv.SaveOutcome:
        out = run(plan, ports, job)
        return NotSaved(Step.REPLACE, Reason.IO_ERROR, False) if isinstance(out, sv.Saved) else out

    monkeypatch.setattr(sv, "run", saved_said_not_saved)
    assert re.search(r"the code tells 'notsaved' .*which the disk does not allow: 'saved'", _first_failure())


def test_a_resolved_rollback_told_saved_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A save whose rollback resolved its record (every file back at pre) and says "saved" is caught."""
    run = sv.run

    def resolved_said_saved(plan: sv.SavePlan, ports: sv.Ports, job: Any) -> sv.SaveOutcome:
        out = run(plan, ports, job)
        late = isinstance(out, NotSaved) and out.step not in (Step.BACKUP, Step.JOURNAL) and not out.open_journal
        return sv.Saved(cast(Any, None)) if late else out

    monkeypatch.setattr(sv, "run", resolved_said_saved)
    assert re.search(r"the code tells 'saved' .*which the disk does not allow: 'notsaved'", _first_failure())


def test_not_saved_over_our_post_bytes_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rollback that stopped with post bytes of ours in place (a swap that took place, a created file) and says
    "not saved" with the journal open, not Unfinished, is caught."""
    run = sv.run

    def unfinished_said_not_saved(plan: sv.SavePlan, ports: sv.Ports, job: Any) -> sv.SaveOutcome:
        out = run(plan, ports, job)
        return NotSaved(Step.REPLACE, Reason.IO_ERROR, False, True) if isinstance(out, Unfinished) else out

    monkeypatch.setattr(sv, "run", unfinished_said_not_saved)
    assert re.search(r"the code tells 'notsaved' .*which the disk does not allow: 'unfinished'", _first_failure())


def test_a_wrong_reading_after_an_engine_death_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reading after an engine death against the disk's answer (§6.6): an outcome reading that takes a `.closed`
    record for an `.open` one is caught."""
    from dataclasses import replace as copy

    read = sv.outcome

    def closed_read_as_open(*args: Any) -> sv.OutcomeFacts:
        facts = read(*args)
        return copy(facts, record="open") if facts.record == "closed" else facts

    monkeypatch.setattr(sv, "outcome", closed_read_as_open)
    assert re.search(r"the code tells 'unfinished' .*, the disk 'saved' \(closed\)", _first_failure())


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
    """The answer against the model's disk: a writer whose "not saved" always says the files are unchanged (no
    re-hash) is caught where an outside edit came before an I/O error (`S_save_io_edit`)."""
    from ledger_engine.ledger import writer

    def always_unchanged(handles: object, targets: object) -> tuple[bool, str | None]:
        del handles, targets
        return True, None

    monkeypatch.setattr(writer, "_rehash", always_unchanged)  # `_unchanged` before S0100
    assert "the code tells 'unchanged'" in _first_failure()


def test_the_disk_outcome_table_is_the_models() -> None:
    """`DISK` restates the model's `DiskOutcome` (a `.closed` record is saved, `.resolved` not
    saved, `.open` unfinished); the engine's `kept` is an `.open` journal whose kept copy stops it (§6.4)."""
    model = (REPO / "formal" / "tla" / "engine-jobs" / "EngineJobs.tla").read_text(encoding="utf-8")
    assert 'DiskOutcome(s) == CASE jr[s].st \\in CL -> "saved"' in model
    assert 'CL       == {"closed"}' in model
    assert '[] jr[s].st = "resolved" -> "notsaved"' in model
    assert {k for k, v in DISK.items() if v == "saved"} == {"closed"}
    assert DISK["resolved"] == "notsaved"
    assert DISK["open"] == DISK["kept"] == "unfinished"


def test_no_save_path_step_stops_the_replay() -> None:
    """S0072 (BL-0161): the model's save path is the code's, so every save the walk did not cut is replayed: the
    kept-name branches (an outside save at the swap, at the rollback's check), the created path never touched and the
    backup step that reads the file included. Must not pass on fcf96576, whose replay stopped at `swap_back`,
    `restore_swap_back`, `temp_rule`, `created_path` and `backup_check`."""
    assert set(STOPS) == {"cut", "after_stop"}
    tally = every_world()
    assert tally.branches["an outside save moved to its kept name"] > 0
    assert tally.branches["a file at the path the save creates is never touched"] > 0


def test_a_model_value_round_trips() -> None:
    for f in FILES:
        for v in (1, 3, 12, 25):
            assert value(f, content(f, v)) == v
        assert value(f, None) == ABS
        assert value(f, b"") == TORN
        assert value(f, b"; X 3\n") == TORN
    assert value("R", content("N", 3)) == TORN
