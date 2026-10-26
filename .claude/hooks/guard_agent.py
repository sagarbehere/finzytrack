#!/usr/bin/env python3
"""PreToolUse hook for the Agent tool: an implementer never starts a verifier or any other agent.

Owner, 2026-10-26: "make sure that future implementers don't start a verifier themselves. That's a problem if it
ever happens." The orchestrator starts the verifier with only the range, the slice ID and the brief (PROCESS.md §3
step 5). The implementer's `disallowedTools` did not take effect in a live session, so this hook enforces it per
call from the hook input's `agent_type`. Exit 2 = blocked (the reason goes to the agent on stderr).
"""

from __future__ import annotations

import json
import sys

SPAWN_TOOLS = {"Agent", "Task"}
NO_SPAWN_AGENTS = {"implementer"}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("guard_agent: unreadable hook input; refusing to be safe", file=sys.stderr)
        return 2
    if not isinstance(event, dict):
        print("guard_agent: unreadable hook input; refusing to be safe", file=sys.stderr)
        return 2
    if event.get("tool_name") in SPAWN_TOOLS and event.get("agent_type") in NO_SPAWN_AGENTS:
        print("guard_agent: blocked: an implementer never starts a verifier or any other agent (owner, 2026-10-26). "
              "Commit with green gates, stop and report the range; the orchestrator starts the verifier.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
