# ADR-0051: Working principles carried over from the review

This record states four owner principles that guide the rebuild but had no ADR of their own (review:G F-10).

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** process, engineering

## Context

The 2026-10 review recorded several owner principles in its ground rules and decision register (review:99 §5 rows 2, 3, 5 and 9; review:00 §1, §1b; review:13 §4; review:21 §5 item 8). They shape many later decisions, so they need a citable home.

## Decision

1. **Effort is not a constraint; cleanliness is.** Prefer the clean, composable, fast design over the cheaper patch, including a rewrite where incremental edits would be error-prone (review:99 §5 row 2).
2. **Earlier decisions stand unless new evidence appears.** The decisions of the 2026-05 review and of this review are not reopened without evidence; a change is a superseding ADR (row 3).
3. **Reuse libraries over hand-written plumbing; functionality beats style-system fit.** Choose a maintained library unless it fights required behaviour; do not reject one only because it does not match the design system's look (row 5; review:13 §4). EP-level detail lives in `ENGINEERING.md`.
4. **Owner time has no weekly budget.** The owner says when the process asks too much; the pilot and reflection measure owner time and propose reductions, and escalations are never silently dropped (row 9; `PROCESS.md` §5).

## Alternatives considered and rejected

- **Leaving these only in the private review archive.** Rejected: agents on `next` could not cite them.
- **Folding them into `ENGINEERING.md` as EPs.** Rejected for 2 and 4, which are about process, not code; 1 and 3 are reflected in the EPs as well.

## Consequences

- Verifiers may cite ADR-0051 when a slice takes a shortcut that leaves a messier design, or hand-writes what a pinned library already does.

## References

review:99 §5 rows 2, 3, 5, 9; review:00 §1, §1b; review:13 §4; review:21 §5 item 8; review:G F-10.
