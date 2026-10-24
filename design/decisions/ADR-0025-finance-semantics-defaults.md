# ADR-0025: Finance semantics defaults

This record fixes the meaning of the core numbers users see: balances, currencies, future-dated entries and stale prices.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** domain kernel, finance

## Context

The review found period balances summed as floats with Equity treated as a flow, "as of" meaning different things on different paths, a foreign-currency bank account hidden by the cash rule, and dashboards showing two different Total Assets for the same day once a future-dated entry existed (review:11 L3-06, L3-13; review:14 L5-01). The owner is not a finance expert, so these follow finance and Beancount/Fava practice.

## Decision

(owner, 2026-10-24)
- **Period balances:** Assets, Liabilities and Equity are cumulative to the end date; Income and Expenses cover the period. When totals are shown, Retained Earnings and Net Income are synthesized (Fava/Beancount clamp semantics). Sums are exact.
- **Current balances and net worth exclude entries dated after today**, as a bank's ledger balance does. An "include scheduled" toggle shows them.
- **Currency vs holding:** `operating_currency` means only "report in these" (Beancount/Fava semantics). ISO-4217 codes count as cash by default. `Currency` is a role the Profile assigns, not a lexical class. This is a one-time reclassification of existing ledgers, visible in Cash & Deposits. Foreign cash still needs FX conversion for reporting totals.
- **Stale prices never exclude a position** from totals (that would understate net worth). A position is valued at its last price and badged with the price date. Staleness thresholds: daily cadence 7 days, monthly 45 days, manual never.
- **Every position is valued** (units × price as of the date), whatever its cost basis, so holdings bought with only an `@` price never vanish.
- **Realized gains come from Beancount's booking** (lot reductions), not from sale-leg annotations.
- **Fiscal year:** deferred (CAP-FISCAL-YEAR).

## Alternatives considered and rejected

- **Equity as a period flow.** Rejected: it is not how a balance sheet works.
- **`operating_currency` as the "is cash" whitelist** (legacy). Rejected: it conflates "reporting currency" with "cash" and hides foreign bank accounts.
- **Excluding stale positions.** Rejected: understates net worth.

## Consequences

- One backend function per financial question (the `ledger` step measures, ADR-0029) implements these rules once.
- The investment measures follow the same rules (ADR-0030).

## References

review:11 Q2, Q3, Q5, Q6, Q8, L3-06, L3-13; review:14 L5-01, M-NC, L5-11; ADR-0024, ADR-0029, ADR-0030; INV-02.
