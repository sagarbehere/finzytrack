#!/usr/bin/env python3
"""PreToolUse hook for Bash (INV-24): refuse commands that endanger the owner's data. Exit 2 = blocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guards import bash_problem  # noqa: E402


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("guard_bash: unreadable hook input; refusing to be safe", file=sys.stderr)
        return 2
    command = (event.get("tool_input") or {}).get("command", "")
    cwd = Path(event.get("cwd") or ".")
    problem = bash_problem(command, cwd)
    if problem:
        print(f"guard_bash: blocked: {problem}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
