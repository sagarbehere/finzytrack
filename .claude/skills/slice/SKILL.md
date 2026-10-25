---
name: slice
description: Run one Finzytrack slice through the full loop (orient, brief, tests first, implement, independent verify, docs, land, retro) per design/process/PROCESS.md §3.
---

# Slice

Follow `design/process/PROCESS.md` §3 exactly; this list is only the checklist.

1. **ID:** next free `S<nnnn>-<slug>` (look at `design/journal/`).
2. **Orient:** the `orient` skill.
3. **Brief:** `design/journal/<slice-id>.md` from the template in `design/journal/README.md`. Pick the risk class and lane. Rubric questions → the `escalate` skill; park if blocked.
4. **Tests first:** write them, run them, see them fail on the base commit. Record the base SHA in the journal.
5. **Implement** until the gates are green: `.githooks/pre-commit` on commit; `python3 -m unittest discover -s scripts/tests`, `python3 scripts/check_invariants.py`, `python3 scripts/check_capabilities.py`, plus the package's types and tests.
6. **Hand-off section** in the journal (PROCESS.md §3 step 4, ADR-0057): checks with their must-not-pass tests; on High and Medium slices, `python3 scripts/mutants.py <plan.toml>` with at least one loosening mutant per check (tried / killed; a survivor gets a test or an "equivalent" reason); population source and counts.
7. **Verify:** start the `verifier` agent with only the commit range, the slice ID and the brief. Fix or record each finding (High: fix; Medium: fix or backlog ID; Low: fix or one-line reason). After fixing a High, or three or more Medium, run a fresh verifier round on the fix range.
8. **Docs:** regenerate generated files; update the capability row (`design/capabilities.toml` + `python3 scripts/gen_capabilities.py`); ADRs for decisions; user docs on the docs repo's `v0.4` branch (light lane: at milestone end).
9. **Land:** commit on `next` with the slice ID in the message. Push only if the owner asked in this session.
10. **Retro** in the journal: verifier findings, what a cheaper gate could have caught, where time went, process cost.
