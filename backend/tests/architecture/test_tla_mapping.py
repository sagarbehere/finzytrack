"""The TLA+ model's code map, checked both ways (ADR-0050; ARCHITECTURE.md §8.3 "Code mapping"; BL-0109; S0053).

Each engine and app action of `formal/tla/engine-jobs/EngineJobs.tla` (the actions of its `EngineStep` and `AppStep`,
and `AdviceTimer`, which `MAPPING.md` §1.2 lists among the app's actions) names the code that carries it out: a
function with a no-op `@tla_action("<Action>")` marker (`app.platform.tla` on the main side; the engine gets its own
marker with its first marked action). User, OS and outside-editor actions need none, though the app's side of a user
action may carry one. The check fails:
- on a marker that names no action of `Next`;
- on an engine or app action with no marker and no row in `PLANNED`;
- on a `PLANNED` action that is marked (the row is stale), or a `PARTIAL` action that is not;
- on a `PLANNED` or `PARTIAL` row whose backlog ID is not an open or in-progress row of `design/backlog.md`.

`PLANNED` names, for each action not built yet, the backlog row of the BL-0109 piece that builds it; `PARTIAL` names
the row that builds the rest of an action whose marked function carries only part of it. Both shrink to nothing as
the pieces land. The markers are read from the source (AST), never by importing the engine (ADR-0054).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.platform.tla import tla_action

REPO = Path(__file__).resolve().parents[3]
MODEL = REPO / "formal" / "tla" / "engine-jobs" / "EngineJobs.tla"
BACKLOG = REPO / "design" / "backlog.md"
SOURCES = (REPO / "backend" / "src", REPO / "engine" / "src")
APP_ACTIONS_OUTSIDE_APPSTEP = frozenset({"AdviceTimer"})  # MAPPING.md §1.2: an app action, listed in EnvStep

SAVE = "BL-0151"  # the save, rollback and recover-all over the fake file system
R4_READS = "BL-0152"  # R4, the reads and the open
CREATE_COPY = "BL-0153"  # create and copy
SWITCH = "BL-0154"  # the switch, Reopen, quit, boot and update
SHELL = "BL-0150"  # the client's §5.5 changes and the scheduler shell

PLANNED: Mapping[str, str] = {
    **{a: SHELL for a in ("DispatchUnavail", "AppBgSave")},
    **{
        a: SAVE
        for a in [
            "S_R4Check",
            "S_WaitR4",
            "S_Locks",
            "S_Gate",
            "S_Load",
            "S_Recheck",
            "S_Backup",
            "S_BackupSync",
            "S_JPart",
            "S_JPartSync",
            "S_JOpen",
            "S_JDirSync",
            "S_TempStart",
            "S_TempDone",
            "S_TempSync",
            "S_Swap",
            "S_Compare",
            "S_DirSync",
            "S_ReadBack",
            "S_RemoveTemps",
            "S_Close",
            "S_CloseSync",
            "S_Saved",
            "S_Publish",
            "S_Release",
            "RB_Check",
            "RS_Write",
            "RS_Swap",
            "RS_Compare",
            "RS_Temps",
            "RS_Sync",
            "RS_Resolve",
            "RC_Init",
            "RC_Next",
            "RC_Lock",
            "RC_Check",
        ]
    },
    **{
        a: R4_READS
        for a in [
            "R4_Pick",
            "R4_Start",
            "R4_Load",
            "R4_Check",
            "R4_Confirm",
            "R4_Sync",
            "RD_Load",
            "RD_Check",
            "OP_Load",
            "OP_Check",
        ]
    },
    "AppFocusCheck": R4_READS,
    **{
        a: CREATE_COPY
        for a in [
            "CP_Check",
            "CP_Load",
            "CP_Manifest",
            "CR_Target",
            "CR_WriteA",
            "CR_SyncA",
            "CR_ReadBack",
            "CR_Load",
            "CR_ManifestTemp",
            "CR_ManifestRename",
            "CR_ManifestSync",
            "CR_Register",
        ]
    },
    **{a: SWITCH for a in ("CloseA", "QuitClose", "Assess")},
}

PARTIAL: Mapping[str, str] = {
    "Dispatch": SHELL,  # the engine's Unavailable, fed in by the shell (the guard `~Unavail`)
    "SpawnStep": SHELL,  # its Unavailable branch; recover-all's `not_tried` marks with BL-0151
    "AdviceTimer": SHELL,  # the shell arms the timer and shows the text
    "S_Commit": SHELL,  # the engine's `gate` and its wait for `proceed` (protocol)
    "CR_Commit": SHELL,
    "CP_Commit": SHELL,
    "FreshEnd": SAVE,  # the save's and R4's own engine changes call it
    "UserCancelRunning": SAVE,  # recover-all's Cancel per journal (§8.1)
    "UserCancelQueued": SWITCH,  # the switch's or boot's cancelled screen (§9.1 step 3, §9.2)
}


@dataclass(frozen=True, slots=True)
class Marker:
    action: str
    where: str


def defined_operators(model: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\([^)]*\))?\s*==", model, re.MULTILINE))


def step_actions(model: str, group: str) -> set[str]:
    """The actions a disjunction of `Next` names (`EngineStep`, `AppStep`, `UserStep`, `EnvStep`)."""
    match = re.search(rf"^{group}\s*==\s*\n(.*?)(?:\n\s*\n)", model, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{group} not found in the model"
    # each disjunct is an action, possibly under an existential: `\/ A`, `\/ \E i \in S : A(i)`
    names = set(re.findall(r"\\/\s*(?:\\E\s+[^:]+:\s*)?([A-Za-z_][A-Za-z0-9_]*)", match.group(1)))
    return names & defined_operators(model)


def markers(roots: Iterable[Path]) -> list[Marker]:
    """Every `@tla_action(...)` decorator in the Python sources under `roots`, read from the AST."""
    found: list[Marker] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                for deco in node.decorator_list:
                    if not isinstance(deco, ast.Call):
                        continue
                    func = deco.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                    if name != "tla_action":
                        continue
                    shown = path.relative_to(REPO) if path.is_relative_to(REPO) else path
                    where = f"{shown}:{node.lineno} {node.name}"
                    for arg in deco.args:
                        literal = isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        assert literal, f"{where}: a marker names actions as string literals"
                        assert isinstance(arg, ast.Constant)
                        assert isinstance(arg.value, str)
                        found.append(Marker(arg.value, where))
    return found


def open_rows(backlog: str) -> set[str]:
    rows = re.findall(r"^\| (BL-\d{4}) \|.*\| (open|in-progress) \|", backlog, re.MULTILINE)
    return {row for row, _ in rows}


def problems(
    model: str, found: Iterable[Marker], planned: Mapping[str, str], partial: Mapping[str, str], live: set[str]
) -> list[str]:
    every = set[str]().union(*(step_actions(model, g) for g in ("EngineStep", "AppStep", "UserStep", "EnvStep")))
    required = step_actions(model, "EngineStep") | step_actions(model, "AppStep") | APP_ACTIONS_OUTSIDE_APPSTEP
    marked = {m.action for m in found}
    out = [f"{m.where}: @tla_action({m.action!r}) names no action of Next" for m in found if m.action not in every]
    out += [
        f"{a}: an engine or app action with no @tla_action and no PLANNED row"
        for a in sorted(required)
        if a not in marked | set(planned)
    ]
    out += [f"{a}: PLANNED but marked; drop its row (or move it to PARTIAL)" for a in sorted(set(planned) & marked)]
    out += [f"{a}: PARTIAL but not marked" for a in sorted(set(partial) - marked)]
    out += [
        f"{a}: a PLANNED or PARTIAL row that is not an action of Next"
        for a in sorted((set(planned) | set(partial)) - every)
    ]
    rows = {**planned, **partial}
    out += [f"{a}: cites {bl}, which is not an open backlog row" for a, bl in sorted(rows.items()) if bl not in live]
    return out


# -- the real repository ----------------------------------------------------------------------------------------------


def test_the_code_map_holds_both_ways() -> None:
    model = MODEL.read_text(encoding="utf-8")
    found = markers(SOURCES)
    assert problems(model, found, PLANNED, PARTIAL, open_rows(BACKLOG.read_text(encoding="utf-8"))) == []
    assert found, "no marker found: the scan is broken"


def test_the_required_set_is_the_models_engine_and_app_actions() -> None:
    model = MODEL.read_text(encoding="utf-8")
    required = step_actions(model, "EngineStep") | step_actions(model, "AppStep")
    assert {"Dispatch", "S_Commit", "RC_Check", "CR_Register", "SpawnStep", "QuitClose"} <= required
    assert not required & {"UserSave", "EngineCrash", "PowerLoss", "Editor"}
    assert len(required) >= 60


def test_the_marker_changes_nothing() -> None:
    def f(x: int) -> int:
        return x + 1

    assert tla_action("Dispatch")(f) is f


# -- must not pass (synthetic inputs) ---------------------------------------------------------------------------------

TOY = """
A_One == x' = 1
A_Two(i) == x' = i
B == TRUE
U == TRUE
EngineStep ==
  \\/ A_One \\/ \\E i \\in S : A_Two(i)

AppStep ==
  \\/ B

UserStep ==
  \\/ U

EnvStep ==
  \\/ AdviceTimer

AdviceTimer == TRUE
"""


def m(action: str) -> Marker:
    return Marker(action, "toy.py:1 f")


def test_toy_model_with_everything_marked_passes() -> None:
    found = [m("A_One"), m("A_Two"), m("B"), m("AdviceTimer")]
    assert problems(TOY, found, {}, {}, set[str]()) == []


@pytest.mark.parametrize(
    ("found", "planned", "partial", "live", "needle"),
    [
        ([m("A_One"), m("A_Two"), m("B"), m("AdviceTimer"), m("Nope")], {}, {}, set[str](), "names no action"),
        ([m("A_One"), m("B"), m("AdviceTimer")], {}, {}, set[str](), "A_Two: an engine or app action"),
        ([m("A_One"), m("A_Two"), m("B")], {}, {}, set[str](), "AdviceTimer: an engine or app action"),
        ([m("A_One"), m("A_Two"), m("B"), m("AdviceTimer")], {"B": "BL-0001"}, {}, {"BL-0001"}, "PLANNED but marked"),
        ([m("A_One"), m("A_Two"), m("AdviceTimer")], {}, {"B": "BL-0001"}, {"BL-0001"}, "PARTIAL but not marked"),
        ([m("A_One"), m("A_Two"), m("AdviceTimer")], {"B": "BL-0001"}, {}, set[str](), "not an open backlog row"),
        ([m("A_One"), m("A_Two"), m("B"), m("AdviceTimer")], {"Gone": "BL-0001"}, {}, {"BL-0001"}, "not an action"),
    ],
    ids=[
        "unknown-marker",
        "unmarked-engine",
        "unmarked-advice",
        "stale-planned",
        "partial-unmarked",
        "closed-row",
        "planned-non-action",
    ],
)
def test_must_not_pass(
    found: list[Marker], planned: dict[str, str], partial: dict[str, str], live: set[str], needle: str
) -> None:
    out = problems(TOY, found, planned, partial, live)
    assert any(needle in p for p in out), out


def test_a_marker_naming_an_action_with_a_non_literal_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("NAME = 'Dispatch'\n@tla_action(NAME)\ndef f() -> None: ...\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="string literals"):
        markers([tmp_path])


def test_markers_are_found_on_functions_methods_and_attribute_forms(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text(
        "@tla_action('A', 'B')\ndef f() -> None: ...\n"
        "class C:\n    @tla.tla_action('C')\n    def g(self) -> None: ...\n",
        encoding="utf-8",
    )
    assert sorted(x.action for x in markers([tmp_path])) == ["A", "B", "C"]


def test_open_rows_reads_only_open_and_in_progress_rows() -> None:
    text = (
        "| BL-0001 | a | x | M1a | open | — |\n"
        "| BL-0002 | b | x | M1a | done | — |\n"
        "| BL-0003 | c | x | M1a | in-progress | — |\n"
    )
    assert open_rows(text) == {"BL-0001", "BL-0003"}
