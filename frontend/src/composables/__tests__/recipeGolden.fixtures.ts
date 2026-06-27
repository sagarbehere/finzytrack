/**
 * Golden baseline fixtures for the recipe pipeline (Phase 0 of the
 * dashboard-recipe DAG refactor — see dev-docs/refactored-dashboard-recipes.md §7.0).
 *
 * These rows are representative of the SQL output of every transform actually
 * used by the seed recipes (captured BEFORE the executor/transform rewrite):
 *
 *   - `pivot`     — year-summary, widget-gallery  ({ account, year_month, amount })
 *   - `firstRow`  — transaction-count             ({ count })
 *   - `none`      — every kpi/chart widget        (rows pass through unchanged)
 *
 * The companion golden test snapshots the CURRENT transform output over these
 * fixtures. Phase 1 rewrites the catalog to a `(inputs[], config, ctx)`
 * signature; that snapshot is the equivalence guard — the new catalog must
 * reproduce these outputs exactly (refactored-dashboard-recipes.md §7.2/§7.3).
 *
 * The shapes mirror the real seed SQL; the values are fixed (not live) so the
 * golden is deterministic and regenerable.
 */

import type { TransformConfig } from '@/types/recipes'

export interface GoldenCase {
  /** Stable name for the snapshot key. */
  name: string
  /** The seed widget(s) this transform shape is drawn from. */
  source: string
  /** Transform spec exactly as it appears in the seed recipe (string or config). */
  transform: string | TransformConfig
  /** Representative query-result rows feeding the transform. */
  rows: Record<string, unknown>[]
}

/**
 * pivot — year-summary / widget-gallery.
 * Rows: per (account, year_month) expense sums. Includes a sparse account
 * (missing a month) and a second account so column-union + row-sort + totals
 * are all exercised. year_month strings drive the columnMeta date-range logic.
 */
const PIVOT_ROWS: Record<string, unknown>[] = [
  { account: 'Expenses:Groceries', year_month: '2025-01', amount: 650.5 },
  { account: 'Expenses:Groceries', year_month: '2025-02', amount: 712.25 },
  { account: 'Expenses:Groceries', year_month: '2025-03', amount: 698 },
  { account: 'Expenses:EatingOut', year_month: '2025-01', amount: 300 },
  { account: 'Expenses:EatingOut', year_month: '2025-03', amount: 245.75 },
  { account: 'Expenses:Utilities', year_month: '2025-02', amount: 0 },
]

/**
 * firstRow — transaction-count. A single aggregate row.
 */
const FIRSTROW_ROWS: Record<string, unknown>[] = [{ count: 1843 }]

/**
 * none — treemap (expense-treemap) passthrough. Hierarchy/name+value rows.
 */
const TREEMAP_ROWS: Record<string, unknown>[] = [
  { name: 'Groceries', account: 'Expenses:Groceries', value: 650.5, dateFrom: '2025-06-01', dateTo: '2025-06-30' },
  { name: 'HouseRent', account: 'Expenses:HouseRent', value: 2200, dateFrom: '2025-06-01', dateTo: '2025-06-30' },
  { name: 'EatingOut', account: 'Expenses:EatingOut', value: 312.4, dateFrom: '2025-06-01', dateTo: '2025-06-30' },
]

/**
 * none — multi-currency KPI (net-worth / monthly-income). The KPI renderer
 * consumes these rows directly; `none` must pass them through verbatim.
 */
const KPI_MULTICURRENCY_ROWS: Record<string, unknown>[] = [
  { currency: 'USD', amount: 125430.18 },
  { currency: 'INR', amount: 845200.5 },
]

export const GOLDEN_CASES: GoldenCase[] = [
  {
    name: 'pivot/year-summary',
    source: 'year-summary, widget-gallery',
    transform: {
      type: 'pivot',
      rowField: 'account',
      columnField: 'year_month',
      valueField: 'amount',
      formatColumn: 'monthYear',
      sortRowsBy: 'total_desc',
    },
    rows: PIVOT_ROWS,
  },
  {
    name: 'firstRow/transaction-count',
    source: 'transaction-count',
    transform: 'firstRow',
    rows: FIRSTROW_ROWS,
  },
  {
    name: 'none/expense-treemap',
    source: 'expense-treemap',
    transform: 'none',
    rows: TREEMAP_ROWS,
  },
  {
    name: 'none/kpi-multicurrency',
    source: 'net-worth, monthly-income',
    transform: 'none',
    rows: KPI_MULTICURRENCY_ROWS,
  },
]
