#!/usr/bin/env python3
"""Write the conformance trace corpus of the engine-jobs model (S0059; BL-0150; design/specs/engine-jobs.md §13, §14).

    python3 formal/tla/engine-jobs/gen_traces.py            # regenerate traces/corpus.json (needs Java 21)
    python3 formal/tla/engine-jobs/gen_traces.py --check    # exit 1 if the corpus is stale (no Java)

TLC's simulate mode (`-simulate`, a fixed `-seed` and `-depth`, one worker, so the same corpus every time) walks the
small worlds named in WORLDS below, with each world's constants read from its committed `cfg/<world>.cfg`. The walk
uses `EngineJobsTrace`, a module this script writes next to a copy of the model in a temporary folder; it changes the
model in two ways only, and every step it takes is a step of the model's `Next`, so every trace is a behaviour of
`Spec`:

- each step is labelled with its action (`act`), so the replay knows what happened;
- the app's own dispatch (`Dispatch`, `DispatchUnavail`, `SpawnStep`) is taken first whenever it is enabled: the
  queue table dispatches inside the step of the event that allows it (§3), so a model interleaving in which the
  user acts between that event and the dispatch has no counterpart in the code;
- the actions in OUT are left out (their behaviour belongs to later pieces; the replay could only stop there).

The corpus keeps, per state, only the variables the replays read (`KEEP_VARS`: the queue's, and around saves the
file system, the records and what the user was told, `prune`; `engine/tests/ledger/test_save_conformance.py` replays
the saves through the engine), each trace as deltas, and records the SHA-256 of the model, of each world's `.cfg`, of
this script and of the TLA+ tools, so `backend/tests/platform/test_engine_jobs_conformance.py` fails on a stale
corpus with this command in its message. The reader of TLC's trace values below reads only the shapes those
variables use (records, sequences, sets, functions, strings, integers, booleans) and refuses anything else.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODEL = HERE / "EngineJobs.tla"
CFG = HERE / "cfg"
JAR = REPO / "formal" / "tla" / "tools" / "tla2tools.jar"
LOCK = REPO / "formal" / "tla" / "tools.lock"
CORPUS = HERE / "traces" / "corpus.json"
COMMAND = "python3 formal/tla/engine-jobs/gen_traces.py"

# The small worlds walked, each a committed world (`cfg/<base>.cfg`) with a few constants changed (`set`) and some
# branches left out (`leave`, names in LEAVE): the queue, Cancel's one rule and the engine; the engine lock at boot;
# the stop bound, its window and Try again. `traces` are kept, of at most `depth` states, each with at least `least`
# states: TLC walks CANDIDATES times as many and, of the long enough ones, those that add the most branches are kept
# (`select`; a world runs out of things to do early when the boot is cancelled, say). Kept small: the test replays every trace through the table and through
# the shell in seconds.
WORLDS: dict[str, dict[str, Any]] = {
    "S_queue": {"base": "S_queue", "traces": 12, "depth": 50, "least": 20, "leave": ["boot_cancel"]},
    # every job runs to its end: a save's R4 queued behind a background save (the oldest background job first)
    "S_queue_done": {
        "base": "S_queue", "set": {"MaxCrash": "0"}, "traces": 6, "depth": 60, "least": 30, "leave": ["cancel"],
    },
    "S_cancel": {"base": "S_cancel", "traces": 12, "depth": 40, "least": 14, "leave": []},
    # S0064: a closed tab while its read is queued
    "S_close": {"base": "S_cancel", "traces": 6, "depth": 40, "least": 10, "leave": ["boot_cancel", "close_queued_read"]},
    "S_recover_lock": {"base": "S_recover_lock", "traces": 8, "depth": 40, "least": 2, "leave": []},
    # saves that finish and their R4 (S0068: a background job with no disk effect)
    "S_r4_fresh": {
        "base": "S_r4_ends", "set": {"MaxCrash": "0", "MaxPower": "0", "MaxReads": "0"}, "traces": 6, "depth": 80,
        "least": 30, "leave": ["cancel"],
    },
    # R4's engine death, and the window letting that one stop go below the bound (S_r4_attempts walked it before S0068)
    "S_r4_crash": {
        "base": "S_r4_ends", "set": {"MaxPower": "0", "MaxReads": "0", "MaxExpire": "1"}, "traces": 8, "depth": 80,
        "least": 16,
        "leave": ["cancel", "crash_in_r4"],
    },
    # one stop makes the engine unavailable: the queued jobs end without one, the window, Try again
    "S_queue_bound1": {
        "base": "S_queue", "set": {"StopBound": "1", "MaxExpire": "1"}, "traces": 10, "depth": 50, "least": 16,
        "leave": ["cancel"],
    },
    # BL-0159 (S0064): a Cancel of a queued read meeting a background save, before and past its commit point
    "S_cancel_bg": {
        "base": "S_queue", "set": {"MaxSaves": "0", "MaxCrash": "0", "MaxEdits": "0"}, "traces": 10, "depth": 50,
        "least": 16, "leave": ["cancel_behind_bg_save"],
    },
    # BL-0159: a Cancel of a queued save meeting a running R4 (R4 is ended by another's Cancel)
    "S_cancel_r4": {
        "base": "S_r4_ends", "set": {"MaxCrash": "0", "MaxPower": "0", "MaxReads": "0"}, "traces": 8, "depth": 80,
        "least": 30, "leave": ["cancel_behind_r4", "save_behind_r4"],
    },
    # BL-0151 slice 5, BL-0159: recover-all's listing, a Cancel after it, an engine death, the fresh engine and,
    # with the bound reached, its unavailable branch (two boot journals; StopBound 2)
    "S_recover_bound": {"base": "S_recover_bound", "traces": 10, "depth": 40, "least": 8, "leave": []},
    "S_recover": {"base": "S_recover", "traces": 8, "depth": 40, "least": 8, "leave": []},
    # BL-0151 slice 5: the save path replayed through the engine's save over the in-memory file system: outside
    # edits, an engine death and an I/O error anywhere in the save (one save; then two, the second replacing the
    # file the first created, so its rollback restores both)
    "S_save": {
        "base": "S_save_crash", "set": {"MaxEdits": "0"}, "traces": 16, "depth": 60, "least": 10,
        "leave": ["cancel", "crash_in_save"],
    },
    "S_save_edit": {
        "base": "S_save_crash", "set": {"MaxIO": "0"}, "traces": 12, "depth": 60, "least": 16,
        "leave": ["cancel", "edit_before_commit", "crash_in_save"],
    },
    "S_save_edit_late": {
        "base": "S_save_crash", "set": {"MaxIO": "0"}, "traces": 12, "depth": 60, "least": 16,
        "leave": ["cancel", "edit_past_commit", "crash_past_commit"],
    },
    # a Cancel of a save (its own, or of a read queued behind it) before or past its commit point, outside edits
    "S_save_cancel": {"base": "S_cancel_commit", "traces": 12, "depth": 50, "least": 8, "leave": ["boot_cancel"]},
    "S_save_io": {
        "base": "S_save_crash", "set": {"MaxCrash": "0", "MaxSaves": "2", "MaxEdits": "0", "MaxReads": "0"},
        "traces": 16, "depth": 90, "least": 30, "leave": ["cancel", "save_after_r4"],
    },
    # the second save's faults: it replaces the file the first created, so its rollback restores both files; a third
    # save is refused while the second's journal is open
    "S_save_two": {
        "base": "S_save_crash", "set": {"MaxSaves": "3", "MaxEdits": "0", "MaxReads": "0", "MaxIO": "1"},
        "traces": 16, "depth": 120, "least": 40, "leave": ["cancel", "save_after_r4", "second_save_faults"],
    },
    "S_save_restore": {
        "base": "S_save_crash", "set": {"MaxSaves": "2", "MaxEdits": "0", "MaxReads": "0", "MaxCrash": "0"},
        "traces": 10, "depth": 100, "least": 40, "leave": ["cancel", "save_after_r4", "second_save_faults",
                                                           "io_after_first_swap"],
    },
    # round 1 (verifier F3): an outside edit of a file the save writes, after its backup, then an I/O error before the
    # journal is open, so the save ends in the engine with "not saved, changed" (the re-hash, §11)
    "S_save_io_edit": {
        "base": "S_save_crash", "set": {"MaxCrash": "0", "MaxReads": "0", "EdFiles": '{"R", "N"}'}, "traces": 10, "depth": 60,
        "least": 10, "leave": ["cancel", "edit_after_backup", "io_after_edit"],
    },
    # an engine death after the journal's close (the save has landed: told "saved" from the disk)
    "S_save_late_crash": {
        "base": "S_save_crash", "set": {"MaxIO": "0", "MaxEdits": "0"}, "traces": 6, "depth": 60, "least": 20,
        "leave": ["cancel", "crash_after_close"],
    },
    # an engine death past the commit point (S0068: least 24, as no R4 runs after the death any more and most walks
    # end at its drain, 22-38 states)
    "S_save_io_crash": {
        "base": "S_save_crash", "set": {"MaxSaves": "2", "MaxEdits": "0", "MaxReads": "0"}, "traces": 16,
        "depth": 90, "least": 24, "leave": ["cancel", "crash_past_commit", "save_after_r4"],
    },
}  # fmt: skip
CANDIDATES = 20
SEED = 20261029
EAGER = ("Dispatch", "DispatchUnavail", "SpawnStep")
OUT = frozenset(
    {
        # BL-0153: create and copy (their jobs never start, so their steps never run)
        "UserCreate", "UserCopy",
        "CP_Check", "CP_Load", "CP_Commit", "CP_Manifest",
        "CR_Target", "CR_WriteA", "CR_SyncA", "CR_ReadBack", "CR_Load", "CR_Commit",
        "CR_ManifestTemp", "CR_ManifestRename", "CR_ManifestSync", "CR_Register",
        # BL-0154: the switch, Reopen, the screens, quit, update, the next start; the app's crash and a power loss
        # end the session, whose next start is the boot's
        "UserSwitch", "UserStay", "UserRetry", "UserBack", "UserOpenAnother", "UserKeep",
        "UserQuit", "UserQuitAnyway", "Relaunch", "CloseA", "QuitClose", "AppCrash", "PowerLoss",
    }
)  # fmt: skip
# Branches of an action that belong to a later piece, left out the same way (the replay stops on each, should one
# ever reach it).
GUARDS: dict[str, str] = {
    # BL-0152: the open job's engine death ends the switch on its failed screen (recover-all's death and its Cancel
    # after the listing are walked since S0064, BL-0151 slice 5)
    "EngineCrash": '~(Busy /\\ RJ.k = "open")',
    # BL-0152: the open job (the boot's open of a workspace whose mirror is out of date, as after an outside edit
    # during the boot)
    "Assess": '~(\\E i \\in 1..Len(q\') : q\'[i].k = "open")',
    "Editor": 'ses.app # "boot"',
}
# Branches a world may leave out so that its traces reach further (each trace is still a behaviour of Spec): the
# boot's own Cancel (a cancelled boot opens nothing, and its world has nothing left to do), or every Cancel (a save
# then reaches its R4).
LEAVE: dict[str, dict[str, str]] = {
    "boot_cancel": {"UserCancelRunning": '~(Busy /\\ RJ.own = "switch")', "UserCancelQueued": 'q[i].own # "switch"'},
    "cancel": {"UserCancelRunning": "FALSE", "UserCancelQueued": "FALSE"},
    # only the Cancels that meet a running background save (S0064, BL-0159), or a running R4
    "cancel_behind_bg_save": {
        "UserCancelRunning": "FALSE", "UserCancelQueued": 'Busy /\\ RJ.k = "save" /\\ ~RJ.wf',
    },
    "cancel_behind_r4": {"UserCancelRunning": "FALSE", "UserCancelQueued": 'Busy /\\ RJ.k = "r4"'},
    # outside edits only while a save runs, before or past its commit point (S0064: an edit early in a walk makes
    # every save refuse at its gate)
    "edit_before_commit": {"Editor": 'Busy /\\ RJ.k = "save" /\\ ~ctl.past'},
    "edit_past_commit": {"Editor": 'Busy /\\ RJ.k = "save" /\\ ctl.past'},
    # an edit after the save's backup and before its journal is open, and an I/O error only after the edit and before
    # the journal is open (round 1, verifier F3: the re-hash then finds the edit)
    "edit_after_backup": {"Editor": 'Busy /\\ RJ.k = "save" /\\ ctl.pc \\in {"s_bks", "s_jp", "s_jps"}'},
    "io_after_edit": {"IOFail": 'ctl.pc \\in PreJournalPcs /\\ n.ed = MaxEdits'},
    # an engine death only where a world wants it (S0064: with one death allowed, recover-all at boot took it first)
    "crash_in_r4": {"EngineCrash": 'RJ.k = "r4"'},
    "crash_in_save": {"EngineCrash": 'RJ.k = "save"'},
    "crash_past_commit": {"EngineCrash": 'RJ.k = "save" /\\ ctl.past'},
    "crash_after_close": {"EngineCrash": 'RJ.k = "save" /\\ ctl.pc \\in {"s_csync", "s_saved", "s_pub", "s_rel"}'},
    # a second save only while an R4 runs, so a Cancel of it meets the R4 (S0064)
    "save_behind_r4": {"UserSave": 'n.save = 0 \\/ (Busy /\\ RJ.k = "r4")'},
    # a second save only once the first has ended (S0064: a save asked for while the first runs is planned on the
    # mirror from before it, so its gate refuses it; S0068: no R4 runs inside a save any more)
    "save_after_r4": {"UserSave": 'n.save = 0 \\/ ~Busy'},
    # faults only in the second save (S0064)
    "second_save_faults": {"IOFail": 'ctl.s = "s2"', "EngineCrash": 'RJ.k = "save" /\\ ctl.past /\\ ctl.s = "s2"'},
    # an I/O error in the second save only once it has replaced its first file, so its rollback puts pre bytes back
    "io_after_first_swap": {"IOFail": 'ctl.s = "s2" /\\ fs.cur["N"] = PostV("s2")'},
    # a closed tab only while its read is queued (S0064)
    "close_queued_read": {"UserCloseTab": '\\E i \\in 1..Len(q) : q[i].id = id /\\ q[i].k = "read"'},
}  # fmt: skip
KEEP_VARS = ("act", "q", "runs", "eng", "ctl", "ses", "pst", "fs", "jr", "gh")

# -- the model's Next, read from its source --------------------------------------------------------------------------

GROUPS = ("EngineStep", "AppStep", "UserStep", "EnvStep")
DISJUNCT = re.compile(r"^((?:\\E\s+[a-z]+\s+\\in\s+[^:]+:\s*)?)([A-Za-z_][A-Za-z0-9_]*)(\(([^()]*)\))?$")


def _body(model: str, name: str) -> str:
    lines = model.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(rf"{name}\s*==", line))
    body = [lines[start].split("==", 1)[1]]
    for line in lines[start + 1 :]:
        if not line.strip() or not line[0].isspace():
            break
        body.append(line)
    return " ".join(re.sub(r"\\\*.*$", "", line) for line in body)


def disjuncts(model: str, group: str) -> list[tuple[str, str, str]]:
    """(binder, action, argument) for each disjunct of a group of `Next`: `\\E w \\in WS : UserRead(w)` gives
    (`\\E w \\in WS :`, `UserRead`, `w`). A disjunct it cannot read is refused, never dropped."""
    out = []
    for part in (p.strip() for p in _body(model, group).split("\\/")):
        if not part:
            continue
        found = DISJUNCT.match(part)
        if found is None:
            raise SystemExit(f"{group}: cannot read the disjunct {part!r}")
        out.append((found.group(1).strip(), found.group(2), found.group(4) or ""))
    return out


def actions(model: str) -> list[tuple[str, str, str]]:
    return [d for group in GROUPS for d in disjuncts(model, group)]


def wrapper(model: str, leave: list[str]) -> str:
    """The trace module: the model's Next with each step labelled, the dispatch first, OUT left out, and the guarded
    branches left out."""
    guards = {n: [g] for n, g in GUARDS.items()}
    for name in leave:
        for n, g in LEAVE[name].items():
            guards.setdefault(n, []).append(g)

    def labelled(binder: str, name: str, arg: str) -> str:
        param = arg if arg else "0"
        call = f"{name}({arg})" if arg else name
        for guard in guards.get(name, []):
            call = f"{call} /\\ ({guard})"
        return f"({binder} {call} /\\ act' = [a |-> \"{name}\", p |-> {param}])".replace("( ", "(")

    every = actions(model)
    names = {name for _, name, _ in every}
    unknown = OUT - names
    if unknown:
        raise SystemExit(f"OUT names actions the model does not have: {sorted(unknown)}")
    eager = [d for d in every if d[1] in EAGER]
    other = [d for d in every if d[1] not in EAGER and d[1] not in OUT]
    lines = [
        "---------------------------- MODULE EngineJobsTrace ----------------------------",
        "\\* written by gen_traces.py (S0059): the model's Next, each step labelled, the dispatch first",
        "EXTENDS EngineJobs",
        "VARIABLE act",
        "TVars == <<vars, act>>",
        "TInit == Init /\\ act = [a |-> \"Init\", p |-> 0]",
        "Eager == " + " \\/ ".join(name for _, name, _ in eager),
        "TEager ==",
        *[f"  \\/ {labelled(*d)}" for d in eager],
        "TOther ==",
        *[f"  \\/ {labelled(*d)}" for d in other],
        "TNext == IF ENABLED Eager THEN TEager ELSE TOther",
        "=============================================================================",
    ]
    return "\n".join(lines) + "\n"


def constants(world: str) -> dict[str, str]:
    """The world's constants: its base configuration's, with its own changes."""
    w = WORLDS[world]
    text = (CFG / f"{w['base']}.cfg").read_text(encoding="utf-8")
    block = text.split("CONSTANTS", 1)[1].split("INVARIANT", 1)[0].split("PROPERTY", 1)[0]
    consts = dict(re.findall(r"^\s+(\w+) = (.+)$", block, re.MULTILINE))
    unknown = set(w.get("set", {})) - set(consts)
    if unknown:
        raise SystemExit(f"{world}: no such constants {sorted(unknown)}")
    return {**consts, **w.get("set", {})}


def world_cfg(world: str) -> str:
    """The configuration of the walk: the world's constants, the trace module's INIT and NEXT."""
    lines = [f"    {k} = {v}" for k, v in constants(world).items()]
    return "CONSTANTS\n" + "\n".join(lines) + "\nINIT TInit\nNEXT TNext\nCHECK_DEADLOCK FALSE\n"


# -- TLC's trace values ----------------------------------------------------------------------------------------------

TOKEN = re.compile(
    r'\s*(?:(<<|>>|\|->|:>|@@|[\[\]{}(),])|("(?:[^"\\]|\\.)*")|(-?\d+)|(TRUE|FALSE)|([A-Za-z_][A-Za-z0-9_]*))'
)


class Reader:
    """A TLA+ value as TLC prints it: `[f |-> v, ...]` (dict), `<<v, ...>>` (list), `{v, ...}` (a list tagged
    {"set": [...]}), a function whose domain is neither strings nor `1..n`, `(k :> v @@ ...)` (a list of `[k, v]`
    pairs tagged {"fn": [...]}; S0064: the save temps' slots `<<journal, file>>`), strings, integers, booleans.
    Nothing else (a model value, a range) is read."""

    def __init__(self, text: str) -> None:
        self.tokens: list[tuple[str, str]] = []
        pos = 0
        text = text.rstrip()
        while pos < len(text):
            found = TOKEN.match(text, pos)
            if found is None or found.end() == pos:
                raise ValueError(f"cannot read the TLA+ value at {text[pos : pos + 40]!r}")
            kinds = ("punct", "str", "int", "bool", "name")
            kind = next(k for k, g in zip(kinds, found.groups(), strict=True) if g is not None)
            self.tokens.append((kind, found.group(kinds.index(kind) + 1)))
            pos = found.end()
        self.i = 0

    def value(self) -> Any:
        out = self._value()
        if self.i != len(self.tokens):
            raise ValueError(f"trailing tokens after a TLA+ value: {self.tokens[self.i : self.i + 5]}")
        return out

    def _take(self, want: str | None = None) -> tuple[str, str]:
        if self.i >= len(self.tokens):
            raise ValueError("the TLA+ value ended early")
        token = self.tokens[self.i]
        if want is not None and token[1] != want:
            raise ValueError(f"expected {want!r}, found {token[1]!r}")
        self.i += 1
        return token

    def _peek(self) -> str:
        return self.tokens[self.i][1] if self.i < len(self.tokens) else ""

    def _items(self, close: str) -> list[Any]:
        items: list[Any] = []
        if self._peek() == close:
            self._take(close)
            return items
        while True:
            items.append(self._value())
            if self._take()[1] == close:
                return items

    def _value(self) -> Any:
        kind, text = self._take()
        if kind == "str":
            return json.loads(text)
        if kind == "int":
            return int(text)
        if kind == "bool":
            return text == "TRUE"
        if text == "<<":
            return self._items(">>")
        if text == "{":
            return {"set": self._items("}")}
        if text == "(":
            pairs: list[list[Any]] = []
            while True:
                key = self._value()
                self._take(":>")
                pairs.append([key, self._value()])
                sep = self._take()[1]
                if sep == ")":
                    return {"fn": pairs}
                if sep != "@@":
                    raise ValueError(f"expected '@@' or ')', found {sep!r}")
        if text == "[":
            record: dict[str, Any] = {}
            while True:
                _, field = self._take()
                self._take("|->")
                record[field] = self._value()
                if self._take()[1] == "]":
                    return record
        raise ValueError(f"not a TLA+ value this reader reads: {text!r}")


def read_trace(text: str) -> list[dict[str, Any]]:
    """The states of one TLC trace file, each with the variables in KEEP_VARS."""
    states = []
    for block in re.split(r"^STATE_\d+ ==\s*$", text, flags=re.MULTILINE)[1:]:
        block = block.split("\n====", 1)[0]
        state: dict[str, Any] = {}
        for part in re.split(r"^/\\ ", block, flags=re.MULTILINE)[1:]:
            name, value = part.split(" = ", 1)
            if name.strip() in KEEP_VARS:
                state[name.strip()] = Reader(value).value()
        missing = set(KEEP_VARS) - set(state)
        if missing:
            raise ValueError(f"a trace state lacks {sorted(missing)}")
        states.append(state)
    return states


def compact(state: dict[str, Any]) -> dict[str, Any]:
    """Only the fields the replay reads, sets as sorted lists."""

    def job(j: dict[str, Any]) -> dict[str, Any]:
        return {k: j[k] for k in ("id", "k", "w", "wf", "conf", "req", "own")}

    runs = state["runs"]["set"]
    return {
        "act": state["act"],
        "q": [job(j) for j in state["q"]],
        "run": job(runs[0]) if runs else None,
        "eng": {"st": state["eng"]["st"]},
        "ctl": {k: state["ctl"][k] for k in ("pc", "past", "rinit")},
        "ses": {
            **{k: state["ses"][k] for k in ("app", "stops", "advice")},
            "opened": sorted(state["ses"]["opened"]["set"]),
        },
        "pst": {"orphan": state["pst"]["orphan"], "mirror1": state["pst"]["mirror"][0]["fp"]},
        **save_state(state),
    }


def save_state(state: dict[str, Any]) -> dict[str, Any]:
    """S0064 (BL-0151 slice 5): what the engine's save replay reads (`engine/tests/ledger/test_save_conformance.py`):
    the ledger files of workspace 1 and their temps, backups and restore temps (`fs`), each journal's state (`jr`),
    the running save's progress (`sv`: its journal, file index, plan, the journal a restore is on, the rollback's
    message, the file it skips) and what each save was told (`told`)."""
    fs, ctl = state["fs"], state["ctl"]
    return {
        "fs": {
            "cur": {f: fs["cur"][f] for f in ("R", "N", "I")},
            "tc": {f"{k[0]}/{k[1]}": v for k, v in fs["tc"]["fn"] if k[0] in ("s1", "s2", "s3")},
            "bk": dict(fs["bk"]),
            "rt": {f: fs["rt"][f] for f in ("R", "N")},
        },
        "jr": {j: rec["st"] for j, rec in state["jr"].items()},
        "sv": {
            "s": ctl["s"], "fi": ctl["fi"], "rj": ctl["rj"], "plan": {f: ctl["plan"][f] for f in ("R", "N", "I")},
            "rbt": ctl["rbt"], "skip": ctl["skip"],
        },
        "told": dict(state["gh"]["told"]),
    }  # fmt: skip


# -- the corpus ------------------------------------------------------------------------------------------------------


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tools_sha() -> str:
    return next(line.split()[2] for line in LOCK.read_text().splitlines() if line.startswith("tla2tools.jar"))


def header() -> dict[str, Any]:
    """What the corpus was made from; a corpus whose header differs from this is stale."""
    return {
        # each hash under a key named `sha256`, so the privacy scan reads it as a hash pin, not a secret
        "model": {"sha256": sha256(MODEL)},
        "generator": {"sha256": sha256(Path(__file__))},
        "tools": {"sha256": tools_sha()},
        "cfg": {b: {"sha256": sha256(CFG / f"{b}.cfg")} for b in sorted({w["base"] for w in WORLDS.values()})},
        "seed": SEED,
        "worlds": WORLDS,
        "candidates": CANDIDATES,
        "guards": GUARDS,
        "leave": LEAVE,
        "eager": list(EAGER),
        "out": sorted(OUT),
        "command": COMMAND,
    }


def simulate(world: str, traces: int, depth: int, work: Path) -> list[list[dict[str, Any]]]:
    """`traces` traces of the world of at most `depth` states, as TLC walks them with the seed."""
    folder = work / world
    folder.mkdir()
    shutil.copy(MODEL, folder / "EngineJobs.tla")
    (folder / "EngineJobsTrace.tla").write_text(wrapper(MODEL.read_text(encoding="utf-8"), WORLDS[world]["leave"]), encoding="utf-8")
    (folder / "EngineJobsTrace.cfg").write_text(world_cfg(world), encoding="utf-8")
    cmd = [
        "java", "-XX:+UseParallelGC", "-cp", str(JAR), "tlc2.TLC",
        "-simulate", f"file={folder / 'trace'},num={traces}", "-depth", str(depth), "-seed", str(SEED),
        "-workers", "1", "-metadir", str(folder / "states"), "-config", "EngineJobsTrace.cfg", "EngineJobsTrace.tla",
    ]  # fmt: skip
    done = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, timeout=600, check=False)
    if done.returncode != 0:
        raise SystemExit(f"TLC failed on {world} ({done.returncode}):\n{done.stdout[-4000:]}\n{done.stderr[-2000:]}")
    files = sorted(folder.glob("trace_*"), key=lambda p: [int(x) for x in p.name.split("_")[1:]])
    if len(files) != traces:
        raise SystemExit(f"{world}: TLC wrote {len(files)} traces, not {traces}")
    return [prune([compact(s) for s in read_trace(f.read_text(encoding="utf-8"))]) for f in files]


SAVE_KEYS = ("fs", "jr", "sv", "told")


def prune(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The save state (`save_state`) kept only where the engine's save replay reads it: the first state, each state
    in which a save runs, and the state right after one (what it was told). The corpus stays a few MB."""

    def saving(st: dict[str, Any]) -> bool:
        return st["run"] is not None and st["run"]["k"] == "save"

    out = []
    for i, st in enumerate(trace):
        keep = i == 0 or saving(st) or saving(trace[i - 1])
        out.append(st if keep else {k: v for k, v in st.items() if k not in SAVE_KEYS})
    return out


def generate() -> dict[str, Any]:
    if not JAR.exists() or sha256(JAR) != tools_sha():
        raise SystemExit("formal/tla/tools/tla2tools.jar is missing or not the pinned one: sh formal/tla/fetch_tools.sh")
    corpus: dict[str, Any] = {"header": header(), "constants": {}, "traces": {}}
    with tempfile.TemporaryDirectory(prefix="gen_traces_") as tmp:
        for world, w in WORLDS.items():
            keep, least = w["traces"], w["least"]
            corpus["constants"][world] = constants(world)
            walked = simulate(world, keep * CANDIDATES, w["depth"], Path(tmp))
            long = [t for t in walked if len(t) >= least]
            if len(long) < keep:
                raise SystemExit(f"{world}: only {len(long)} of {len(walked)} traces have {least} states or more")
            corpus["traces"][world] = select(long, keep)
    return corpus


def branches(trace: list[dict[str, Any]]) -> set[tuple[object, ...]]:
    """The steps of a trace by branch (S0064, BL-0159): the action, where the running job was (its kind and step), and
    whether the step ended it. A coarse key: the replay's own branch list is `BRANCHES` in the conformance test."""
    return {
        (t["act"]["a"], s["run"]["k"] if s["run"] else "", s["ctl"]["pc"], t["run"] is None)
        for s, t in zip(trace, trace[1:], strict=False)
    }


def select(long: list[list[dict[str, Any]]], keep: int) -> list[list[dict[str, Any]]]:
    """`keep` of the long enough traces, in TLC's order: each time the one that adds the most branches not yet kept
    (the first such on a tie), then the first ones left once nothing new comes (S0064: the first long enough traces of
    a world were alike, and a step that is enabled everywhere, as an I/O error, came early in each)."""
    seen: set[tuple[object, ...]] = set()
    chosen: list[int] = []
    keys = [branches(t) for t in long]
    while len(chosen) < min(keep, len(long)):
        gain, best = max((len(k - seen), -i) for i, k in enumerate(keys) if i not in chosen)
        if gain == 0:
            break
        chosen.append(-best)
        seen |= keys[-best]
    chosen += [i for i in range(len(long)) if i not in chosen][: keep - len(chosen)]
    return [long[i] for i in sorted(chosen)]


def delta(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each state as the variables that differ from the state before (the first whole), and under "-" the ones it no
    longer has. The readers rebuild the states with `undelta` (S0064: the corpus with the save state would be several
    MB)."""
    out = [trace[0]] if trace else []
    for before, now in zip(trace, trace[1:], strict=False):
        step = {k: v for k, v in now.items() if k not in before or before[k] != v}
        gone = sorted(before.keys() - now.keys())
        out.append({**step, "-": gone} if gone else step)
    return out


def undelta(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The states `delta` wrote (`backend/tests/platform/test_engine_jobs_conformance.py` and
    `engine/tests/ledger/test_save_conformance.py` each hold a copy of these lines: neither may import this file)."""
    states: list[dict[str, Any]] = []
    for step in steps:
        state = {**(states[-1] if states else {}), **step}
        for k in state.pop("-", []):
            del state[k]
        states.append(state)
    return states


def write(corpus: dict[str, Any]) -> None:
    CORPUS.parent.mkdir(exist_ok=True)
    lines = [json.dumps({k: v for k, v in corpus.items() if k != "traces"}, sort_keys=True)]
    for world, traces in corpus["traces"].items():
        for i, trace in enumerate(traces):
            row = {"world": world, "trace": i, "steps": delta(trace)}
            lines.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    CORPUS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stale() -> list[str]:
    """The header fields of the committed corpus that differ from the model, the worlds and this script now."""
    if not CORPUS.exists():
        return ["the corpus is missing"]
    first = json.loads(CORPUS.read_text(encoding="utf-8").splitlines()[0])["header"]
    now = header()
    return [k for k in now if first.get(k) != now[k]]


def main() -> int:
    if "--check" in sys.argv:
        diff = stale()
        if diff:
            print(f"the trace corpus is stale ({', '.join(diff)}); regenerate it: {COMMAND}")
            return 1
        print("the trace corpus is current")
        return 0
    corpus = generate()
    write(corpus)
    count = sum(len(t) for t in corpus["traces"].values())
    steps = sum(len(s) - 1 for t in corpus["traces"].values() for s in t)
    print(f"wrote {CORPUS.relative_to(REPO)}: {count} traces, {steps} steps")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
