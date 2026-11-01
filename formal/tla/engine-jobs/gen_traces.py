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
- the actions in OUT are left out (their behaviour is replayed elsewhere or later; the replay could only stop there).

The corpus keeps, per state, only the variables the save replay reads (`KEEP_VARS`; around saves the file system, the
records and what the save was told, `prune`; `engine/tests/ledger/test_save_conformance.py` replays the saves through
the engine), each trace as deltas, and records the SHA-256 of the model, of each world's `.cfg`, of this script and of
the TLA+ tools, so the replay fails on a stale corpus with this command in its message. The reader of TLC's trace
values below reads only the shapes those variables use (records, sequences, sets, functions, strings, integers,
booleans) and refuses anything else.
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
# branches left out (`leave`, names in LEAVE): outside edits, an engine death and an I/O error anywhere in the save
# (one save; then two or three, the second replacing the file the first created, so its rollback restores both).
# `traces` are kept, of at most `depth` states, each with at least `least` states: TLC walks CANDIDATES times as many
# and, of the long enough ones, those that add the most branches are kept (`select`). Kept small: the test replays
# every trace through the engine's save in seconds.
WORLDS: dict[str, dict[str, Any]] = {
    "S_save": {
        "base": "S_save_crash", "set": {"MaxEdits": "0"}, "traces": 20, "depth": 40, "least": 5,
        "leave": ["crash_in_save"],
    },
    "S_save_edit": {
        "base": "S_save_crash", "set": {"MaxIO": "0"}, "traces": 12, "depth": 40, "least": 5,
        "leave": ["edit_before_commit", "crash_in_save"],
    },
    "S_save_edit_late": {
        "base": "S_save_crash", "set": {"MaxIO": "0"}, "traces": 12, "depth": 40, "least": 10,
        "leave": ["edit_past_commit", "crash_past_commit"],
    },
    # S0072 (BL-0161): an outside save at the swap, moved to its kept name; the rollback then stops on the kept copy
    "S_save_kept": {
        "base": "S_save_crash", "set": {"MaxIO": "0", "MaxCrash": "0", "EdFiles": '{"R"}'}, "traces": 6,
        "depth": 40, "least": 10, "leave": ["edit_at_swap"],
    },
    # S0072 (BL-0161): an I/O error before the displaced check: the rollback's check keeps the temp's outside save
    "S_save_kept_io": {
        "base": "S_save_crash", "set": {"MaxCrash": "0", "EdFiles": '{"R"}'}, "traces": 6, "depth": 40,
        "least": 10, "leave": ["edit_at_swap", "io_at_compare"],
    },
    # an outside save in the root after the journal opened, then an I/O error at a swap or exclusive rename, before it
    # or after it took place (S0072 verifier r1 F2): the rollback stops on the outside save, Unfinished
    "S_save_swap_io": {
        "base": "S_save_crash", "set": {"MaxCrash": "0", "EdFiles": '{"R"}'}, "traces": 8, "depth": 40,
        "least": 10, "leave": ["edit_before_swap", "io_at_swap"],
    },
    # S0072 (BL-0161): a file put at the path the save creates, past the gate: the code never touches it
    "S_save_created_taken": {
        "base": "S_save_crash", "set": {"MaxIO": "0", "MaxCrash": "0", "EdFiles": '{"N"}'}, "traces": 6,
        "depth": 40, "least": 6, "leave": ["edit_past_commit"],
    },
    "S_save_io": {
        "base": "S_save_two", "set": {"MaxCrash": "0"}, "traces": 16, "depth": 60, "least": 20, "leave": [],
    },
    # the second save's faults: it replaces the file the first created, so its rollback restores both files; a third
    # save is refused while the second's journal is open
    "S_save_two": {
        "base": "S_save_two", "set": {"MaxSaves": "3"}, "traces": 16, "depth": 80, "least": 30,
        "leave": ["second_save_faults"],
    },
    "S_save_restore": {
        "base": "S_save_two", "set": {"MaxCrash": "0"}, "traces": 10, "depth": 60, "least": 30,
        "leave": ["second_save_faults", "io_after_first_swap"],
    },
    # an outside edit of a file the save writes, after its backup, then an I/O error before the journal is open, so
    # the save ends in the engine with "not saved, changed" (the re-hash, §11)
    "S_save_io_edit": {
        "base": "S_save_crash", "set": {"MaxCrash": "0", "EdFiles": '{"R", "N"}'}, "traces": 10, "depth": 40,
        "least": 8, "leave": ["edit_after_backup", "io_after_edit"],
    },
    # an engine death after the journal's close (the save has landed: told "saved" from the disk)
    "S_save_late_crash": {
        "base": "S_save_crash", "set": {"MaxIO": "0", "MaxEdits": "0"}, "traces": 6, "depth": 40, "least": 12,
        "leave": ["crash_after_close"],
    },
    # an engine death past the commit point
    "S_save_io_crash": {
        "base": "S_save_two", "set": {"MaxEdits": "0"}, "traces": 16, "depth": 60, "least": 10,
        "leave": ["crash_past_commit"],
    },
}  # fmt: skip
CANDIDATES = 20
SEED = 20261029
OUT = frozenset(
    {
        # BL-0153: create and copy (their jobs never start, so their steps never run)
        "StartCreate", "StartCopy", "EditorDrop", "EditorOverwrite",
        # BL-0164: recover-all through the engine, and Keep; the app's crash and a power loss end the session, whose
        # next start is recover-all's; the path changer and the unreachable disk act on boot journals only
        "StartRecover", "Keep", "Relaunch", "AppCrash", "PowerLoss", "PathChange", "Unreach",
    }
)  # fmt: skip
# Branches of an action that belong to a later piece, left out the same way (none since S0072).
GUARDS: dict[str, str] = {}
# Branches a world may leave out so that its traces reach further (each trace is still a behaviour of Spec).
LEAVE: dict[str, dict[str, str]] = {
    # outside edits only while a save runs, before or past its commit point (an edit early in a walk makes every
    # save refuse at its gate)
    "edit_before_commit": {"Editor": 'Busy /\\ run.k = "save" /\\ ctl.pc = "s_gate"'},
    "edit_past_commit": {"Editor": 'Busy /\\ run.k = "save" /\\ ctl.pc # "s_gate"'},
    # an edit just before the save's swap, so the swap displaces it (S0072: the kept move), and an I/O error only at
    # the displaced check that follows (the rollback's check then keeps it)
    "edit_at_swap": {"Editor": 'Busy /\\ run.k = "save" /\\ ctl.pc = "s_sw"'},
    "io_at_compare": {"IOFail": 'ctl.pc = "s_cmp"'},
    # an edit after the journal is open and before the first swap, and an I/O error only at a swap or exclusive rename
    "edit_before_swap": {"Editor": 'Busy /\\ run.k = "save" /\\ ctl.pc \\in {"s_jds", "s_tw1", "s_tw2", "s_ts"} /\\ ctl.fi = 1'},
    "io_at_swap": {"IOFail": 'ctl.pc = "s_sw"'},
    # an edit after the save's backup and before its journal is open, and an I/O error only after the edit and before
    # the journal is open (the re-hash then finds the edit)
    "edit_after_backup": {"Editor": 'Busy /\\ run.k = "save" /\\ ctl.pc \\in {"s_bks", "s_jp", "s_jps"}'},
    "io_after_edit": {"IOFail": 'ctl.pc \\in PreJournalPcs /\\ n.ed = MaxEdits'},
    # an engine death only where a world wants it (with one death allowed, recover-all at the start took it first)
    "crash_in_save": {"EngineCrash": 'run.k = "save"'},
    "crash_past_commit": {"EngineCrash": 'run.k = "save" /\\ ctl.pc # "s_gate"'},
    "crash_after_close": {"EngineCrash": 'run.k = "save" /\\ ctl.pc \\in {"s_csync", "s_saved"}'},
    # faults only in the second save
    "second_save_faults": {"IOFail": 'ctl.s = "s2"', "EngineCrash": 'run.k = "save" /\\ ctl.pc # "s_gate" /\\ ctl.s = "s2"'},
    # an I/O error in the second save only once it has replaced its first file, so its rollback puts pre bytes back
    "io_after_first_swap": {"IOFail": 'ctl.s = "s2" /\\ fs.cur["N"] = PostV("s2")'},
}  # fmt: skip
KEEP_VARS = ("act", "app", "run", "ctl", "fs", "jr", "gh")

# -- the model's Next, read from its source --------------------------------------------------------------------------

GROUPS = ("EngineStep", "UserStep", "EnvStep")
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
    """The trace module: the model's Next with each step labelled, OUT left out, and the guarded branches left
    out."""
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
    kept = [d for d in every if d[1] not in OUT]
    lines = [
        "---------------------------- MODULE EngineJobsTrace ----------------------------",
        "\\* written by gen_traces.py (S0059): the model's Next, each step labelled",
        "EXTENDS EngineJobs",
        "VARIABLE act",
        "TVars == <<vars, act>>",
        "TInit == Init /\\ act = [a |-> \"Init\", p |-> 0]",
        "TNext ==",
        *[f"  \\/ {labelled(*d)}" for d in kept],
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
    run = state["run"]
    return {
        "act": state["act"],
        "app": state["app"],
        "run": None if run["k"] == "none" else {"k": run["k"], "id": run["id"]},
        "ctl": {"pc": state["ctl"]["pc"]},
        **save_state(state),
    }


def save_state(state: dict[str, Any]) -> dict[str, Any]:
    """S0064 (BL-0151 slice 5), S0072: what the engine's save replay reads (`engine/tests/ledger/test_save_conformance.py`):
    the ledger files of workspace 1 and, per save journal and file, its temp, staging file, restore temp and kept
    copies, and its backup (`fs`); each journal's state (`jr`); the running save's progress (`sv`: its journal, file
    index, plan, the journal a restore is on, the rollback's message) and what each save was told (`told`)."""
    fs, ctl = state["fs"], state["ctl"]

    def slots(fn: dict[str, Any]) -> dict[str, Any]:
        return {f"{k[0]}/{k[1]}": v for k, v in fn["fn"] if k[0] in ("s1", "s2", "s3")}

    return {
        "fs": {
            "cur": {f: fs["cur"][f] for f in ("R", "N", "I")},
            "tc": slots(fs["tc"]), "sg": slots(fs["sg"]), "rt": slots(fs["rt"]),
            "kp": {k: sorted(v["set"]) for k, v in slots(fs["kp"]).items()},
            "bk": dict(fs["bk"]),
        },
        "jr": {j: rec["st"] for j, rec in state["jr"].items()},
        "sv": {
            "s": ctl["s"], "fi": ctl["fi"], "rj": ctl["rj"], "plan": {f: ctl["plan"][f] for f in ("R", "N", "I")},
            "rbt": ctl["rbt"],
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
    """The states `delta` wrote (`engine/tests/ledger/test_save_conformance.py` holds a copy of these lines: it may
    not import this file)."""
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
