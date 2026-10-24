# ADR-0048: v0.2.2 mirror tables and compute functions keep their names through 0.4.x

This record decides compatibility for users' own SQL and dashboards written against v0.2.2.

- **Status:** Accepted; the `lots` part awaits the owner's re-confirmation
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** mirror, recipes, compatibility

## Context

v0.4 changes the mirror and the compute catalog. Users' saved queries and dashboards may name v0.2.2 tables and functions. Checked against the `v0.2.2` tag: the only compute function is `budget_for_range`; there are no SQL UDFs and no `latest_prices` table; the mirror tables are `postings`, `accounts`, `commodities`, `balance_assertions`, `pad_directives`, `prices`, `notes`, `events`, `documents`, `custom_directives`, `stored_queries`, `ledger_options`, `account_balances`, `lots`, `commodity_usage`, `ledger_errors`, `training_data`.

## Decision

(owner, 2026-10-24)
- **Mirror tables and compute functions that existed in v0.2.2 keep their old names working through 0.4.x**, served by the versioned `r_*` surface as compatibility views. They are dropped later with a warning.
- **Removed functions** (only `budget_for_range`) keep working through 0.4.x with a **located deprecation message** naming the replacement.
- **`lots`:** under this rule the v0.2.2 `lots` table keeps working through 0.4.x, served from Beancount's as-of booked inventory; the end-of-ledger computation stays deleted. **Pending:** the owner's note said v0.2.x had no `lots` table, but the v0.2.2 tag creates one (also v0.2.0 and v0.2.1). The owner is asked to re-confirm; until then the general rule applies.
- Capabilities that existed only in v0.3.0 (for example `latest_prices`, `portfolio_returns`, `portfolio_series`) were never released and need no compatibility.

## Alternatives considered and rejected

- **Drop the old names now and say so in the release notes.** Rejected: cheap to keep, and it avoids silent breakage.

## Consequences

- The `r_*` view golden includes the compatibility views.
- The "decide `lots` before M1" dependency is removed: the general rule decides it.

## References

review:F §2 item 10; review:21 §5 item 7; review:25 §14 item 9; ADR-0015, ADR-0029, ADR-0031; INV-05; CAP-FN-BUDGET-FOR-RANGE, CAP-QUERY-SURFACE.
