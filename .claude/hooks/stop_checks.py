#!/usr/bin/env python3
"""Stop hook: before an agent session ends, run the fast repo checks so it never stops on a red tree.

Runs the check-script meta-tests, the invariant and capability checks, and the privacy scan over files
changed in the working tree. Exit 2 sends the failures back to the agent once; when the hook has already
asked for a fix (stop_hook_active), it lets the session stop and only prints the problems.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKS = [
    ["python3", "-m", "unittest", "discover", "-s", "scripts/tests", "-q"],
    ["python3", "scripts/check_invariants.py"],
    ["python3", "scripts/check_capabilities.py"],
]


def changed_files() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"], cwd=REPO,
                         capture_output=True, text=True).stdout
    files = []
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[0] != "D" and entry[1] != "D":
            files.append(entry[3:])
    return [f for f in files if (REPO / f).is_file()]


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        event = {}
    failures = []
    for cmd in CHECKS:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=110)
        if r.returncode:
            failures.append(f"$ {' '.join(cmd)}\n{(r.stdout + r.stderr)[-2000:]}")
    files = changed_files()
    if files:
        r = subprocess.run(["python3", "scripts/privacy_scan.py", "--quiet", *files], cwd=REPO, capture_output=True, text=True)
        if r.returncode:
            failures.append(f"privacy scan of changed files:\n{r.stderr[-2000:]}")
    if not failures:
        return 0
    print("stop_checks: the tree is red:\n\n" + "\n\n".join(failures), file=sys.stderr)
    return 0 if event.get("stop_hook_active") else 2


if __name__ == "__main__":
    sys.exit(main())
