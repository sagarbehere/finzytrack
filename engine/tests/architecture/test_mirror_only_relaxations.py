"""The two relaxations S0132 gave the mirror's publish stay the publish's alone (BL-0294; S0132 round 1, red team F2):
clearing Windows' read-only attribute (`DirHandle.clear_readonly`) and taking a network share's refused folder sync as
done (`SyncRefused`). The ledger's write path relies on the opposite: a read-only file is refused (`locked`), and a
sync that did not happen fails the save. Checked from the source (AST): outside `ledger_engine/mirror/publish.py` no
module calls `clear_readonly`, and none catches `SyncRefused` (the adapters may define and raise them).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ledger_engine"
HOME = "mirror/publish.py"


def _names(node: ast.expr | None) -> set[str]:
    """The names an `except` clause catches (`X`, `mod.X`, a tuple of them)."""
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        out: set[str] = set()
        for e in node.elts:
            out |= _names(e)
        return out
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    return set()


def relaxations(source: str) -> list[str]:
    """Each call of `clear_readonly` and each `except` that names `SyncRefused`, with its line."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "clear_readonly":
            found.append(f"clear_readonly call, line {node.lineno}")
        elif isinstance(node, ast.ExceptHandler) and "SyncRefused" in _names(node.type):
            found.append(f"except SyncRefused, line {node.lineno}")
    return found


def test_only_the_publish_clears_the_read_only_attribute_or_takes_a_refused_sync() -> None:
    homes: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        found = relaxations(path.read_text(encoding="utf-8"))
        if rel == HOME:
            homes.extend(found)
            continue
        assert not found, f"{rel}: {found}: only {HOME} may clear the attribute or take a refused sync (S0132)"
    assert any(f.startswith("clear_readonly") for f in homes)  # the check sees the real sites
    assert any(f.startswith("except SyncRefused") for f in homes)


def test_the_check_sees_a_planted_call_and_catch() -> None:
    """The must-not-pass case (EP-29): the red team's repro, a `clear_readonly` call in the writer, and a catch."""
    planted = (
        "def f(d):\n"
        "    d.clear_readonly('main.bean')\n"
        "    try:\n"
        "        d.sync()\n"
        "    except (OSError, port.SyncRefused):\n"
        "        pass\n"
    )
    assert relaxations(planted) == ["clear_readonly call, line 2", "except SyncRefused, line 5"]
