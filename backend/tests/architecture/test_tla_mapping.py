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
the pieces land. The markers are read from the source (AST), never by importing the engine (ADR-0054). The step
reader refuses a disjunct it cannot read rather than dropping it (S0053 verifier F9).

Limit: the check sees which actions are marked, not whether the marked function is the one that carries the action
out. A marker on the wrong function passes; the conformance replay (S0059,
`tests/platform/test_engine_jobs_conformance.py`) runs the model's traces through the table and the shell and catches
wrong behaviour, whichever function holds it.
(Until S0058 `SpawnStep` sat on `_engine_needed`, which records the ask; it is now on `_spawn_step`, which starts the
fresh engine or, while the engine is unavailable, ends the job.)
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
MARKED_GROUPS = ("EngineStep", "AppStep")  # their actions are the code's: each needs a marker or a PLANNED row
FREE_GROUPS = ("UserStep", "EnvStep")  # user, OS and outside-editor actions: no marker needed

R4_READS = "BL-0152"  # the reads and the open (BL-0152 part 2; R4 itself landed in S0066)
R4_REPLAY = "BL-0168"  # R4 walked by the save conformance replay (S0066 left it to the sidecar's tests)
CREATE_COPY = "BL-0153"  # create and copy
SWITCH = "BL-0154"  # the switch, Reopen, quit, boot and update

RECOVER_ALL = "BL-0164"  # recover-all's per-journal tries replayed through the engine (after BL-0161)
PUBLISH = "BL-0113"  # the save's publish of the mirror (§6.1 step 10)

PLANNED: Mapping[str, str] = {
    "S_WaitR4": R4_REPLAY,  # a seeded bug's action only (the save waits for the queued R4); R4 runs inside the save
    # (S0066), so no code carries this action: it stays planned only for the replay that will walk R4 (BL-0168)
    "S_Publish": PUBLISH,
    **{a: RECOVER_ALL for a in ["RC_Init", "RC_Next", "RC_Lock", "RC_Check"]},
    **{
        a: R4_READS
        for a in [
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
"""Since S0064 (BL-0151 slice 5) the save's steps, its rollback and the restore are marked in the engine
(`ledger_engine.tla`); those `engine/tests/ledger/test_save_conformance.py` walks through the code (its `WALKED`)
have no row here, the others are `PARTIAL` below (its `NOT_WALKED`)."""

MODEL_UPDATE = "BL-0161"  # the model after the kept-name design and the owner's 2026-10-30 rules

PARTIAL: Mapping[str, str] = {
    "DispatchUnavail": SWITCH,  # the quit notes a recover-all that never ran answers (§9.3); its `not_tried` marks
    # are the sidecar's "nothing listed: every open blocked" since S0062 (`recovery.blocking_for`)
    "CR_Commit": CREATE_COPY,
    "CP_Commit": CREATE_COPY,
    "UserCancelQueued": SWITCH,  # the switch's or boot's cancelled screen (§9.1 step 3, §9.2)
    "S_R4Check": R4_REPLAY,  # the replay walks "no R4 owed"; an owed R4 is run by the sidecar between two `save`
    # requests (S0066, `test_r4_job.py`), which the engine's save replay does not walk yet (BL-0168)
    "S_Compare": MODEL_UPDATE,  # a read with no hook: the replay sees only that the code goes on when the swap
    # displaced our own bytes; an outside save goes to a kept name in the code, the model swaps it back (BL-0161), so
    # the kept-name branch is `test_writer.py`'s (it kills "always ours")
    "RS_Compare": MODEL_UPDATE,  # walked when the restore's swap displaced our own bytes; the other branch as above
    "RB_Check": MODEL_UPDATE,  # reads with no hook; the code's staging-name rule differs from the model's old one, and
    # the code checks a created path (`created_path_taken`), which the model removes (BL-0161; `test_restore.py`)
    "S_Backup": MODEL_UPDATE,  # the copy and its sync walked; the re-hash of the file is the code's (the model copies
    # the plan's bytes unread: the stop `backup_check`, BL-0161), killed by `test_writer.py` (S0064 round 1, F2)
    "S_ReadBack": MODEL_UPDATE,  # a read with no hook; the model's read-back fails only after an edit past the swap,
    # which reaches a swap-back first (BL-0161); a skipped read-back is killed by `test_writer.py` (round 1, F2)
}


@dataclass(frozen=True, slots=True)
class Marker:
    action: str
    where: str


DISJUNCT = re.compile(r"(?:\\E\s+[^:]+:\s*)*([A-Za-z_][A-Za-z0-9_]*)(?:\([^()]*\))?")
"""One disjunct of a step: an operator, applied or not, under any number of existentials."""


def defined_operators(model: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\([^)]*\))?\s*==", model, re.MULTILINE))


def definition_body(model: str, name: str) -> str:
    """The body of the top-level definition `name ==`: the rest of its line and every following line until the next
    line that starts in the first column (the next definition), blank lines included; TLA+ line comments dropped.
    A group is read whole, however its lines are spaced (S0053 verifier R2-F2)."""
    lines = model.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(rf"{name}\s*==", line)]
    assert len(starts) == 1, f"{name}: defined {len(starts)} times in the model, not once"
    first = starts[0]
    body = [lines[first].split("==", 1)[1]]
    for line in lines[first + 1 :]:
        if line[:1] not in ("", " ", "\t"):
            break
        body.append(line)
    return "\n".join(re.sub(r"\\\*.*$", "", line) for line in body)


def disjuncts(model: str, name: str) -> list[str]:
    """The disjuncts of the definition `name`, with or without a leading `\\/`; an empty one is kept, for the caller
    to refuse."""
    parts = [part.strip() for part in re.split(r"\\/", definition_body(model, name))]
    return parts[1:] if parts[0] == "" else parts


def step_actions(model: str, group: str) -> set[str]:
    """The actions a group of `Next` names. Each disjunct must be an operator of the model, applied or not, possibly
    under existentials (`\\/ A`, `\\/ A(x)`, `\\/ \\E i \\in S : \\E j \\in T : A(i, j)`); any other form is refused
    rather than dropped, since a dropped disjunct would leave its action out of the required set."""
    defined = defined_operators(model)
    names: set[str] = set()
    for part in disjuncts(model, group):
        read = DISJUNCT.fullmatch(part)
        assert read is not None, (
            f"{group}: cannot read the disjunct {part!r}; write it as an action, or extend the reader"
        )
        assert read.group(1) in defined, f"{group}: cannot read the disjunct {part!r}: no operator of that name"
        names.add(read.group(1))
    return names


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


def next_groups(model: str) -> list[str]:
    """The groups `Next` is the disjunction of, every one of them, each a bare operator name."""
    groups = disjuncts(model, "Next")
    for group in groups:
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", group), f"Next: cannot read the disjunct {group!r} as a group"
    return groups


def problems(
    model: str, found: Iterable[Marker], planned: Mapping[str, str], partial: Mapping[str, str], live: set[str]
) -> list[str]:
    groups = {g: step_actions(model, g) for g in next_groups(model)}
    every = set[str]().union(*groups.values())
    required = set[str]().union(*(groups.get(g, set[str]()) for g in MARKED_GROUPS)) | APP_ACTIONS_OUTSIDE_APPSTEP
    marked = {m.action for m in found}
    out = [
        f"{g}: a group of Next the code map does not classify (actions {sorted(acts)}); add it to MARKED_GROUPS "
        "or FREE_GROUPS"
        for g, acts in groups.items()
        if g not in (*MARKED_GROUPS, *FREE_GROUPS)
    ]
    out += [
        f"{a}: a group of Next the code map expects is missing"
        for a in (*MARKED_GROUPS, *FREE_GROUPS)
        if a not in groups
    ]
    out += [f"{m.where}: @tla_action({m.action!r}) names no action of Next" for m in found if m.action not in every]
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

Next == EngineStep \\/ AppStep \\/ UserStep \\/ EnvStep
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


def test_a_blank_line_inside_a_group_does_not_end_it() -> None:
    """S0053 verifier R2-F2: a group is read up to the next definition, not to its first blank line; an action after
    a blank line is still required."""
    model = TOY.replace("AppStep ==\n  \\/ B\n", "AppStep ==\n  \\/ B\n\n  \\/ A_Late\n").replace(
        "B == TRUE", "B == TRUE\nA_Late == TRUE"
    )
    assert "\\/ B\n\n  \\/ A_Late" in model
    assert step_actions(model, "AppStep") == {"B", "A_Late"}
    out = problems(model, [m("A_One"), m("A_Two"), m("B"), m("AdviceTimer")], {}, {}, set[str]())
    assert any("A_Late: an engine or app action" in p for p in out), out


def test_a_comment_in_a_group_is_not_read_as_a_disjunct() -> None:
    model = TOY.replace("AppStep ==\n  \\/ B\n", "AppStep ==\n  \\/ B \\* the app's own\n")
    assert step_actions(model, "AppStep") == {"B"}


def test_every_group_of_next_is_read_and_an_unclassified_one_fails() -> None:
    """S0053 verifier R2-F2: the groups come from `Next` itself; a fifth group fails until it is classified, so its
    actions are never left out of the required set unnoticed."""
    model = (
        TOY.replace("\\/ EnvStep\n", "\\/ EnvStep \\/ OrphanStep\n")
        + "\nOrphanStep ==\n  \\/ A_Orphan\n\nA_Orphan == TRUE\n"
    )
    assert next_groups(model)[-1] == "OrphanStep"
    out = problems(model, [m("A_One"), m("A_Two"), m("B"), m("AdviceTimer")], {}, {}, set[str]())
    assert any("OrphanStep: a group of Next the code map does not classify" in p and "A_Orphan" in p for p in out), out


def test_a_marked_group_missing_from_next_fails() -> None:
    model = TOY.replace("Next == EngineStep \\/ ", "Next == ")
    out = problems(model, [m("B"), m("AdviceTimer")], {}, {}, set[str]())
    assert any("EngineStep: a group of Next the code map expects is missing" in p for p in out), out


@pytest.mark.parametrize(
    "next_line", ["Next == EngineStep \\/ (AppStep)", "Next == EngineStep \\/ \\/ AppStep", "Next == EngineStep \\/"]
)
def test_a_next_disjunct_that_is_not_a_group_name_is_refused(next_line: str) -> None:
    model = re.sub(r"^Next ==.*$", lambda _: next_line, TOY, flags=re.MULTILINE)
    assert next_line in model
    with pytest.raises(AssertionError, match="cannot read"):
        next_groups(model)


def test_a_definition_given_twice_is_refused() -> None:
    """A second `Next` (or group) would let the reader pick the one without the new group (the verifier's repro of
    R2-F2 appends one); TLA+ refuses a redefinition, and so does the reader."""
    model = TOY + "\nNext == EngineStep \\/ AppStep \\/ UserStep \\/ EnvStep \\/ OrphanStep\n"
    with pytest.raises(AssertionError, match="defined 2 times"):
        next_groups(model)


def test_the_real_models_next_has_exactly_the_four_classified_groups() -> None:
    assert next_groups(MODEL.read_text(encoding="utf-8")) == [*MARKED_GROUPS, *FREE_GROUPS]


def test_nested_existentials_are_read_as_their_action() -> None:
    model = TOY.replace(
        "\\/ A_One \\/ \\E i \\in S : A_Two(i)", "\\/ A_One \\/ \\E i \\in S : \\E j \\in T : A_Two(i, j)"
    )
    assert "\\E j" in model
    assert step_actions(model, "EngineStep") == {"A_One", "A_Two"}
    out = problems(model, [m("A_One"), m("B"), m("AdviceTimer")], {}, {}, set[str]())
    assert any("A_Two: an engine or app action" in p for p in out), out


@pytest.mark.parametrize(
    "disjunct",
    ["(A_One)", "A_One /\\ x = 1", "IF c THEN A_One ELSE A_Two(1)", "\\E i \\in S : (A_Two(i))", "Undefined", ""],
    ids=["parenthesised", "conjunction", "if-then-else", "parenthesised-under-exists", "not-an-operator", "empty"],
)
def test_a_disjunct_the_reader_cannot_parse_is_refused_not_dropped(disjunct: str) -> None:
    """S0053 verifier F9: a disjunct the reader does not understand would drop an action from the required set, and
    the code map would pass with it unmarked. The reader refuses instead."""
    model = TOY.replace("AppStep ==\n  \\/ B", f"AppStep ==\n  \\/ B \\/ {disjunct}")
    assert disjunct in model
    with pytest.raises(AssertionError, match="cannot read"):
        step_actions(model, "AppStep")


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
