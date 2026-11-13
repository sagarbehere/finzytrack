---
name: slice
description: Run one Finzytrack slice through the full loop (orient, brief, tests first, implement, independent verify, docs, land, retro) per design/process/PROCESS.md §3.
---

# Slice

Follow `design/process/PROCESS.md` §3 exactly; this list is only the checklist.

1. **ID:** next free `S<nnnn>-<slug>` (look at `design/journal/`). **Batching** (PROCESS.md §3, IMP-0072): batch backlog rows of one lane and one kind into one slice; never put a loosening, or a mechanism that needed two fix rounds before, into a batch.
2. **Orient:** the `orient` skill.
3. **Brief:** `design/journal/<slice-id>.md` from the template in `design/journal/README.md`. Pick the risk class, lane and review-budget row (PROCESS.md §3). A brief that adds or hardens a mechanism answers the two questions of §3 step 2 (real setup and policy; fix-round history), IMP-0065. Rubric questions → the `escalate` skill; park if blocked.
4. **Tests first:** write them, run them, see them fail on the base commit. Record the base SHA in the journal.
5. **Implement** until the gates are green: `.githooks/pre-commit` on commit; `python3 scripts/run_meta_tests.py` (the meta-tests, in parallel), `python3 scripts/check_invariants.py`, `python3 scripts/check_capabilities.py`, plus the types and tests of every package the slice touches, and the frontend's gates (`pnpm typecheck`, `pnpm test` in `frontend/`) whenever `contracts/` changes, since the frontend's types are generated from it (IMP-0053).
6. **Hand-off section** in the journal (PROCESS.md §3 step 4, ADR-0057): checks with their must-not-pass tests; for checks that guard ledger data, finance, real-data privacy or product security (optional on tooling), `python3 scripts/mutants.py <plan.toml>` with at least one loosening mutant per check (a survivor gets a test or an "equivalent" reason); population source and counts.
7. **Verify:** the orchestrator (never the implementer, which cannot start agents) starts the `verifier` agent with only the commit range, the slice ID and the brief. Fix or record each finding (High: always fix; Medium: fix or backlog ID; Low: fix or one-line reason; on tooling, gates, measurement or docs, a missed shape outside the stop rule's points (1)-(3) becomes a stated-limits line, IMP-0066); each finding carries its class (a)-(e). Red teams, fresh rounds and what a later round fixes follow the review budget (PROCESS.md §3). An options note for the owner lists the cheapest option first and the recommended mechanism's review history (PROCESS.md §5, IMP-0071).
8. **Docs:** regenerate generated files; update the capability row (`design/capabilities.toml` + `python3 scripts/gen_capabilities.py`); ADRs for decisions; user docs on the docs repo's `v0.4` branch (light lane: at milestone end). A slice that retires names (adds terms to `scripts/check_retired_terms.py`) runs `python3 scripts/check_retired_terms.py --source` and fixes or records each hit in its journal.
9. **Land:** commit on `next` with the slice ID in the message. Then push `next` (`git push origin next`); the pre-push hook runs the checks.
10. **Retro** in the journal, one line: build min, fix min, rounds, findings H/M/L, one sentence on the cheaper gate, and `<check> <n> catch / <n> false positive` for each process check that fired (PROCESS.md §9).
