# ADR-0001: Adopt the development process

This record adopts the development process for the v0.4.0 rebuild and names its single living reference.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** process

## Context

The 2026-10 architecture review found a patchwork: the same concept implemented many times, docs that drifted from the code, and integrity bugs that no gate caught. The rebuild (ADR-0002) is also a prototype for building correctness-critical software with AI coding agents. The review wrote a founding process proposal (review:20). The owner accepted its direction on 2026-10-24 and amended it through the final-review decisions (review:F §2 items 5–7).

## Decision

Adopt the process described in **`design/process/PROCESS.md`**. That file is the single, current reference for how Finzytrack is developed, and the owner owns it. The founding proposal (review:20) is frozen in the private review archive and is not edited again.

The process has these parts (details live in `PROCESS.md`, not here):
- **`design/` is the project memory**, tracked in the repo: invariants, ADRs, specs, the capability map, the slice journal, the backlog, the glossary and generated references. Agents and the owner answer "did we do X?" and "why Y?" from it, never from chat history.
- **Owner-owned invariants** (`design/INVARIANTS.md`). Each has an ID and an "Enforced by" list. An invariant without enforcement fails CI unless it is marked *pending* with an open backlog ID and a milestone. Adding or tightening needs the owner (batched); weakening always needs the owner; every change gets an ADR.
- **Engineering principles** (`design/ENGINEERING.md`): "should" rules, deviations recorded with a reason.
- **The slice loop:** brief, tests first (`tests-fail-on-base`), implement under the gates, independent verifier with fresh context, docs in the same change, land. Ceremony scales with the risk class and lane (ADR-0044).
- **Escalation rubric** with one queue (`design/inbox/`). A slice that implements an accepted ADR as written does not escalate.
- **Spec-change protocol:** stop the slice, capture evidence, draft a superseding ADR, decide, update dependents.
- **Tiered gates** T0 (pre-commit ≤ 30 s, pre-push), T1 (per push), T2 (nightly, deterministic), T-local (owner's machine, real-ledger copies).
- **Thresholds detect accidents and regressions.** Agents may raise a threshold or baseline with a one-line reason, except the 30-second T0 budget and the per-change process-cost ceiling, which only the owner raises.
- **No-drift docs:** generate it, test it, or keep it to the "why". ADRs are append-only.
- **Golden-change rule, privacy gate (INV-23), reflection and `IMPROVEMENTS.md`.**

## Alternatives considered and rejected

- **Keep the gitignored `dev-docs/` folder.** Rejected: it was not versioned with the code, nobody could check it, and it drifted.
- **Exhaustive upfront requirements or traceability matrices.** Rejected: implementation detail is the agent's job, checked by tests and the verifier; lightweight IDs in tests and docs are enough.
- **One big review at the end.** Rejected: defects must be caught in the change that introduces them.

## Consequences

- Setup cost is front-loaded in M0 (gates, corpora, acceptance harness) before the first feature.
- Every later process change is an `IMPROVEMENTS.md` entry plus the matching `PROCESS.md` edit; changes to an invariant or the escalation rubric are ADRs.
- `CLAUDE.md` files become short pointers into `design/`.

## References

review:20 §0–§12; review:F §2 items 5, 6, 7; ADR-0044, ADR-0045, ADR-0046; INV-01–INV-31.
