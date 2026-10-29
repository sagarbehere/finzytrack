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

The corpus keeps, per state, only the variables the replay reads (`q`, `runs`, `eng`, `ctl`, `ses`, `pst`; the file
system, the journals and the ghost are left out), and records the SHA-256 of the model, of each world's `.cfg`, of
this script and of the TLA+ tools, so `backend/tests/platform/test_engine_jobs_conformance.py` fails on a stale
corpus with this command in its message. The reader of TLC's trace values below reads only the shapes those
variables use (records, sequences, sets, strings, integers, booleans) and refuses anything else.
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
# states: TLC walks CANDIDATES times as many and the first long enough are kept (a world runs out of things to do
# early when the boot is cancelled, say). Kept small: the test replays every trace through the table and through
# the shell in seconds.
WORLDS: dict[str, dict[str, Any]] = {
    "S_queue": {"base": "S_queue", "traces": 12, "depth": 50, "least": 20, "leave": ["boot_cancel"]},
    # every job runs to its end: a save's R4 queued behind a background save (the oldest background job first)
    "S_queue_done": {
        "base": "S_queue", "set": {"MaxCrash": "0"}, "traces": 6, "depth": 60, "least": 30, "leave": ["cancel"],
    },
    "S_cancel": {"base": "S_cancel", "traces": 12, "depth": 40, "least": 14, "leave": []},
    "S_recover_lock": {"base": "S_recover_lock", "traces": 8, "depth": 40, "least": 2, "leave": []},
    # saves that finish and their R4 in a fresh engine; R4's crashes and the window
    "S_r4_fresh": {
        "base": "S_r4_attempts", "set": {"MaxCrash": "0", "MaxAppCrash": "0"}, "traces": 6, "depth": 80, "least": 40,
        "leave": ["cancel"],
    },
    "S_r4_attempts": {"base": "S_r4_attempts", "traces": 8, "depth": 80, "least": 16, "leave": ["cancel"]},
    # one stop makes the engine unavailable: the queued jobs end without one, the window, Try again
    "S_queue_bound1": {
        "base": "S_queue", "set": {"StopBound": "1", "MaxExpire": "1"}, "traces": 10, "depth": 50, "least": 16,
        "leave": ["cancel"],
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
    # BL-0151: recover-all goes on with the next journal after its engine's death, and after a Cancel past its listing
    "EngineCrash": '~(Busy /\\ RJ.k \\in {"recover", "open"})',
    "UserCancelRunning": '~(Busy /\\ RJ.k = "recover" /\\ ctl.rinit)',
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
}
KEEP_VARS = ("act", "q", "runs", "eng", "ctl", "ses", "pst")

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
            call = f"{call} /\\ {guard}"
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

TOKEN = re.compile(r'\s*(?:(<<|>>|\|->|[\[\]{}(),])|("(?:[^"\\]|\\.)*")|(-?\d+)|(TRUE|FALSE)|([A-Za-z_][A-Za-z0-9_]*))')


class Reader:
    """A TLA+ value as TLC prints it: `[f |-> v, ...]` (dict), `<<v, ...>>` (list), `{v, ...}` (a list tagged
    {"set": [...]}), strings, integers, booleans. Nothing else (a function `:>`/`@@`, a model value) is read."""

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
        "pst": {"orphan": state["pst"]["orphan"]},
    }


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
    return [[compact(s) for s in read_trace(f.read_text(encoding="utf-8"))] for f in files]


def generate() -> dict[str, Any]:
    if not JAR.exists() or sha256(JAR) != tools_sha():
        raise SystemExit("formal/tla/tools/tla2tools.jar is missing or not the pinned one: sh formal/tla/fetch_tools.sh")
    corpus: dict[str, Any] = {"header": header(), "constants": {}, "traces": {}}
    with tempfile.TemporaryDirectory(prefix="gen_traces_") as tmp:
        for world, w in WORLDS.items():
            keep, least = w["traces"], w["least"]
            corpus["constants"][world] = constants(world)
            walked = simulate(world, keep * CANDIDATES, w["depth"], Path(tmp))
            long = [t for t in walked if len(t) >= least][:keep]
            if len(long) < keep:
                raise SystemExit(f"{world}: only {len(long)} of {len(walked)} traces have {least} states or more")
            corpus["traces"][world] = long
    return corpus


def write(corpus: dict[str, Any]) -> None:
    CORPUS.parent.mkdir(exist_ok=True)
    lines = [json.dumps({k: v for k, v in corpus.items() if k != "traces"}, sort_keys=True)]
    for world, traces in corpus["traces"].items():
        for i, trace in enumerate(traces):
            lines.append(json.dumps({"world": world, "trace": i, "states": trace}, sort_keys=True, separators=(",", ":")))
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
