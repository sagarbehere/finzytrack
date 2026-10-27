#!/usr/bin/env python3
"""Generated artifacts are never hand-edited (INV-19).

Every generated file is listed in GENERATORS with the command that produces it on stdout. The check:
  --staged  a staged generated file must carry its GENERATED header and match a fresh generation; if a
            generator's source is staged, its output must be regenerated in the same commit;
  --all     regenerate everything and compare with the working tree;
  --only P  check one generated path.
Everything under design/generated/ must be listed here. The header is the first line, or for a JSON file a
top-level `$comment` key that comes first. A generator that cannot run (the engine's environment not synced) is a
problem, not a crash. Exit 0 = ok, 1 = stale, hand-edited or not checkable.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The engine's own environment (ADR-0054): its schema is generated from models only that environment can import.
ENGINE_PYTHON = ROOT / ("engine/.venv/Scripts/python.exe" if os.name == "nt" else "engine/.venv/bin/python")
# The backend's own environment: the statechart references are generated from tables only it can import (S0040).
BACKEND_PYTHON = ROOT / ("backend/.venv/Scripts/python.exe" if os.name == "nt" else "backend/.venv/bin/python")
# generated path -> (command printing the expected content, source paths; a source ending in "/" is a folder and stands
# for every path under it)
GENERATORS: dict[str, tuple[list[str], list[str]]] = {
    "design/capabilities.md": (
        [sys.executable, "scripts/gen_capabilities.py", "--stdout"],
        ["design/capabilities.toml", "scripts/gen_capabilities.py"],
    ),
    "design/specs/write-path/intent-table.md": (
        [sys.executable, "scripts/gen_intent_table.py", "--stdout"],
        ["design/specs/write-path/intent-table.toml", "scripts/gen_intent_table.py"],
    ),
    "design/specs/mirror/surface.md": (
        [sys.executable, "scripts/gen_mirror_surface.py", "--stdout"],
        ["design/specs/mirror/surface.toml", "scripts/gen_mirror_surface.py"],
    ),
    "contracts/engine-protocol.schema.json": (
        [str(ENGINE_PYTHON), "-I", "-m", "ledger_engine.protocol.schema"],
        [
            "engine/src/ledger_engine/protocol/messages.py",
            "engine/src/ledger_engine/protocol/schema.py",
            "engine/pyproject.toml",
            "engine/uv.lock",
        ],
    ),
    "design/generated/statecharts/engine-supervision.md": (
        [str(BACKEND_PYTHON), "-I", "backend/scripts/gen_statecharts.py", "--stdout"],
        ["backend/src/app/platform/engine_supervision.py", "backend/scripts/gen_statecharts.py"],
    ),
    # The API contract, exported offline from the app (ADR-0038, S0046): any module of the app can change a schema,
    # a route or an error code, so the whole package is its source.
    "contracts/openapi.json": (
        [str(BACKEND_PYTHON), "-I", "backend/scripts/export_openapi.py", "--stdout"],
        ["backend/src/app/", "backend/scripts/export_openapi.py", "backend/pyproject.toml", "backend/uv.lock"],
    ),
    # Its TypeScript types (openapi-typescript), generated from the contract in the same commit (ADR-0038, S0046).
    "frontend/src/api/generated/openapi.ts": (
        ["node", "frontend/scripts/gen-api-types.mjs", "--stdout"],
        ["contracts/openapi.json", "frontend/scripts/gen-api-types.mjs", "frontend/package.json", "frontend/pnpm-lock.yaml"],
    ),
}
MARK = "GENERATED"


class GeneratorFailed(Exception):
    pass


def expected(path: str) -> str:
    cmd, _ = GENERATORS[path]
    try:
        return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        detail = e.stderr.strip().splitlines()[-1:] if isinstance(e, subprocess.CalledProcessError) and e.stderr else [str(e)]
        raise GeneratorFailed(f"{' '.join(cmd)}: {' '.join(detail)}") from e


def has_header(path: str, text: str) -> bool:
    """A JSON file carries its header as the first key of its top-level object, `$comment`; any other file on
    its first line."""
    if path.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            return False
        return isinstance(data, dict) and next(iter(data), None) == "$comment" and MARK in str(data["$comment"])
    first_line = text.splitlines()[0] if text else ""
    return MARK in first_line


def current(path: str, staged: bool) -> str | None:
    if staged:
        r = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    p = ROOT / path
    return p.read_text(encoding="utf-8") if p.exists() else None


def check_one(path: str, staged: bool) -> list[str]:
    have = current(path, staged)
    if have is None:
        return [f"{path}: missing; run its generator"]
    problems = []
    if not has_header(path, have):
        where = "a top-level `$comment` first" if path.endswith(".json") else "its first line"
        problems.append(f"{path}: the GENERATED header must be {where}")
    try:
        fresh = expected(path)
    except GeneratorFailed as e:
        return [*problems, f"{path}: the generator could not run ({e}); for the engine or the backend, run `uv sync --locked` in engine/ or backend/; for the frontend, `pnpm install --frozen-lockfile` in frontend/"]
    if have != fresh:
        problems.append(f"{path}: out of date or hand-edited; regenerate it ({' '.join(GENERATORS[path][0][1:])})")
    return problems


def triggered(staged: set[str], path: str, sources: list[str]) -> bool:
    """Whether a staged change can change `path`: the output itself, a source file, or a path under a source folder."""
    if path in staged:
        return True
    files = {s for s in sources if not s.endswith("/")}
    folders = tuple(s for s in sources if s.endswith("/"))
    return bool(staged & files) or any(p.startswith(folders) for p in staged if folders)


def staged_paths() -> set[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return {p for p in out.split("\0") if p}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    problems: list[str] = []
    unlisted = [str(p.relative_to(ROOT)) for p in (ROOT / "design/generated").rglob("*")
                if p.is_file() and p.name != "README.md" and str(p.relative_to(ROOT)) not in GENERATORS] \
        if (ROOT / "design/generated").exists() else []
    problems += [f"{u}: under design/generated/ but not registered in scripts/check_generated.py" for u in unlisted]
    if args.only:
        problems += check_one(args.only, staged=False)
    elif args.staged:
        staged = staged_paths()
        for path, (_, sources) in GENERATORS.items():
            if triggered(staged, path, sources):
                problems += check_one(path, staged=True)
    else:
        for path in GENERATORS:
            problems += check_one(path, staged=False)
    for p in problems:
        print(f"check-generated: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
