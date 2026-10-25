# ADR-0057: The pilot's result: hand-off checks, re-verification, and a first slice count

This record closes the process pilot that ADR-0046 set up: what three slices measured, how the rubric and the gates change because of it, and a rough slice count for M1–M12.

- **Status:** Accepted for the tightenings (agents may apply them first, PROCESS.md §9); the owner's approval of the PROCESS.md edits is asked in `design/inbox/20261025-pilot-process-changes.md`
- **Date:** 2026-10-25
- **Deciders:** agent (the tightenings, the estimate); owner (approval of the PROCESS.md edits)
- **Area:** process

## Context

The pilot ran the intent table (S0004) under the full process, together with two tooling slices (S0002, the oracle harness; S0003, the legacy API tests). The measurements are in `design/journal/S0004-intent-table.md`, "Pilot measurements". In short:

- **Escalations:** 4, batched, none blocking. All four needed the owner. One was partly answered by an existing ADR; the verifier caught that and the question was narrowed before it reached the owner.
- **Defects by finder:** verifiers 39 findings, 5 High and 34 Medium/Low (S0002 16, S0003 5, S0004 18, both rounds counted); gates 1; found later 0. S0004's journal said 15; it was written before S0004's second round and undercounted. Verifier precision 100% (every finding was real), against a target of at least 70%.
- **Second rounds paid:** after the first round's fixes, round 2 found 6 more findings in S0002 and 8 in S0004 (all Medium or Low).
- **Ceremony share:** about 0.2 (S0002) and 0.7 (S0004), within the 1.0 target. The cost was rework the verifier forced, not ceremony.
- **Owner time:** none inside the slices.
- **What the Highs had in common:** all five (S0002 F1–F4, plus F5's missing comparison) were checks that accepted too much: an eraser, a guard, a classifier. Every unit test written so far asked "does the check fire", never "does it fire on what it must not". The verifiers found most of them by writing mutants.
- **Two population errors:** S0003 chose API-level tests by fixture name and undercounted by about 100 (F2); S0004 built "every ledger writer" from the legacy mutators and ADRs and missed 13 writers that the capability map lists (F1). Both lists were built by name instead of by behaviour.

The sample is small (three slices, all tooling or spec, no package code yet), so these adjustments are recalibrated at the first periodic reflection and after M1b, the calibrated second run.

## Decision

(agent, 2026-10-25) Five tightenings and no loosening. The rubric's items are unchanged.

1. **Must-not-pass list before hand-off.** The implementer lists every check the slice adds or changes that accepts, tags or refuses (classifiers, guards, validators, table checks), each with the test of a case the spec says must not pass. This makes EP-29's third bullet a hand-off step instead of only a verifier question. The list goes in the journal's Hand-off section.
2. **Self-mutation before hand-off** (High and Medium slices). For each check on that list, the implementer seeds at least one mutant that loosens it (drop a condition, return "accept", widen a bound) and runs the tests with `scripts/mutants.py`. The journal records "tried / killed". A survivor gets a test before hand-off, or a one-line reason when the mutant is equivalent (it cannot change behaviour). The runner works on a fresh copy of the working tree for every run and never writes into the checkout (INV-24). This is the cheap, hand-picked version of the T2 mutation testing (mutmut, Stryker), which arrives with the packages; it does not replace it.
3. **Population sweep.** A spec or check that claims to cover a whole population (every mutator, every API test, every error class) names the inventory it was built from, builds it by behaviour (the capability map row by row; the code that actually does the thing, such as "builds a `TestClient`"), never by name, and records the counts in and out with reasons. A mechanical version for ledger writers is BL-0049.
4. **Re-verification.** After fixing a High finding, or three or more Medium findings, a fresh verifier round runs on the fix range before the slice lands. This writes down what S0002 and S0004 did.
5. **"ADRs checked" in every escalation.** The inbox template gains a line naming the ADRs searched and why none settles the question, so a question an ADR answers is caught before it reaches the owner.

**Kept as they are, with the evidence:** the rubric's items (4 of 4 escalations needed the owner); the verifier's scope and attack minimums (100% precision, and every High came from the verifier); the ceremony target of 1.0 (met with room); the gates' budgets (T0 about 1 s against 30 s).

**Rough slice count for M1–M12** (ADR-0046; review:21 §4). Built from the capability rows per milestone (`design/capabilities.toml`), the milestone contents of review:27, and about two rows per product slice (light-lane catalogue entries in M7 go faster, about three per slice):

| Milestone | Planned capability rows | Slices (rough) |
|---|---|---|
| M0, remaining (gates, package skeletons, M1's specs) | — | 12–16 |
| M1a read core + workspace-format core | 2 | 12–18 |
| M1b write contract | — | 15–25 |
| M2 shell, boot, read-only | 33 | 16–24 |
| M3 workspace lifecycle, legacy adoption | 27 | 12–18 |
| M4 accounts | 26 | 13–19 |
| M5 transactions and documents | 20 | 10–14 |
| M6 import and reconciliation | 35 | 18–26 |
| M7 dashboards, Analyze, budgets | 95 | 32–48 |
| M8 investments and prices | 43 | 18–26 |
| M9 assistant | 15 | 8–12 |
| M10 settings and completion | 12 | 6–10 |
| M11–M12 release hardening and release | 5 | 10–16 |
| **Total** | 313 (of 340 rows; 15 cut, 12 deferred) | **about 180–270** |

Measured pace so far: about 2–3 hours of agent wall time per slice, verifier rounds included, on tooling and spec slices. Product slices with code in several packages and UI will be slower, so 3–5 hours is the planning figure: very roughly 550–1,350 agent hours, before owner sessions and dogfood windows. This is an order of magnitude, not a plan. The G1 evidence pack replaces it with the pace measured through M1b, and the owner judges it then (ADR-0046).

## Alternatives considered and rejected

- **Loosen the ceremony for Medium spec slices** (for example one verifier round only). Rejected: second rounds found real issues in both slices that had them, and precision was 100%.
- **A T1 mutation job now.** Deferred: the packages do not exist yet, and the planned tools (mutmut, Stryker) target them. The check scripts get hand-picked mutants (adjustment 2) until then.
- **Keep the seeded mutants in the repo and require them to stay killed in T1.** Rejected: each mutant is tied to exact source text, so ordinary edits would make them stale and fail CI for no defect. Real mutation testing does this job properly.
- **Change the rubric's items.** Rejected: no escalation was wrong or missing; the one partly answered by an ADR is handled by adjustment 5.
- **Make the population sweep fully automatic now.** Deferred to BL-0049: it needs a new field on all 340 capability rows.

## Consequences

- `PROCESS.md` §3 (steps 4 and 5), the implementer agent, the slice skill, the journal template and the inbox template carry the adjustments; `IMPROVEMENTS.md` IMP-0010 to IMP-0013 log them.
- `scripts/mutants.py` with its meta-tests (`scripts/tests/test_mutants.py`).
- More agent time per slice before hand-off (minutes per check), traded for fewer verifier rounds. The first reflection measures whether rounds and High findings per slice fall.

## References

ADR-0001, ADR-0046; review:20 §9, §10, §10a; review:21 §4; review:27 §0.1; EP-29; INV-24; journals S0002, S0003, S0004, S0005.
