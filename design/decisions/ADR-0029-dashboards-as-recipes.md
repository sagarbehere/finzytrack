# ADR-0029: Dashboards are user-authored recipes, run on the backend; budgets as measures

This record decides the recipe invariant, the recipe engine, how the format evolves, and budget semantics.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** recipes, dashboards, budgets

## Context

Dashboards are a non-negotiable product principle: users author them. v0.3 interpreted recipes in the browser, had three divergent validators, nine hand-copied net-worth folds with two price rules, silently dropped unknown arguments, and overstated nested budgets (review:14).

## Decision

(owner, 2026-10-24)
- **Invariant (INV-08):** a dashboard is a **non-executable JSON recipe** of arbitrary user queries, a catalog of generic server-side functions, a catalog of transforms, a catalog of visualization widgets, and shared steps. Each catalog grows as needed; nothing is baked in. The one deliberate limit: option strings are data; ECharts template strings are fine, raw HTML and JavaScript formatters are not.
- **The interpreter runs on the backend** (`recipes/`, `POST /api/w/{ws}/recipes:run`): one strict validator with located issues (warn-first period, unknown args rejected), generators, the step DAG, transforms, typed per-currency money columns. The UI, the AI and any future builder use the same validator and `run`.
- **Evolution:**
  1. **A semantic `ledger` step is the primary vocabulary:** measures (balance, flow, value, cost, unrealized/realized gain, income, budget, budget actual) by dimensions (account with level, account kind, currency, period, payee, tag, metadata keys), with as-of, filters and conversion, built on the kernel.
  2. **SQL stays first-class** (hybrid): arbitrary SQL over the versioned `r_*` surface, SQL over earlier steps' outputs (the in-memory `steps` schema), and SQL helpers (`dsum`, `price_asof`) (ADR-0016). BQL steps are allowed under the statement allowlist.
  3. **A widget library with overrides:** dashboards reference shipped widgets by id and override what changes; full copy-and-edit stays available.
  4. **Shared steps stay** as a reuse and naming feature; a backend memo (keyed on ledger version plus a transitive hash of step inputs and parameters) runs them once per board. The frontend shared-steps query is dropped.
- **Self-describing catalogs** (args schema, input/output frame shape, description) generate the docs, the AI guide and a future GUI builder's pickers. `run` can execute a single widget or a partial recipe and returns column metadata. Stable ids and lossless round-trip of recipe JSON. A GUI builder is not planned, but must stay possible.
- **One viz registry** as the extension contract; responses are gzipped.
- **Budgets:** `custom "budget"` directives; budget numbers are `ledger`-step measures. Income budgets are supported and sign-normalized (over target is good; income totals stay separate from expense totals). Nested parent and child budgets are not double-counted. **Negative carry-forward** is kept and documented (it conserves money without a "to be budgeted" pool). Asset/Liability goals are a different instrument (CAP-BUD-GOALS, open).
- **Seed fixes for edited seed dashboards:** a notice with one-click replace (with backup); a modified copy is never overwritten.
- v0.2.2 table and function names keep working (ADR-0048).

## Alternatives considered and rejected

- **Baked-in, code-defined dashboards.** Rejected: violates the invariant.
- **A pure semantic layer with no SQL.** Rejected: users would wait for new measures.
- **Dashboard-level `extends`.** Rejected: beyond the decided widget-library model.
- **Interpreting recipes in the browser.** Rejected: the AI could not run what it writes, and finance logic split across sides.

## Consequences

- Only 1 of 74 seed widgets loses a purely client-side refresh.
- The partial-run and SQL-over-steps capabilities are release-blocking (ADR-0043).

## References

review:14 §0, §0a, §1, §2, §3; review:15 §2 (content packs); ADR-0016, ADR-0025, ADR-0043, ADR-0048; INV-08, INV-09; CAP-DASH-SQL-OVER-STEPS, CAP-DASH-GUI-BUILDER.
