#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write: format and lint the touched file with the package's own tools.

Formatting is applied in place. Lint errors go back to the agent (exit 2) so it fixes them now rather
than at commit time. Files outside backend/ and frontend/, or packages without installed tools, are
skipped here; the T0 pre-commit hook fails closed on them later.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    raw = (event.get("tool_input") or {}).get("file_path")
    if not raw:
        return 0
    path = Path(raw).resolve()
    try:
        rel = path.relative_to(REPO)
    except ValueError:
        return 0
    problems = ""
    if rel.parts[:1] == ("backend",) and path.suffix == ".py":
        ruff = REPO / "backend/.venv/bin/ruff"
        if ruff.exists():
            run([str(ruff), "format", "--config", "backend/ruff.toml", str(rel)], REPO)
            r = run([str(ruff), "check", "--config", "backend/ruff.toml", str(rel)], REPO)
            if r.returncode:
                problems = r.stdout + r.stderr
    elif rel.parts[:1] == ("frontend",) and path.suffix in {".ts", ".tsx", ".vue", ".js", ".mjs", ".css"}:
        bin_dir = REPO / "frontend/node_modules/.bin"
        sub = str(Path(*rel.parts[1:]))
        if (bin_dir / "prettier").exists():
            run([str(bin_dir / "prettier"), "--write", sub], REPO / "frontend")
        if (bin_dir / "eslint").exists() and path.suffix != ".css":
            r = run([str(bin_dir / "eslint"), "--max-warnings", "0", sub], REPO / "frontend")
            if r.returncode:
                problems = r.stdout + r.stderr
    if problems:
        print(f"fmt_lint_file: lint problems in {rel}:\n{problems[:4000]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
