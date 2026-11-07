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

## Retro
