#!/usr/bin/env python3
"""PreToolUse hook for file tools (INV-24): no reads of the live data folder, no writes to the legacy worktree.

Exit 2 = blocked (the reason goes to the agent on stderr).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guards import path_problem  # noqa: E402

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PATH_KEYS = ("file_path", "path", "notebook_path")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("guard_paths: unreadable hook input; refusing to be safe", file=sys.stderr)
        return 2
    tool = event.get("tool_name", "")
    tin = event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or ".")
    candidates = [tin[k] for k in PATH_KEYS if isinstance(tin.get(k), str)]
    if isinstance(tin.get("pattern"), str) and tool == "Glob":
        candidates.append(tin["pattern"])
    for raw in candidates:
        problem = path_problem(raw, cwd, writing=tool in WRITE_TOOLS)
        if problem:
            print(f"guard_paths: blocked {tool} {raw}: {problem}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
