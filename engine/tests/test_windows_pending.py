"""The Windows skip inventory names real tests (S0127): a name that no longer exists, or a module that no longer
holds it, would skip nothing and hide nothing, but the inventory would claim a pending test that is gone."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.windows_pending import PENDING, POSIX_PATH_MODULES

TESTS = Path(__file__).resolve().parent


def _functions(module: str) -> set[str]:
    [path] = [p for p in TESTS.rglob(module) if "__pycache__" not in p.parts]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def test_every_pending_test_exists_in_its_module() -> None:
    missing = [f"{m}::{t}" for m, (_, tests) in PENDING.items() for t in sorted(tests - _functions(m))]
    assert missing == []


def test_every_posix_path_module_exists() -> None:
    assert all(len(list(TESTS.rglob(m))) == 1 for m in POSIX_PATH_MODULES)
