#!/usr/bin/env python3
"""SessionStart hook: point every new agent session at the project's single sources of truth."""

import sys

print(
    "Finzytrack v0.4 (branch next). Before any work: read design/README.md, then design/process/PROCESS.md. "
    "Use the `orient` skill for a feature or change request and the `slice` skill to do the work. "
    "Never touch the owner's live data folder or running app (INV-24). Escalations go to design/inbox/."
)
sys.exit(0)
