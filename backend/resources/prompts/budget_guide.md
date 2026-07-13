# Budgeting in Finzytrack — assistant primer

Concise basics for answering budget questions and building/adjusting budget
dashboards. The dashboard recipe format itself is in `get_recipe_schema`; the
compute functions are in `get_compute_functions`.

## What a budget is

A budget is a Beancount `custom "budget"` directive in the user's ledger:

```
2026-01-01 custom "budget" Expenses:Food "monthly" 500 USD
```

- `interval` ∈ `daily | weekly | monthly | quarterly | yearly`.
- **Effective-dated**: a directive applies from its date until a later directive
  for the same account+currency supersedes it.
- **Inclusive-parent**: a budget on `Expenses:Food` is compared against spend on
  `Expenses:Food` *and all descendants*. (Beancount can't budget the bare
  `Expenses` root — accounts need ≥1 subaccount.)
- **Multi-currency**: a directive applies to its own currency; one account can
  have several (one per currency).
- **Last-wins**: two directives with the same date+account+currency → the last
  one wins (a warning is surfaced).

## How budgeting maps onto the recipe DAG

Budgets are **not** a SQL table. A budget widget is a DAG of steps:

- **`compute` step `budget_for_range`** supplies budget numbers (full-precision
  daily-equivalent over a range; `groupBy: "period"` for a per-month series).
  Call `get_compute_functions` for its exact args/output.
- **`sql` step** supplies actuals from `postings`.
- **`transform` step** merges them:
  - `joinBudgetActual` — budget-vs-actual variance (flat: one row per budgeted
    account; **remainder mode** via `config.totalAccount` adds synthetic
    `Unbudgeted` + `Total` rows for catch-all/zero-based).
  - `runningSum` — cumulative/burn-down columns.
  - `envelopeRollover` — per-period rollover (unspent carries forward).
  - `joinByPeriod` — merge per-period budgets+actuals into `{period, budget, actual}`.

You use this fixed catalog — you cannot invent compute functions or transforms.
If the catalog can't express a budgeting style, say so.

## Budgeting styles → seeded demo dashboards

Each common style is "just a recipe". The seeded demos (list them with
`list_recipes`, read with `read_recipe`, then copy/tweak) are:

| Style | Demo dashboard id | Transform |
|---|---|---|
| Monthly, no rollover (at a glance) | `budget-overview` | `joinBudgetActual` (flat) + `budgetSummary` |
| Top-down / zero-based catch-all | `budget-zero-based` | `joinBudgetActual` (remainder mode) |
| Burn-down / pace | `budget-burndown` | `joinByPeriod` + `runningSum` |
| Envelope with rollover | `budget-envelopes` | `envelopeRollover` |

(50/30/20 and pay-yourself-first are the same machinery as `budget-overview`
with different budgeted numbers/labels.)

## Answering vs. authoring

- **Compute answers** ("what's my Food budget in June?", "am I over budget?") →
  `execute_compute("budget_for_range", {from, to, currency, account?})`, plus
  `execute_query` for actuals.
- **Raw directives** ("what budgets are set?") → `execute_query` against
  `custom_directives` (`type='budget'`), or read the user's Budgets view.
- **Build/adjust a dashboard** → standard authoring flow (`get_recipe_schema`,
  `get_compute_functions`, `preview_recipe` → `write_recipe`), or copy a demo.
- **Setting budget amounts** is done by the user via the Budgets UI, not by you.
