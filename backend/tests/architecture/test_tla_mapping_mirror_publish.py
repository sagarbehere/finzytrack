"""The mirror-publish TLA+ model's code map, checked both ways (EP-19; `formal/tla/mirror-publish/MAPPING.md` §2.1;
S0087, BL-0113).

Each action of `formal/tla/mirror-publish/MirrorPublish.tla` that our code carries out (the groups `EngineStep`, the
engine's publish job, the save and recovery, and `ReaderStep`, the screens', the gate's and an outside tool's reads)
names the code that does it: a function marked `@tla_action("<Action>", model="mirror-publish")` (the engine's
`ledger_engine.tla`, the sidecar's `app.mirror.model_map`). The OS, an outside editor and a crash (`EnvStep`) need
none. An action with no step of ours is in `CODELESS` with its reason. The check fails:
- on a mirror-publish marker that names no action of `Next`;
- on an engine or reader action with neither a marker nor a `CODELESS` row;
- on a `CODELESS` action that is marked, or that is no action of `Next`.
The markers are read from the source (AST) of both packages, never imported (ADR-0054). Same limit as the engine-jobs
map: it sees which actions are marked, not whether the marked function does what the action does; the publish's
sweeps (`engine/tests/mirror/test_publish_crash.py`) test that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

from tests.architecture.test_tla_mapping import Marker, markers, next_groups, of_model, step_actions

REPO = Path(__file__).resolve().parents[3]
MODEL = REPO / "formal" / "tla" / "mirror-publish" / "MirrorPublish.tla"
SOURCES = (REPO / "engine" / "src", REPO / "backend" / "src")
NAME = "mirror-publish"
MARKED_GROUPS = ("EngineStep", "ReaderStep")
FREE_GROUPS = ("EnvStep",)
CODELESS: Mapping[str, str] = {
    "R_End": "a request's end: its connection closes and the pinned path is dropped; no step of ours does more",
}


def problems(model: str, found: Iterable[Marker], codeless: Mapping[str, str]) -> list[str]:
    groups = {g: step_actions(model, g) for g in next_groups(model)}
    every = set[str]().union(*groups.values())
    required = set[str]().union(*(groups.get(g, set[str]()) for g in MARKED_GROUPS))
    found = list(found)
    marked = {m.action for m in found}
    out = [f"{g}: a group of Next the map does not classify" for g in groups if g not in (*MARKED_GROUPS, *FREE_GROUPS)]
    out += [
        f"{g}: a group the map expects is missing from Next" for g in (*MARKED_GROUPS, *FREE_GROUPS) if g not in groups
    ]
    out += [f"{m.where}: @tla_action({m.action!r}, model={NAME!r}) names no action of Next" for m in found
            if m.action not in every]  # fmt: skip
    out += [
        f"{a}: an action of ours with no marker and no CODELESS row" for a in sorted(required - marked - set(codeless))
    ]
    out += [f"{a}: CODELESS but marked" for a in sorted(set(codeless) & marked)]
    out += [f"{a}: a CODELESS row that is not an action of Next" for a in sorted(set(codeless) - every)]
    return out


def test_the_mirror_publish_code_map_holds_both_ways() -> None:
    found = of_model(markers(SOURCES), NAME)
    assert problems(MODEL.read_text(encoding="utf-8"), found, CODELESS) == []
    where = {m.where.split(":")[0] for m in found}
    assert any(w.startswith("engine/") for w in where), "no engine marker found: the scan is broken"
    assert any(w.startswith("backend/") for w in where), "no sidecar marker found: the scan is broken"


def test_the_required_set_is_the_models_engine_and_reader_actions() -> None:
    model = MODEL.read_text(encoding="utf-8")
    assert next_groups(model) == [*MARKED_GROUPS, *FREE_GROUPS]
    required = step_actions(model, "EngineStep") | step_actions(model, "ReaderStep")
    assert {"StartPublish", "P_CleanDb", "P_Swap", "P_JCheck", "SaveCut", "R_Resolve", "R_Read2"} <= required
    assert not required & {"Editor", "EngineCrash", "AppCrash", "PowerLoss", "Purge"}


TOY = """
P_A == TRUE
P_B(g) == TRUE
R_X(r) == TRUE
R_Done(r) == TRUE
Crash == TRUE
EngineStep ==
    \\/ P_A \\/ \\E g \\in Gens : P_B(g)

ReaderStep ==
    \\E r \\in Readers : R_X(r) \\/ R_Done(r)

EnvStep ==
    \\/ Crash

Next == EngineStep \\/ ReaderStep \\/ EnvStep
"""


def m(action: str) -> Marker:
    return Marker(action, "toy.py:1 f", NAME)


def test_toy_with_every_action_marked_or_codeless_passes() -> None:
    assert problems(TOY, [m("P_A"), m("P_B"), m("R_X")], {"R_Done": "why"}) == []


@pytest.mark.parametrize(
    ("found", "codeless", "needle"),
    [
        ([m("P_A"), m("P_B"), m("R_X"), m("Nope")], {"R_Done": "why"}, "names no action"),
        ([m("P_A"), m("R_X")], {"R_Done": "why"}, "P_B: an action of ours"),
        ([m("P_A"), m("P_B")], {"R_Done": "why"}, "R_X: an action of ours"),
        ([m("P_A"), m("P_B"), m("R_X"), m("R_Done")], {"R_Done": "why"}, "CODELESS but marked"),
        ([m("P_A"), m("P_B"), m("R_X")], {"R_Done": "why", "Gone": "why"}, "not an action of Next"),
    ],
    ids=["unknown-marker", "unmarked-engine", "unmarked-reader", "codeless-marked", "codeless-non-action"],
)
def test_must_not_pass(found: list[Marker], codeless: dict[str, str], needle: str) -> None:
    out = problems(TOY, found, codeless)
    assert any(needle in p for p in out), out
