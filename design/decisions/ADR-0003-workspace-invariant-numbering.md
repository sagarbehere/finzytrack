# ADR-0003: Workspace invariants are numbered INV-28–31

This record fixes the invariant IDs after a numbering collision between two review documents.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** process, invariants

## Context

The process proposal (review:20 §2) numbers the starting invariants INV-01–24. INV-21–24 there are the data contract, fail-closed safety checks, public-repo privacy and "agents never touch real data". The workspace design (review:23 §7.2) independently used INV-21–24 for four workspace invariants, plus INV-25–27.

## Decision

(owner, 2026-10-24) The workspace design's INV-21–24 are renumbered; everything else keeps its number.

| New ID | Was (review:23 §7.2) | Invariant |
|---|---|---|
| INV-28 | INV-21 | Workspace-addressed: no server-side active workspace |
| INV-29 | INV-22 | Validate before recording |
| INV-30 | INV-23 | Opening never writes the folder |
| INV-31 | INV-24 | Only the active workspace is touched |

- INV-01–24 are those of review:20 §2. INV-04, INV-11 and INV-17 carry the amendments of review:23 §7.2.
- INV-25 (workspace operations never delete user files), INV-26 (a legacy install keeps working) and INV-27 (the folder is the workspace) keep their numbers.
- `design/INVARIANTS.md` is the only home of the list. IDs are never renumbered again; a retired invariant keeps its ID with a "retired by ADR-nnnn" note.

## Alternatives considered and rejected

- **Renumber review:20's invariants instead.** Rejected: they are cited by more documents, tests and checks.
- **A separate scheme for workspace invariants (for example `INV-W1`).** Rejected: two schemes for one kind of rule make the coverage check and citations harder.

## Consequences

- Any citation of INV-21–24 that comes from review:23 must be read through the table above. The private archive keeps the old text; `design/` uses only the new IDs.

## References

review:23 §7.2 and header; review:25 header and §14 item 1; review:27 §1(d); review:F header (steering 3).
