#!/usr/bin/env python3
"""PreCompact hook: remind the agent that project state belongs in design/, not in the chat history."""

import sys

print(
    "Before context is compacted: make sure the current slice's journal (design/journal/<slice-id>.md) "
    "records the brief, decisions so far, open questions and next step. Agent memory is not project memory."
)
sys.exit(0)
