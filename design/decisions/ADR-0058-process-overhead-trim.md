# ADR-0058: Verification scaled to risk, process checks built on evidence, shorter journals

This record trims the process after the owner asked whether it was eating the development (2026-10-25), and sets when that question is measured again.

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** owner: "I agree with your recommendations 1-4 on improving the process and re-evaulating as in 5" (2026-10-25, in session)
- **Area:** process

## Context

Measured after S0007 (journals S0002–S0007):
- `design/` holds about 150,000 words. About 90,000 of them are the one-time migration of the review (capability map, finding index, ADRs). The recurring part is the journals, about 11,000 words over 8 slices: findings were written up three times (verifier report, journal, sometimes `IMPROVEMENTS.md`).
- Product code: none yet (M0). Process code (checks, hooks, meta-tests, the mutation runner) is about 3,600 lines; product-safety tooling (the oracle harness) about 2,600.
- Verifier precision stays at 100%, and the findings that matter were on code that can produce a wrong number or touch data: S0002's classifiers, S0004's missing writers, S0006's licence check failing open and its architecture contradiction.
- Where it did not pay: S0005 spent four verifier rounds (about 50 minutes of fixes on top of 40 minutes of building) defending a dev tool against tracked symlinks in a repo that has none; refusing every symlink would have removed the class in round 1. And most of M0's remaining items were checks on the agents' own paperwork (BL-0034, BL-0036, BL-0038, BL-0040), built before any escape showed a need, which inverts PROCESS.md §9's "every escape becomes a gate".

## Decision

(owner, 2026-10-25, quoted above)
1. **Verification depth follows risk.** After fixing findings:
   - a slice that touches ledger writes, finance semantics, security or user data (any class): a fresh verifier round after a High fix or three or more Medium fixes (unchanged);
   - tooling and process code that touches no ledger or user data: one verifier round; a fresh round only after a High fix;
   - docs-only slices: one verifier round, focused on facts against their sources and doc consistency; no further round.
   The verifier's attack minimums per class are unchanged.
2. **Checks on the process's own paperwork are built when evidence asks for them.** BL-0034 (`tests-fail-on-base`, verifier-SHA, approval-quote and golden-justification checks), BL-0036 (protected-file guard), BL-0038 (PROCESS.md no-drift checks) and BL-0040 (stricter invariant check) leave M0 and become `parked`: each is built when an escape, a measurement or a periodic reflection shows the need. Until then the verifier checks the same things (tests fail on the base; the head SHA; approval quotes; golden justifications). The data guards stay: privacy scan, staged-file guard, INV-24 guards; and BL-0042 (the guard parser) stays in M0, because its false positive blocked real work three times in S0005.
3. **Shorter journals.** One line per finding with its resolution; the verifier's report is summarised, not transcribed; a process change is described once, in `IMPROVEMENTS.md`, and the journal points to it.
4. **Remove a risk class before defending against it** (EP-30): when a design choice can make a class of defect impossible (refuse it, do not support it, pick the simpler structure), prefer that over a check that defends against each case.
5. **Measure again at the end of M1a:** product code against process code (lines), and per slice the building time against the review-and-fix time, reported in the M1a journal entry and fed into the G1 evidence pack (ADR-0046).

## Alternatives considered and rejected

- **Change nothing until G1.** Rejected: the two costs above were already visible, and G1 is two milestones away.
- **Drop the second verifier round everywhere.** Rejected: on data-touching code the second rounds found real defects (S0002, S0004).
- **Delete the parked checks from the backlog.** Rejected: they are cheap to keep as parked items and may be needed once code volume grows.
- **Lower the attack minimums.** Rejected: the cost was in fix rounds on low-risk code, not in the first review.

## Consequences

- PROCESS.md §3 (steps 3, 5, 7), §5, §6 and §8 change; the journal template shortens; ENGINEERING.md gains EP-30; the backlog gains the `parked` status; the roadmap's M0 list shrinks.
- M0 finishes sooner: package skeletons, BL-0042, the invariant enforcement due by M0, M1's specs, the acceptance harness, the empty window.
- Risk accepted: a paperwork slip (a missing quote, a wrong SHA) is caught by the verifier rather than by CI until a parked check is built. An escape of that kind un-parks its check (PROCESS.md §9).

## References

ADR-0001, ADR-0044, ADR-0046, ADR-0057; PROCESS.md §3, §9; journals S0002–S0007; the owner's question in session, 2026-10-25.
