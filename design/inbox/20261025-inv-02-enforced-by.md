# May the INV-02 "Enforced by" line name the main sidecar's Beancount ban?
- Asked: 2026-10-25 by S0006-backend-skeleton (verifier doc check)
- blocks: nothing
- Rubric item: 4 (changing an invariant's text is the owner's)
- ADRs checked: ADR-0054 (Beancount lives only in the engine process; the main sidecar never imports it) settles the rule; it does not edit INVARIANTS.md, which is owner-owned.

## Context (plain language, a concrete example first)
INV-02 ("Beancount is the oracle; Finzytrack never reimplements it") lists, as its planned enforcement, "an architecture test that only `ledger.beancount_port` imports Beancount". Since ADR-0054 the app has two processes, and the main one must not import Beancount at all. S0006 built that check for the main sidecar (`backend/.importlinter`, `backend/tests/architecture/test_contracts.py`). The INV-02 line still describes the single-process design. The rule itself does not change; only the description of how it is enforced is stale.

## Options
1. Update the "Enforced by" line to: "planned: domain vectors generated from Beancount; in the engine, an architecture test that only `ledger.beancount_port` imports Beancount (BL-0041); in the main sidecar, `backend/.importlinter` and `backend/tests/architecture/test_contracts.py` (no Beancount import at all, ADR-0054)". Status stays pending until the engine side exists.
2. Leave it until the engine slice (BL-0041) and update both halves then.

## Recommendation
Option 1: the invariant file should say where it is enforced today; the rule text is unchanged.

## Answer (owner, verbatim, with date)
