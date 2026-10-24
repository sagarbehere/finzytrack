# ADR-0044: Process lanes and review cadences

This record decides how heavy the per-change process is, and how often the wider reviews run.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** process

## Context

A risky change ran about 5–8 agent passes; lens reviews were planned monthly and at every milestone; a UI tour ran nightly; nothing measured the process's cost. Loosening approved rules needs the owner.

## Decision

(owner, 2026-10-24)
- **Three lanes**, picked by the slice's risk class:
  - **Full lane:** anything touching ledger writes; the full High ceremony.
  - **Specialist lane:** finance and security work. The finance or security red-team review runs **once per area**, not per change, unless the change alters what a number means or adds a new input.
  - **Light lane:** read-only screens and catalogue entries (dashboard widgets, recipe catalog rows, seed content): short brief, verifier on the diff, gates. Their user docs are written at milestone end.
- **Cadences:**
  - cross-cutting (whole-code) lens reviews at **G1, G2, after M8 and at M11 only**; no monthly round;
  - the UI tour **weekly and scripted** (the scripted part in T2, the exploratory part locally);
  - **mutation testing weekly and before each clearance**;
  - a whole-system review at M11 before release.
- **Owner-only ceilings:** the 30-second T0 budget and a ceiling on process cost per change (agent passes and wall time per slice) can be raised only by the owner. All other thresholds stay agent-approved with a one-line reason.
- Review findings are capped per run and triaged; integrity, security and wrong-number findings become slices at once.
- **Spot audits** by a fresh context with a different prompt and role, plus the seeded-defect catch rate, measure the verifiers (ADR-0045).

## Alternatives considered and rejected

- **Full ceremony for every change.** Rejected: too slow for read-only screens.
- **Monthly lens rounds and a nightly UI tour.** Rejected: cost without matching benefit.

## Consequences

- The reflection cycle reports process overhead per slice and the false-positive rate per check, and proposes consolidations when overhead rises.

## References

review:F §2 item 6 (a)–(d); review:20 §3, §4a–§4c, §6a, §10a; ADR-0001, ADR-0045.
