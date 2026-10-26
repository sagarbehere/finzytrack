#!/usr/bin/env python3
"""The import boundary between the ledger engine and everything else (ADR-0054 decision 5; INV-02; ARCHITECTURE.md
§2.0 and §2.4 rule 4). Static and environment-independent; each environment adds its own `find_spec` checks
(`backend/tests/architecture/test_contracts.py`, `engine/tests/architecture/test_boundaries.py`).

1. `beancount` and `beanquery` are imported only inside `engine/`, `scripts/oracle/` (the oracle harness, its own
   Beancount environment, never shipped) and `corpora/` (plugin fixtures that run inside Beancount's loader).
2. Inside `engine/`, only `src/ledger_engine/ledger/beancount_port.py` imports them (INV-02's single gateway);
   the engine's tests may too (Beancount is their oracle).
3. Nothing in `engine/` imports the main sidecar's package `app`.
4. Nothing outside `engine/` imports the engine's package `ledger_engine`: the boundary is the protocol.

An import is `import x`, `from x import y` (absolute) or a call `import_module("x…")` / `__import__("x…")` with a
literal name. Stated limit: a computed name (`import_module(name)`) is not seen; review covers it. A file that does
not parse fails closed. Files: what git tracks plus untracked files it does not ignore.
Usage: python3 scripts/check_engine_boundary.py   (exit 0 = boundary holds, 1 = violations)
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEANCOUNT = frozenset({"beancount", "beanquery"})
BEANCOUNT_HOMES = ("engine/", "scripts/oracle/", "corpora/")
GATEWAY = "engine/src/ledger_engine/ledger/beancount_port.py"


def imported_roots(source: str) -> set[str]:
    """The top-level package of every module the source imports, statically or by a literal dynamic import."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    roots.add(first.value.split(".")[0])
    return roots


def rule_violations(rel: str, roots: set[str]) -> list[str]:
    out: list[str] = []
    in_engine = rel.startswith("engine/")
    beancount = sorted(roots & BEANCOUNT)
    if beancount and not rel.startswith(BEANCOUNT_HOMES):
        out.append(f"{rel}: imports {beancount}; only the engine may (ADR-0054)")
    if beancount and in_engine and not rel.startswith("engine/tests/") and rel != GATEWAY:
        out.append(f"{rel}: imports {beancount}; inside the engine only {GATEWAY} may (INV-02)")
    if in_engine and "app" in roots:
        out.append(f"{rel}: imports the main sidecar's package `app` (ADR-0054)")
    if not in_engine and "ledger_engine" in roots:
        out.append(f"{rel}: imports the engine's package `ledger_engine`; use the protocol (ADR-0054)")
    return out


def violations(root: Path, paths: list[str]) -> list[str]:
    out: list[str] = []
    for rel in paths:
        try:
            roots = imported_roots((root / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            out.append(f"{rel}: cannot be parsed, so its imports cannot be checked ({type(e).__name__})")
            continue
        out += rule_violations(rel, roots)
    return out


def python_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    return sorted(p for p in set(out.split("\0")) if p and (root / p).is_file())


def main(argv: list[str]) -> int:
    del argv
    found = violations(ROOT, python_files(ROOT))
    for v in found:
        print(f"check-engine-boundary: {v}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
