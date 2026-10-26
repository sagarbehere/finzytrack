#!/usr/bin/env python3
"""PreToolUse hook: an implementer never starts a verifier or any other agent (IMP-0029).

Owner, 2026-10-26: "make sure that future implementers don't start a verifier themselves. That's a problem if it
ever happens." The orchestrator starts the verifier with only the range, the slice ID and the brief (PROCESS.md §3
step 5). `.claude/agents/implementer.md` denies the implementer the Agent, Task and SendMessage tools; a definition
edit takes effect only once Claude Code reloads it, so this hook enforces the same per call from the hook input's
`agent_type`, and also refuses an implementer's Bash command that runs the `claude` CLI (a nested `claude -p --agent
verifier ...` starts an agent without the Agent tool; IMP-0029 verifier F1). Stated limit: the Bash check matches the
word `claude` as a command or quoted argument; a deliberately obfuscated invocation (built from parts, an alias) is
review's, like the other shell guards (S0028). Exit 2 = blocked (the reason goes to the agent on stderr); any input
it cannot read is refused.
"""

from __future__ import annotations

import json
import re
import sys

SPAWN_TOOLS = {"Agent", "Task", "SendMessage"}
NO_SPAWN_AGENTS = {"implementer"}
# `claude` as a word: at the start, after whitespace, a shell operator, a quote or a path separator, and followed by
# the end, whitespace, a shell operator or a quote; `.claude/` paths and `claude-code` package names do not end there
# except `claude-code` itself, which is matched separately.
CLAUDE_CLI = re.compile(r"""(?:^|[\s;&|(){}`'"=/])claude(?:-code)?(?=$|[\s;&|()`'"])""")


def refuse(reason: str) -> int:
    print(f"guard_agent: blocked: {reason}", file=sys.stderr)
    return 2


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return refuse("unreadable hook input; refusing to be safe")
    if not isinstance(event, dict):
        return refuse("unreadable hook input; refusing to be safe")
    tool, agent = event.get("tool_name"), event.get("agent_type")
    if not isinstance(tool, str) or not (agent is None or isinstance(agent, str)):
        return refuse("unreadable hook input; refusing to be safe")
    if agent not in NO_SPAWN_AGENTS:
        return 0
    why = ("an implementer never starts a verifier or any other agent (owner, 2026-10-26). Commit with green gates, "
           "stop and report the range; the orchestrator starts the verifier.")
    if tool in SPAWN_TOOLS:
        return refuse(why)
    if tool == "Bash":
        command = (event.get("tool_input") or {}).get("command") if isinstance(event.get("tool_input"), dict) else None
        if not isinstance(command, str):
            return refuse("unreadable Bash command; refusing to be safe")
        if CLAUDE_CLI.search(command):
            return refuse("this command runs the claude CLI; " + why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
