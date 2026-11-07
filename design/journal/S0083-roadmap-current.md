# S0083: roadmap §1 brought up to date with S0075 to S0082

## Brief
- Base: 5ac7890b.
- Goal: `design/specs/roadmap.md` §1 ("Where we are") still said "updated 2026-11-01" and listed copy (BL-0196) as next, twice. Bring it current with the slices landed since: S0075 to S0082 (S0075 was never added either), so it is right before new work. Owner, 2026-11-07: "Bring design/specs/roadmap.md §1 ("Where we are", still dated 2026-11-01) up to date with S0076–S0082 as a small docs slice, so it is current before new work."
- Implements: roadmap Orientation "Keeping it current" (a slice that finishes an item in §1 updates §1).
- Capabilities: none.
- Invariants touched: none.
- **The bytes it protects:** none (docs only).
- Risk class / lane: Low / light lane. One verifier round, on facts and doc consistency (PROCESS.md §3 step 5).
- Acceptance tests: none new (docs only); the gates (`check_invariants.py`, `check_retired_terms.py` through the meta-tests, privacy scan) stay green.
- Expected doc impact: `design/specs/roadmap.md` §1 only: the date; the "next, in order" list (create, copy and the cleanups built; next BL-0201 then BL-0113; the duplicate "copy (BL-0196)" removed); a new paragraph "Since the step back (2026-11-01 to 2026-11-07)" naming each slice with its backlog rows and the owner's words; the "Next:" line naming what remains in M1a.
- Open questions: none.

## Decisions
- **A new paragraph rather than more clauses in the long first paragraph** (agent): §1's first paragraph is already very long; S0075 to S0082 are listed after the "Step back" paragraph they follow in time. The first paragraph only points to it.
- **The "Next:" line lists the rest of M1a without ordering it after BL-0113** (agent): the owner asked to be shown the remaining items and a proposed order after BL-0113; that order is set then.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none added or changed.
- Self-mutation: not applicable.
- Population: the slices S0075 to S0082, from `design/journal/` and `git log` between the S0074 journal and 5ac7890b (8 slices in, 8 named in §1).

## Verifier
- Round 1, head 8e508f7a: passed with findings, 0 High, 1 Medium, 3 Low. Attacks: 10 (every slice, backlog ID, quote and date checked against journals, backlog and git; spec Orientation cross-check; nothing landed in the range missing; gates).
- F1 Medium: the "Next:" line read as the whole rest of M1a while about 22 M1a rows are open. Fixed: it names BL-0164 and BL-0110 and points to `FT_MILESTONE_EXIT=M1a` for the full list.
- F2 Low: the edited sentence still said every L world passes except `L_save_deep` (pre-S0072), with a stray ")" and ",,". Fixed: BL-0110, none run on the S0072 model yet; punctuation fixed.
- F3 Low: the final-round rule was first applied in S0080, not S0081, and lived only in journals. Fixed: the roadmap says S0080; the rule is now in PROCESS.md §3 step 7 with the owner's words (IMP-0052).
- F4 Low: the 2026-11-01 answer also kept the lineage match. Fixed: BL-0191 named.
- Docs-only slice: one round (PROCESS.md §3 step 5); the fixes are wording, not re-verified.

## Retro
- Build 15 min, fix 10 min, 1 verifier round, findings 0H/1M/3L. Cheaper gate: run the M1a exit listing (`FT_MILESTONE_EXIT=M1a`) before writing any "what remains" sentence, and reread the whole sentence an edit lands in.
