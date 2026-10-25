# S0023: Review findings fail their milestone's exit; M0/M1 findings triaged

## Brief
- Goal: make "milestone done" include the review's findings due by it. A milestone-exit run of `check_invariants.py` (`FT_MILESTONE_EXIT=Mx`) also fails while any backlog item (review finding or `BL-`) due by Mx is `open` or `in-progress`; every run checks that each backlog row has a known status and that each open item names a milestone. Then triage the open M0 and bare-`M1` items: close what is done with its evidence, split `M1` into M1a/M1b (ADR-0046), park BL-0042 as the owner instructed.
- Implements: owner request (quoted under Decisions); backlog.md "How this file works" (items are closed by slices); ADR-0046 (M1 split); PROCESS.md §3.2 exit gates via the existing milestone-exit mechanism.
- Capabilities: none. Invariants touched: none (the pending-invariant rule is unchanged).
- Risk class / lane: Low-Medium tooling (a check on the work list; no ledger or user data): one verifier round, a fresh one only after a High (ADR-0058).
- Acceptance tests: `scripts/tests/test_check_invariants.py` class `BacklogAtMilestoneExit` (open and in-progress fail their exit and later exits; closed statuses pass; a later milestone does not fail an earlier exit; bare `M1` is due by `M1a`; unknown status and missing milestone refused; tables not headed `ID` and rows outside a table ignored; the real backlog parses; an unknown exit name refused).
- Expected doc impact: `scripts/check_invariants.py` docstring; backlog statuses and milestones; roadmap §1 (M0 list gains L9.M-7 and L10-18); PROCESS.md §3.2 is unchanged in wording (roadmap §3.2 already says "every slice met the definition of done"); this journal.
- Open questions: none.
- Base SHA: 55ae244.

## Decisions
- Owner: "Do both" (2026-10-25, in session), answering the proposal to triage the M0/M1 findings and to add "a milestone-exit run also fails while any backlog finding due at Mx or earlier is still open" to the existing check.
- A bare `M1` counts as due by `M1a` (the conservative reading); this slice splits every open `M1` row so none is left bare (agent).
- M1 split by content (ADR-0046: M1a read core, M1b write contract): M1a: I4, L1-02, L1-05, L1-09, L1-10, L5-07, L11-02, L11-03, L11-06, L11.M-1; M1b: I2, I3, I9, I10, L1-03, L11.M-6 (the post-write reconciliation load) (agent).
- Closed with evidence: L1-06 (S0006: `backend/.importlinter` layers contract, run by pre-push and `ci.yml`); L9-18 (S0009: `eslint --max-warnings 0` without fixing, `vue-tsc` and `vitest` in pre-commit, pre-push and `ci.yml`) (agent).
- Still open at M0: L9.M-7 (no component tests exist yet; a lint ban on name-keyed `stubs` closes it) and L10-18 (goes with the demo generator slice) (agent).
- BL-0042 → `parked`: owner, session 5 brief: "BL-0042 stays parked unless I ask." (2026-10-25).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Ways to loosen or bypass, each tested: ignore `in-progress`; `<` instead of `<=`; bare `M1` read as `M1b`; accept any status; let an open row skip its milestone; read any table with a `Milestone` column; let a table run on past a non-table line; accept a misspelt exit name (`m0`) and silently check nothing.
- Self-mutation: 8 tried, 8 killed.
- Population: every row of the 14 backlog tables headed `ID` with `Milestone` and `Status` columns (299 rows on 2026-10-25).
