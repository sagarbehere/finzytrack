# S0008: Trim the process (ADR-0058)

## Brief
- Goal: apply the owner-approved process trim: verification depth follows risk; the paperwork checks (BL-0034, 0036, 0038, 0040) are parked; shorter journals; EP-30; a re-measurement at the end of M1a. Owner: "I agree with your recommendations 1-4 on improving the process and re-evaulating as in 5" (2026-10-25, in session).
- Implements: ADR-0058 (new); PROCESS.md §9 (loosening needs the owner: approved above).
- Capabilities: none. Invariants touched: none (the data guards stay).
- Risk class / lane: Low / light (docs and process text). Under ADR-0058 itself: one verifier round on facts and consistency.
- Expected doc impact: ADR-0058 and its index line; PROCESS.md; ENGINEERING.md EP-30; journal template; backlog statuses; IMPROVEMENTS IMP-0015; roadmap §1 and M1a exit.
- Open questions: none.
- Base SHA: 1257e62.

## Verifier
- Round 1, head 052cf68: PASS WITH FINDINGS. Attacks: 4 (measurements, edits against the five items, stale claims of parked checks, check scripts).
- F1 Medium: the slice skill and the implementer agent still had the old re-verification rule. Fixed.
- F2 Low: the measurement covered journals S0000–S0007, not S0002–S0007. Fixed in ADR-0058 (before landing).
- F3 Low: ADR-0057 and ADR-0045 are amended by ADR-0058 without pointers. Fixed: an "Amends" line in ADR-0058, status pointers in both.
- F4 Low: "the journal points to the IMP entry" was not in the template. Fixed.
- Docs-only slice: one round (ADR-0058).

## Retro
- The two agent prompts restate the rule instead of linking to PROCESS.md, so a rule change has to find every copy; they now say less and point to §3 step 5. Process cost: about 30 min writing, 2.5 min verifier, 10 min fixes.
