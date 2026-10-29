#!/usr/bin/env python3
"""SessionStart hook: point every new agent session at the project's single sources of truth.

It also warns when this clone's git hooks are not installed (S0031, the M0 gate red team L2): without
`core.hooksPath = .githooks` no commit or push is checked. It never fails the session.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def hooks_path_problem(repo: Path) -> str | None:
    """None when the clone runs .githooks; otherwise what to tell the agent."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not read core.hooksPath ({exc}); the git hooks may be off"
    if out == ".githooks":
        return None
    return (f"core.hooksPath is {out!r}, not '.githooks': no commit or push in this clone is checked. Ask the owner "
            "to run `git config core.hooksPath .githooks` (agents may not change it), and do not commit until then")


def main() -> int:
    print(
        "Finzytrack v0.4 (branch next). Before any work: read design/README.md, then design/process/PROCESS.md. "
        "Use the `orient` skill for a feature or change request and the `slice` skill to do the work. "
        "Never touch the owner's live data folder (INV-24). Never stop, start or call the owner's running app: if it is in "
        "your way, ask the owner to stop it. Escalations go to design/inbox/."
    )
    try:
        problem = hooks_path_problem(REPO)
    except Exception as exc:  # noqa: BLE001 - a session must start whatever happens here
        problem = f"could not check core.hooksPath ({exc})"
    if problem:
        print(f"WARNING: {problem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
