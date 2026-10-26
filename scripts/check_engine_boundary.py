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
5. Inside `engine/src/`, only the gateway imports, runs or evaluates code by name: any use of `import_module`,
   `__import__`, `runpy` (`run_module`, `run_path`), `pkgutil.resolve_name`, `exec`, `eval`, `exec_module` or
   `load_module` is refused elsewhere, whatever its argument (EP-30: a computed name cannot be checked, so the
   engine's own code may not use one; S0035 independent verifier F5).

An import is `import x`, `from x import y` (absolute), or a call with a literal name, also through an alias or a
keyword argument: `import_module`, `__import__`, `runpy.run_module`, `pkgutil.resolve_name`, and `exec`/`eval` of a
literal source (its imports). Stated limit outside `engine/src/`: a computed name (`import_module(name)`) is not
seen; review covers it. A file that does not parse fails closed. Files: what git tracks plus untracked files it does
not ignore.
Usage: python3 scripts/check_engine_boundary.py   (exit 0 = boundary holds, 1 = violations)
"""

from __future__ import annotations

import ast
import contextlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEANCOUNT = frozenset({"beancount", "beanquery"})
BEANCOUNT_HOMES = ("engine/", "scripts/oracle/", "corpora/")
GATEWAY = "engine/src/ledger_engine/ledger/beancount_port.py"
ENGINE_SOURCE = "engine/src/"
# Calls that import a module named by their argument, and the keyword that names it.
BY_NAME = {"import_module": "name", "__import__": "name", "run_module": "mod_name", "resolve_name": "name"}
RUNS_SOURCE = frozenset({"exec", "eval"})
DYNAMIC = frozenset({*BY_NAME, *RUNS_SOURCE, "run_path", "exec_module", "load_module"})
DYNAMIC_MODULES = frozenset({"runpy"})


def _callee(node: ast.Call, aliases: dict[str, str]) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return aliases.get(func.id, func.id)
    return ""


def _literal_arg(node: ast.Call, keyword: str) -> str | None:
    value = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == keyword), None)
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _aliases(tree: ast.AST) -> dict[str, str]:
    """`from m import f as g`: g is f."""
    return {
        a.asname: a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for a in node.names
        if a.asname is not None
    }


def imported_roots(source: str) -> set[str]:
    """The top-level package of every module the source imports, statically or by a literal dynamic import."""
    tree = ast.parse(source)
    aliases = _aliases(tree)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            name = _callee(node, aliases)
            if name in BY_NAME and (text := _literal_arg(node, BY_NAME[name])) is not None:
                roots.add(text.split(":")[0].split(".")[0])
            elif name in RUNS_SOURCE and (text := _literal_arg(node, "source")) is not None:
                with contextlib.suppress(SyntaxError, ValueError):
                    roots |= imported_roots(text)
    return roots


def dynamic_uses(source: str) -> set[str]:
    """Every mention of a way to import, run or evaluate code by name (rule 5): a name, an attribute, an imported
    name, a `getattr` of one by a literal, or the `runpy` module."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in DYNAMIC:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in DYNAMIC:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found |= {a.name for a in node.names if a.name in DYNAMIC}
            if node.module and node.module.split(".")[0] in DYNAMIC_MODULES:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] in DYNAMIC_MODULES}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            found |= {
                a.value for a in node.args[1:2] if isinstance(a, ast.Constant) and a.value in DYNAMIC
            }
    return found


def rule_violations(rel: str, roots: set[str], dynamic: set[str] | None = None) -> list[str]:
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
    if dynamic and rel.startswith(ENGINE_SOURCE) and rel != GATEWAY:
        out.append(f"{rel}: uses {sorted(dynamic)}; inside engine/src only {GATEWAY} may import or run code by name")
    return out


def violations(root: Path, paths: list[str]) -> list[str]:
    out: list[str] = []
    for rel in paths:
        try:
            source = (root / rel).read_text(encoding="utf-8")
            roots, dynamic = imported_roots(source), dynamic_uses(source)
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            out.append(f"{rel}: cannot be parsed, so its imports cannot be checked ({type(e).__name__})")
            continue
        out += rule_violations(rel, roots, dynamic)
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
