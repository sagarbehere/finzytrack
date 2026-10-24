---
name: orient
description: Get up to speed on a Finzytrack feature, area or change request before planning or coding. Returns the relevant capability rows, ADRs (incl. rejected alternatives), invariants, specs, backlog items and code entry points. First step of every slice brief.
---

# Orient

1. Pick 2–6 search terms from the request (capability words, IDs such as `CAP-IMP-OFX`, `L4-01`, `INV-14`, module names).
2. Run: `python3 scripts/orient.py <term> [<term> ...]`. It searches `design/capabilities.toml`, `design/decisions/`, `design/INVARIANTS.md`, `design/specs/`, `design/backlog.md`, `design/ARCHITECTURE.md` and the code, and prints a compact briefing with file references.
3. Read the ADRs it lists **including their rejected alternatives**, so old debates are not reopened unknowingly. Read the Orientation block of each listed spec.
4. Summarize for the brief: what exists, what is decided (ADR IDs), which invariants bind it, open backlog items, gaps (no ADR, no spec, no capability row), and any question that hits `design/process/escalation-rubric.md`.

If a capability has no row, or a decision has no ADR, say so explicitly; that gap is part of the slice.
