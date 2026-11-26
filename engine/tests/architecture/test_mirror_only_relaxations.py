"""The relaxation S0132 gave the mirror's publish stays the publish's alone (BL-0294; S0132 round 1, red team F2):
clearing Windows' read-only attribute (`DirHandle.clear_readonly`). The ledger's write path relies on the opposite: a
read-only file is refused (`locked`). Checked from the source (AST): outside `ledger_engine/mirror/publish.py` no
module calls `clear_readonly` (the adapters may define it). S0132's other relaxation, a network share's refused folder
sync taken as done (`SyncRefused`), was removed in S0134 with the refusal of a cache folder on a share (owner,
2026-11-19: "Let's remove the best effort sync code, but only if it's dead code and unreachable").

S0152 (BL-0283; owner, 2026-11-26: "Clear it, then remove (Recommended)"; ADR-0069 amendment of that date): the
ledger's write path clears the attribute in one form only, `DirHandle.rmdir(name, made=True)`, on a folder the caller's
own job made (the undo's folders the record names, the create's folders it made and checked by identity). Checked from
the source too: `made=` appears only at the reviewed sites in `REVIEWED_MADE`, always the literal `True`, never on any
other call.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ledger_engine"
HOME = "mirror/publish.py"


def relaxations(source: str) -> list[str]:
    """Each call of `clear_readonly`, with its line."""
    return [
        f"clear_readonly call, line {node.lineno}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "clear_readonly"
    ]


def test_only_the_publish_clears_the_read_only_attribute() -> None:
    homes: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        found = relaxations(path.read_text(encoding="utf-8"))
        if rel == HOME:
            homes.extend(found)
            continue
        assert not found, f"{rel}: {found}: only {HOME} may clear the attribute (S0132)"
    assert homes  # the check sees the real sites


def test_the_check_sees_a_planted_call() -> None:
    """The must-not-pass case (EP-29): the red team's repro, a `clear_readonly` call in the writer."""
    assert relaxations("def f(d):\n    d.clear_readonly('main.bean')\n") == ["clear_readonly call, line 2"]


REVIEWED_MADE = {
    "ledger/restore.py": 1,  # `_restore_all`: a folder the record's `created_dirs` names (one holding an entry: kept)
    "ledger/create.py": 2,  # `Run._cleanup`: a folder the run made (its identity checked), and the target it made
}
"""Each module that may pass `made=`, with its number of calls (S0152)."""


def made_calls(source: str) -> list[str]:
    """Each `rmdir` call with a `made=` keyword (or a `**` mapping, which could carry one), with its line and whether
    the value is the literal `True`. (`restore.restore`'s own `made`, the folders a save made, is another parameter.)"""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "rmdir"):
            continue
        for kw in node.keywords:
            if kw.arg in ("made", None):
                literal = kw.arg == "made" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                out.append(f"rmdir(made={'True' if literal else 'not literal True'}), line {node.lineno}")
    return out


def made_problems(found: dict[str, list[str]]) -> list[str]:
    """What is wrong with the `made=` calls `found` (module -> calls): a module or a number of calls other than
    `REVIEWED_MADE`'s, or a value that is not the literal `True`."""
    counts = {rel: len(calls) for rel, calls in found.items()}
    out = [f"sites {counts}, reviewed {REVIEWED_MADE}"] if counts != REVIEWED_MADE else []
    return out + [f"{rel}: {c}" for rel, calls in found.items() for c in calls if not c.startswith("rmdir(made=True)")]


def found_in_source() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("fs/"):
            continue  # the adapters define `rmdir(..., made=...)`
        calls = made_calls(path.read_text(encoding="utf-8"))
        if calls:
            found[rel] = calls
    return found


def test_only_the_reviewed_sites_remove_a_folder_as_made() -> None:
    assert made_problems(found_in_source()) == []


def test_the_made_check_refuses_a_new_site_an_extra_call_a_missing_one_and_a_computed_value() -> None:
    """The must-not-pass cases (EP-29) of `made_problems`."""
    real = found_in_source()
    ok = "rmdir(made=True), line 1"
    assert made_problems({**real, "ledger/writer.py": [ok]})
    assert made_problems({**real, "ledger/create.py": [ok, ok, ok]})
    assert made_problems({rel: c for rel, c in real.items() if rel != "ledger/restore.py"})
    assert made_problems({**real, "ledger/restore.py": ["rmdir(made=not literal True), line 1"]})


def test_the_made_check_sees_a_planted_call_and_a_value_that_is_not_literal_true() -> None:
    """The must-not-pass cases (EP-29): a new site, and a computed value."""
    assert made_calls("def f(d):\n    d.rmdir('x', made=True)\n") == ["rmdir(made=True), line 2"]
    assert made_calls("def f(d, ok):\n    d.rmdir('x', made=ok)\n") == ["rmdir(made=not literal True), line 2"]
    assert made_calls("def f(d, kw):\n    d.rmdir('x', **kw)\n") == ["rmdir(made=not literal True), line 2"]
    assert made_calls("def f(d):\n    d.rmdir('x', made=False)\n") == ["rmdir(made=not literal True), line 2"]
    assert made_calls("def f(d):\n    d.rmdir('x')\n") == []
