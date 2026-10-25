# design/: start here

Everything that explains Finzytrack v0.4 (why it is built this way, what exists, what is planned) lives in this folder, versioned with the code. It replaces the old untracked `dev-docs/`.

## Reading order

1. [`process/PROCESS.md`](process/PROCESS.md): how work is done here (the slice loop, gates, what reaches the owner).
2. [`INVARIANTS.md`](INVARIANTS.md): the owner's rules. Breaking one is never a judgement call.
3. [`ARCHITECTURE.md`](ARCHITECTURE.md): the target system, package map, API shape, data flows, version pins.
4. [`ENGINEERING.md`](ENGINEERING.md): engineering principles EP-nn and the verifier checklist.
5. [`capabilities.md`](capabilities.md): "did we do X?" for every capability (generated from `capabilities.toml`).
6. [`decisions/`](decisions/README.md): ADRs, "why did we do Y?", including the alternatives that were rejected.
7. [`specs/roadmap.md`](specs/roadmap.md): the milestone order, what each milestone delivers, and where we are now.
8. [`backlog.md`](backlog.md): the work list and the review's finding index.
9. [`glossary.md`](glossary.md): the vocabulary.
10. `specs/` (per area, each with an Orientation block) and `journal/` (one record per slice) as needed.

For a concrete change, use the `orient` skill first: it pulls the relevant rows, ADRs, specs and backlog items for you.

## Conventions

- **IDs are stable:** `INV-nn`, `EP-nn`, `ADR-nnnn`, `CAP-<AREA>-<NAME>`, backlog IDs (`L4-01`, `I5`, `BL-nnnn`), slice IDs `S<nnnn>-<slug>`. Never renumber.
- **`review:<doc> §<section>`** cites the 2026-10 architecture review, kept in the owner's private archive. It is evidence and history; the decisions themselves are recorded here as ADRs.
- **Generated files** carry a `GENERATED` header and are never edited by hand (INV-19).
- **Public repo:** nothing here may contain real financial data, secrets or personal details (INV-23). The T0 privacy scan enforces it.
