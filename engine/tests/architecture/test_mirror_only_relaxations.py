"""The relaxation S0132 gave the mirror's publish stays the publish's alone (BL-0294; S0132 round 1, red team F2):
clearing Windows' read-only attribute (`DirHandle.clear_readonly`). The ledger's write path relies on the opposite: a
read-only file is refused (`locked`). Checked from the source (AST): outside `ledger_engine/mirror/publish.py` no
module calls `clear_readonly` (the adapters may define it). S0132's other relaxation, a network share's refused folder
sync taken as done (`SyncRefused`), was removed in S0134 with the refusal of a cache folder on a share (owner,
2026-11-19: "Let's remove the best effort sync code, but only if it's dead code and unreachable").
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
