#!/usr/bin/env python3
"""Write the conformance trace corpus of the mirror-publish model (S0091; BL-0210; design/specs/mirror/publish.md).

    python3 formal/tla/mirror-publish/gen_traces.py            # regenerate traces/corpus.json (needs Java 21)
    python3 formal/tla/mirror-publish/gen_traces.py --check    # exit 1 if the corpus is stale (no Java)

As `formal/tla/engine-jobs/gen_traces.py` does for the engine-jobs model (whose reader of TLC's trace values and whose
deltas this script imports): TLC's simulate mode (a fixed `-seed` and `-depth`, one worker) walks the small worlds named
in WORLDS, each a committed `cfg/<base>.cfg` with a few constants changed, through `MirrorPublishTrace`, a module this
script writes next to a copy of the model; it changes the model in two ways only, so every trace is a behaviour of
`Spec`:

- each step is labelled with its action (`act`, and its argument `p`: a generation, a file or a reader);
- the actions in OUT are left out, and so are the branches a world leaves out (`leave`, names in LEAVE).

`engine/tests/mirror/test_publish_conformance.py` replays every trace through `ledger_engine.ledger.rebuild.run` and
`ledger_engine.ledger.restore.recover_journal` over the in-memory fake. The corpus keeps, per state, only what it reads
(`compact`), each trace as deltas, and records the SHA-256 of the model, of each world's `.cfg`, of this script, of
the engine-jobs script it imports and of the TLA+ tools, so the replay fails on a stale corpus with this command in its
message.
"""

from __future__ import annotations

import hashlib
import importlib.util
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
MODEL = HERE / "MirrorPublish.tla"
CFG = HERE / "cfg"
JAR = REPO / "formal" / "tla" / "tools" / "tla2tools.jar"
LOCK = REPO / "formal" / "tla" / "tools.lock"
CORPUS = HERE / "traces" / "corpus.json"
COMMAND = "python3 formal/tla/mirror-publish/gen_traces.py"
ENGINE_JOBS_GEN = REPO / "formal" / "tla" / "engine-jobs" / "gen_traces.py"


def _engine_jobs_gen() -> Any:
    spec = importlib.util.spec_from_file_location("_tla_engine_jobs_gen_traces_for_mirror", ENGINE_JOBS_GEN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EJ = _engine_jobs_gen()

# The small worlds walked. Each leaves out what the replay cannot drive through the code (LEAVE says why); `traces`
# kept of at most `depth` states, each with at least `least` states, chosen as the engine-jobs script chooses them.
WORLDS: dict[str, dict[str, Any]] = {
    # publishes one after another while a reader reads twice per request; an incomplete build; an engine death
    "S_publish": {
        "base": "S_publish", "set": {}, "traces": 16, "depth": 60, "least": 20, "leave": ["whole_cleanup", "names_left"],
    },
    # an outside editor and a save of ours cut with its `.open` journal, then its recovery, around the publishes
    "S_ledger": {
        "base": "S_ledger", "set": {}, "traces": 16, "depth": 60, "least": 20,
        "leave": ["whole_cleanup", "names_left", "edit_outside_the_load"],
    },
    # an engine death anywhere in a job, then the next job's cleanup
    "S_crash": {
        "base": "S_publish", "set": {"MaxReqs": "1", "MayIncomplete": "FALSE", "MaxCrash": "2"}, "traces": 10,
        "depth": 50, "least": 15, "leave": ["whole_cleanup", "names_left"],
    },
}  # fmt: skip
CANDIDATES = 20
SEED = 20261109
OUT = frozenset(
    {
        # a power loss picks one outcome per generation and for the link; the fake cannot be steered to the model's
        # pick (`engine/tests/mirror/test_publish_crash.py` checks every power-loss state at every effect instead)
        "PowerLoss",
        # the OS purges the cache folder under a running job: the model ends the job at once, the code would go on
        # writing into the removed folder; `test_publish_crash.py` and `test_publish.py` (`a cache folder the OS
        # purged is made again`) check it
        "Purge",
    }
)  # fmt: skip
# Branches a world leaves out, each because the code does that step otherwise (each trace is still a behaviour of Spec):
LEAVE: dict[str, dict[str, str]] = {
    # the cleanup removes everything it does not keep before the job goes on (the code's `clean` with its grace period
    # over: `test_publish_conformance.py` runs every job long after the last one); the model may stop it anywhere
    "whole_cleanup": {"P_CleanDone": "\\A g \\in Gens : g \\in Keep \\/ (gens[g].c.k = ABS /\\ ~gens[g].jl)"},
    # a job starts only while the world has a name left for its build (the model's `P_Create` ends a job that has
    # none; the code's names have no such bound)
    "names_left": {"StartPublish": "Gens \\ used # {}"},
    # no outside edit inside the load (between the observation and the end of the read): the code observes both files
    # at once before its load, reads both, and observes again after it, so an edit there ends its job `moved` before
    # the build, where the model's job ends at its re-check (`P_ReObs`) after it (MAPPING.md §3)
    "edit_outside_the_load": {"Editor": 'ctl.pc \\notin {"obs2", "read1", "read2"}'},
}
KEEP_VARS = ("act", "disk", "jopen", "jpre", "jpost", "gens", "lk", "ctl", "rd")

# -- the model's Next, read from its source --------------------------------------------------------------------------

GROUPS = ("EngineStep", "ReaderStep", "EnvStep")
BINDER = re.compile(r"^\\E\s+([a-z]+)\s+\\in\s+([^:]+):\s*(.*)$")
CALL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\(([a-z]*)\))?$")


def disjuncts(model: str, group: str) -> list[tuple[str, str, str]]:
    """(binder, action, argument) for each disjunct of a group of `Next`. A bulleted item `\\/ \\E g \\in Gens : A(g)
    \\/ B(g)` binds `g` for every disjunct on its line (the existential reaches to the end of its item). A disjunct it
    cannot read is refused, never dropped."""
    out: list[tuple[str, str, str]] = []
    body = EJ._body(model, group)  # pyright: ignore[reportPrivateUsage]
    items = [i.strip() for i in re.split(r"(?:^|\s)\\/\s+(?=\\E)", body) if i.strip()]
    for item in items:
        found = BINDER.match(item)
        binder, rest = (f"\\E {found.group(1)} \\in {found.group(2).strip()} :", found.group(3)) if found else ("", item)
        for part in (p.strip() for p in rest.split("\\/")):
            if not part:
                continue
            call = CALL.match(part)
            if call is None:
                raise SystemExit(f"{group}: cannot read the disjunct {part!r}")
            out.append((binder, call.group(1), call.group(3) or ""))
    return out


def actions(model: str) -> list[tuple[str, str, str]]:
    return [d for group in GROUPS for d in disjuncts(model, group)]


def wrapper(model: str, leave: list[str]) -> str:
    """The trace module: the model's Next with each step labelled, OUT and the guarded branches left out."""
    guards: dict[str, list[str]] = {}
    for name in leave:
        for n, g in LEAVE[name].items():
            guards.setdefault(n, []).append(g)

    def labelled(binder: str, name: str, arg: str) -> str:
        call = f"{name}({arg})" if arg else name
        for guard in guards.get(name, []):
            call = f"{call} /\\ ({guard})"
        param = arg if arg else "0"
        return f"({binder} {call} /\\ act' = [a |-> \"{name}\", p |-> {param}])".replace("( ", "(")

    every = actions(model)
    names = {name for _, name, _ in every}
    unknown = (OUT | set(guards)) - names
    if unknown:
        raise SystemExit(f"OUT or LEAVE names actions the model does not have: {sorted(unknown)}")
    kept = [d for d in every if d[1] not in OUT]
    lines = [
        "---------------------------- MODULE MirrorPublishTrace ----------------------------",
        "\\* written by gen_traces.py (S0091): the model's Next, each step labelled",
        "EXTENDS MirrorPublish",
        "VARIABLE act",
        "TInit == Init /\\ act = [a |-> \"Init\", p |-> 0]",
        "TNext ==",
        *[f"  \\/ {labelled(*d)}" for d in kept],
        "=============================================================================",
    ]
    return "\n".join(lines) + "\n"


def constants(world: str) -> dict[str, str]:
    w = WORLDS[world]
    text = (CFG / f"{w['base']}.cfg").read_text(encoding="utf-8")
    block = text.split("CONSTANTS", 1)[1].split("CHECK_DEADLOCK", 1)[0]
    consts = dict(re.findall(r"^\s+(\w+) = (.+)$", block, re.MULTILINE))
    unknown = set(w.get("set", {})) - set(consts)
    if unknown:
        raise SystemExit(f"{world}: no such constants {sorted(unknown)}")
    return {**consts, **w.get("set", {})}


def world_cfg(world: str) -> str:
    lines = [f"    {k} = {v}" for k, v in constants(world).items()]
    return "CONSTANTS\n" + "\n".join(lines) + "\nINIT TInit\nNEXT TNext\nCHECK_DEADLOCK FALSE\n"


# -- TLC's trace values ----------------------------------------------------------------------------------------------


def read_trace(text: str) -> list[dict[str, Any]]:
    """The states of one TLC trace file, each with the variables in KEEP_VARS (engine-jobs' `Reader`)."""
    states = []
    for block in re.split(r"^STATE_\d+ ==\s*$", text, flags=re.MULTILINE)[1:]:
        block = block.split("\n====", 1)[0]
        state: dict[str, Any] = {}
        for part in re.split(r"^/\\ ", block, flags=re.MULTILINE)[1:]:
            name, value = part.split(" = ", 1)
            if name.strip() in KEEP_VARS:
                state[name.strip()] = EJ.Reader(value).value()
        missing = set(KEEP_VARS) - set(state)
        if missing:
            raise ValueError(f"a trace state lacks {sorted(missing)}")
        states.append(state)
    return states


def _content(c: dict[str, Any]) -> dict[str, Any]:
    return {"k": c["k"], "rows": c["rows"], "lab": c["lab"]}


def compact(state: dict[str, Any]) -> dict[str, Any]:
    """What the replay reads: the ledger (its two files, the cut save's journal and versions), per generation name its
    content and whether its journal stands beside it (not what a power loss may leave), the link, the job's step and
    generation, and per reader its request's state, pinned generation and snapshots."""
    ctl = state["ctl"]
    return {
        "act": state["act"],
        "disk": state["disk"],
        "jopen": state["jopen"], "jpre": state["jpre"], "jpost": state["jpost"],
        "gens": [{**_content(g["c"]), "jl": g["jl"]} for g in state["gens"]],
        "lk": state["lk"],
        "ctl": {"pc": ctl["pc"], "g": ctl["g"]},
        "rd": {r: {"st": v["st"], "g": v["g"], "s1": _content(v["s1"]), "s2": _content(v["s2"])}
               for r, v in state["rd"].items()},
    }  # fmt: skip


# -- the corpus ------------------------------------------------------------------------------------------------------


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tools_sha() -> str:
    return next(line.split()[2] for line in LOCK.read_text().splitlines() if line.startswith("tla2tools.jar"))


def header() -> dict[str, Any]:
    """What the corpus was made from; a corpus whose header differs from this is stale."""
    return {
        "model": {"sha256": sha256(MODEL)},
        "generator": {"sha256": sha256(Path(__file__))},
        "engine_jobs_generator": {"sha256": sha256(ENGINE_JOBS_GEN)},
        "tools": {"sha256": tools_sha()},
        "cfg": {b: {"sha256": sha256(CFG / f"{b}.cfg")} for b in sorted({w["base"] for w in WORLDS.values()})},
        "seed": SEED,
        "worlds": WORLDS,
        "candidates": CANDIDATES,
        "leave": LEAVE,
        "out": sorted(OUT),
        "command": COMMAND,
    }


def simulate(world: str, traces: int, depth: int, work: Path) -> list[list[dict[str, Any]]]:
    folder = work / world
    folder.mkdir()
    shutil.copy(MODEL, folder / "MirrorPublish.tla")
    w = WORLDS[world]
    (folder / "MirrorPublishTrace.tla").write_text(wrapper(MODEL.read_text(encoding="utf-8"), w["leave"]), "utf-8")
    (folder / "MirrorPublishTrace.cfg").write_text(world_cfg(world), encoding="utf-8")
    cmd = [
        "java", "-XX:+UseParallelGC", "-cp", str(JAR), "tlc2.TLC",
        "-simulate", f"file={folder / 'trace'},num={traces}", "-depth", str(depth), "-seed", str(SEED),
        "-workers", "1", "-metadir", str(folder / "states"), "-config", "MirrorPublishTrace.cfg",
        "MirrorPublishTrace.tla",
    ]  # fmt: skip
    done = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, timeout=600, check=False)
    if done.returncode != 0:
        raise SystemExit(f"TLC failed on {world} ({done.returncode}):\n{done.stdout[-4000:]}\n{done.stderr[-2000:]}")
    files = sorted(folder.glob("trace_*"), key=lambda p: [int(x) for x in p.name.split("_")[1:]])
    if len(files) != traces:
        raise SystemExit(f"{world}: TLC wrote {len(files)} traces, not {traces}")
    return [[compact(s) for s in read_trace(f.read_text(encoding="utf-8"))] for f in files]


def branches(trace: list[dict[str, Any]]) -> set[tuple[object, ...]]:
    """The steps of a trace by branch: the action and the job's step it was taken at, and whether it ended the job."""
    return {
        (t["act"]["a"], s["ctl"]["pc"], t["ctl"]["pc"] == "none") for s, t in zip(trace, trace[1:], strict=False)
    }


def select(long: list[list[dict[str, Any]]], keep: int) -> list[list[dict[str, Any]]]:
    """`keep` of the long enough traces: each time the one that adds the most branches not yet kept (engine-jobs'
    `select`, over this model's branches)."""
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


def generate() -> dict[str, Any]:
    if not JAR.exists() or sha256(JAR) != tools_sha():
        raise SystemExit("formal/tla/tools/tla2tools.jar is missing or not the pinned one: sh formal/tla/fetch_tools.sh")
    corpus: dict[str, Any] = {"header": header(), "constants": {}, "traces": {}}
    with tempfile.TemporaryDirectory(prefix="gen_traces_mirror_") as tmp:
        for world, w in WORLDS.items():
            keep, least = w["traces"], w["least"]
            corpus["constants"][world] = constants(world)
            walked = simulate(world, keep * CANDIDATES, w["depth"], Path(tmp))
            long = [t for t in walked if len(t) >= least]
            if len(long) < keep:
                raise SystemExit(f"{world}: only {len(long)} of {len(walked)} traces have {least} states or more")
            corpus["traces"][world] = select(long, keep)
    return corpus


def write(corpus: dict[str, Any]) -> None:
    CORPUS.parent.mkdir(exist_ok=True)
    lines = [json.dumps({k: v for k, v in corpus.items() if k != "traces"}, sort_keys=True)]
    for world, traces in corpus["traces"].items():
        for i, trace in enumerate(traces):
            row = {"world": world, "trace": i, "steps": EJ.delta(trace)}
            lines.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    CORPUS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stale() -> list[str]:
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
