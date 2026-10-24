# ADR-0028: Statement reconciliation is a first-class use of the matcher

This record decides the "why doesn't my balance match?" capability and how a reconciled state is recorded.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** import, reconciliation

## Context

The owner logs transactions during the month (for example from nightly email alerts). At month end the balance sometimes differs from the bank's, and finding the missing transactions by hand is painful. v0.3 reconciled nothing against statement balances.

## Decision

(owner, 2026-10-24)
- **Reconcile mode of the import pipeline.** Input: statement lines for one anchor account plus the closing balance and date (PDF, CSV/XLS, OFX, a pasted list or a screenshot; the user types the closing balance when the source has none). When the source carries opening and closing balances, opening + Σ lines = closing also validates the parse.
- **Scope:** lines after the last `balance` assertion on that account (the "reconciled up to" boundary).
- **Matching** against existing postings on the anchor account, whatever created them, on amount, a date window and extracted references, with one-to-one assignment.
- **The report always accounts for the full difference:** matched (optionally stamping `import-key`); on the statement but missing in the ledger (proposed drafts); in the ledger but not on the statement; matched but the amount differs (proposed fix); and the arithmetic, with any unexplained **residual shown, never hidden**.
- **Proceed** applies the chosen additions and fixes through the write contract, then inserts a `balance` assertion dated **closing + 1** (Beancount semantics).
- **Assertion traps handled:** an OFX `DTASOF` is often the download time, so the last fully closed day is used; pending `!` entries count in a balance check, so no assertion is proposed while unsettled pending entries fall on or before the as-of date.
- **Auto-accept** of a matched assertion is on by default only when the internal check passed, the as-of day is fully closed, and no unsettled pending entry falls on or before it; otherwise off, with the reason shown.
- **The `balance` assertion is the durable record** of "reconciled through date X", kept in the ledger. A per-account "reconciled through" indicator is derived from assertions, per account and currency.
- The assistant may explain residuals or parse a pasted statement, but it calls the same matcher.

## Alternatives considered and rejected

- **A separate reconciliation store.** Rejected: state placement keeps facts in the ledger.
- **Re-examining all history on every reconcile.** Rejected: the last assertion is the boundary unless the user asks.
- **First-match pairing.** Rejected: two identical payments on nearby dates would both match one entry.

## Consequences

- Holdings reconciliation for investments uses the same report shape (ADR-0032).

## References

review:12 §2, §3 Q4, §4; review:14 L5-16..22 (reconciled-through indicator); ADR-0027, ADR-0032; INV-14; CAP-RECON-STATEMENT, CAP-RECON-AUTO-ACCEPT, CAP-RECON-THROUGH.
