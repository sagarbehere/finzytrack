import type {
  TransformConfig,
  TransformContext,
  PivotData,
  PivotRow,
} from '@/types/recipes'
import { add, sub, mul, div, toMoney, toNumber, zero, lt, sign, type Money } from '@/utils/money'

// ============================================================================
// Transform catalog (DAG model)
//
// Every transform is a named, client-side, pure function over the resolved
// outputs of prior steps. Uniform signature:
//
//     (inputs: unknown[], config?: TransformConfig, ctx?: TransformContext) => unknown
//
// `inputs` are the resolved outputs of the steps named in the transform step's
// `inputs: [{{steps.x}}, ...]`. Single-input transforms read `inputs[0]` as
// their `rows`. `ctx.params` carries the resolved recipe parameters.
//
// See dev-docs/refactored-dashboard-recipes.md §3.2, §4.5.
// ============================================================================

export type TransformFn = (
  inputs: unknown[],
  config?: TransformConfig,
  ctx?: TransformContext,
) => unknown

/** Coerce the first input into a rows array (the common single-input case). */
function asRows(input: unknown): Record<string, unknown>[] {
  return Array.isArray(input) ? (input as Record<string, unknown>[]) : []
}

// ============================================================================
// Pivot Transform Helpers
// ============================================================================

/**
 * Format a YYYY-MM string as "Month YYYY" (e.g. "2025-01" → "January 2025").
 */
function formatMonthYear(yearMonth: string): string {
  const [year, month] = yearMonth.split('-')
  const monthNames = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ]
  const idx = parseInt(month, 10) - 1
  return `${monthNames[idx] ?? month} ${year}`
}

/**
 * Compute the last day of the month for a YYYY-MM string.
 */
function monthEndDate(yearMonth: string): string {
  const [year, month] = yearMonth.split('-').map(Number)
  const lastDay = new Date(year, month, 0).getDate()
  return `${yearMonth}-${String(lastDay).padStart(2, '0')}`
}

// ============================================================================
// Transforms
// ============================================================================

/**
 * none — pass rows through unchanged.
 */
function noneTransform(inputs: unknown[]): unknown {
  return asRows(inputs[0])
}

/**
 * firstRow — extract the first row as the result (or {} when empty).
 */
function firstRowTransform(inputs: unknown[]): unknown {
  const rows = asRows(inputs[0])
  return rows.length > 0 ? rows[0] : {}
}

/**
 * firstValue — extract the first value from the first row (or null).
 */
function firstValueTransform(inputs: unknown[]): unknown {
  const rows = asRows(inputs[0])
  if (rows.length === 0) return null
  const values = Object.values(rows[0])
  return values.length > 0 ? values[0] : null
}

/**
 * sortBy — sort rows by a field. Config: { field, order? }.
 */
function sortByTransform(inputs: unknown[], config?: TransformConfig): unknown {
  const rows = asRows(inputs[0])
  const field = config?.field as string | undefined
  const order = (config?.order as string | undefined) || 'asc'
  if (!field) return rows

  return [...rows].sort((a, b) => {
    const aVal = a[field]
    const bVal = b[field]

    // Numeric comparison — covers JS numbers AND decimal-string Money values
    // (budget/actual/remaining are TEXT-decimal strings, so a naive string sort
    // would order "-600" / "50" / "1200" wrongly). When both parse as finite
    // numbers, order by exact decimal.js comparison (not float subtraction) so
    // large/precise Money strings can't misorder near float limits; otherwise
    // fall back to locale.
    const aStr = String(aVal ?? '').trim()
    const bStr = String(bVal ?? '').trim()
    if (aStr !== '' && bStr !== '' && Number.isFinite(Number(aStr)) && Number.isFinite(Number(bStr))) {
      const cmp = sign(sub(toMoney(aStr), toMoney(bStr)))
      return order === 'asc' ? cmp : -cmp
    }

    const cmp = aStr.localeCompare(bStr)
    return order === 'asc' ? cmp : -cmp
  })
}

/**
 * limit — keep the first N rows. Config: { count? }.
 */
function limitTransform(inputs: unknown[], config?: TransformConfig): unknown {
  const rows = asRows(inputs[0])
  const count = (config?.count as number | undefined) || 10
  return rows.slice(0, count)
}

/**
 * pluck — extract a single field from all rows as an array. Config: { field }.
 */
function pluckTransform(inputs: unknown[], config?: TransformConfig): unknown {
  const rows = asRows(inputs[0])
  const field = config?.field as string | undefined
  if (!field) return rows
  return rows.map((row) => row[field])
}

/**
 * where — filter rows by a field predicate. Config: { field, equals? , notEquals?, in? }.
 * Returns the matching rows (still an array — chain `firstRow`/`limit` to reduce).
 * The common use is slicing a multi-row result (e.g. joinBudgetActual remainder
 * mode) down to one role: `where(kind == "total")` → the grand-total row for a KPI.
 * Comparisons are strict equality against the raw field value; with `in`, the
 * value must be one of the listed values.
 */
function whereTransform(inputs: unknown[], config?: TransformConfig): unknown {
  const rows = asRows(inputs[0])
  const field = config?.field as string | undefined
  if (!field) return rows
  const hasEquals = config?.equals !== undefined
  const hasNotEquals = config?.notEquals !== undefined
  const inList = Array.isArray(config?.in) ? (config!.in as unknown[]) : undefined
  return rows.filter((row) => {
    const v = row[field]
    if (inList) return inList.includes(v)
    if (hasNotEquals) return v !== config!.notEquals
    if (hasEquals) return v === config!.equals
    return true
  })
}

/**
 * Pivot rows into a PivotData structure suitable for RecipePivotTable.
 *
 * Config: {
 *   rowField: "account",       // column used as row labels
 *   columnField: "year_month", // column whose distinct values become column headers
 *   valueField: "amount",      // column containing cell values
 *   formatColumn: "monthYear", // optional: format "2025-01" → "January 2025"
 *   sortRowsBy: "total_desc",  // optional: row sort order (default: total_desc)
 * }
 *
 * columnMeta entries expose { rawValue, startDate, endDate } for YYYY-MM column fields,
 * making them available in JSON valueLink templates as {{columnMeta.startDate}}, etc.
 */
function pivotTransform(inputs: unknown[], config?: TransformConfig): PivotData {
  const rows = asRows(inputs[0])
  const rowField = (config?.rowField as string | undefined) ?? 'account'
  const columnField = (config?.columnField as string | undefined) ?? 'year_month'
  const valueField = (config?.valueField as string | undefined) ?? 'amount'

  // Collect unique column keys and per-row data.
  // Sums are kept as Money for exactness; we convert to JS number at the
  // final assembly step since PivotRow is a display surface (see
  // dev-docs/money-types.md).
  const columnsSet = new Set<string>()
  const rowsMap = new Map<string, Map<string, Money>>()

  for (const row of rows) {
    const rowLabel = String(row[rowField] ?? '')
    const colKey = String(row[columnField] ?? '')
    const raw = row[valueField]
    const value: Money = raw === null || raw === undefined || raw === '' ? zero() : toMoney(raw as string | number)

    columnsSet.add(colKey)
    if (!rowsMap.has(rowLabel)) rowsMap.set(rowLabel, new Map())
    rowsMap.get(rowLabel)!.set(colKey, value)
  }

  // Sort column keys (YYYY-MM and similar strings sort correctly as strings)
  const rawColumns = Array.from(columnsSet).sort()

  // Build display headers
  const columns = rawColumns.map((col) => {
    if (config?.formatColumn === 'monthYear') return formatMonthYear(col)
    return col
  })

  // Build columnMeta — always include rawValue; add date range for YYYY-MM columns
  const columnMeta: Record<string, unknown>[] = rawColumns.map((col) => {
    const isYearMonth = /^\d{4}-\d{2}$/.test(col)
    return {
      rawValue: col,
      startDate: isYearMonth ? `${col}-01` : col,
      endDate: isYearMonth ? monthEndDate(col) : col,
    }
  })

  // Build pivot rows — Decimal math throughout, convert to number at the end.
  const columnTotalsMoney: Record<string, Money> = {}
  for (const col of columns) columnTotalsMoney[col] = zero()
  let grandTotalMoney: Money = zero()

  const pivotRows: PivotRow[] = []
  for (const [label, colMap] of rowsMap) {
    const values: Record<string, number> = {}
    let rowTotalMoney: Money = zero()

    for (let i = 0; i < rawColumns.length; i++) {
      const amountMoney = colMap.get(rawColumns[i]) ?? zero()
      if (amountMoney !== '0') {
        values[columns[i]] = toNumber(amountMoney)
        rowTotalMoney = add(rowTotalMoney, amountMoney)
        columnTotalsMoney[columns[i]] = add(columnTotalsMoney[columns[i]], amountMoney)
      }
    }

    grandTotalMoney = add(grandTotalMoney, rowTotalMoney)
    pivotRows.push({ label, values, total: toNumber(rowTotalMoney) })
  }

  const columnTotals: Record<string, number> = {}
  for (const col of columns) columnTotals[col] = toNumber(columnTotalsMoney[col])
  const grandTotal = toNumber(grandTotalMoney)

  // Sort rows
  const sortBy = (config?.sortRowsBy as string | undefined) ?? 'total_desc'
  if (sortBy === 'total_desc') pivotRows.sort((a, b) => (b.total ?? 0) - (a.total ?? 0))
  else if (sortBy === 'total_asc') pivotRows.sort((a, b) => (a.total ?? 0) - (b.total ?? 0))
  else if (sortBy === 'label_asc') pivotRows.sort((a, b) => a.label.localeCompare(b.label))
  else if (sortBy === 'label_desc') pivotRows.sort((a, b) => b.label.localeCompare(a.label))

  return { columns, rows: pivotRows, columnTotals, grandTotal, columnMeta }
}

// ============================================================================
// Budget transforms (dev-docs/budget.md §13, §14)
//
// All money arithmetic is exact (decimal.js Money); money fields are emitted as
// decimal strings per money-types.md (G7). Budget rows come from the
// budget_for_range compute step ({ account, currency, budget }); actuals from a
// sql step ({ account, currency, actual }).
// ============================================================================

function key(account: string, currency: string): string {
  return `${account} ${currency}`
}

function toMoneyOr0(v: unknown): Money {
  return v === null || v === undefined || v === '' ? zero() : toMoney(v as string | number)
}

/** Linear time-elapsed fraction (A3) in [0,1], or null when bounds are absent. */
function elapsedFraction(config?: TransformConfig): number | null {
  const start = config?.periodStart as string | undefined
  const end = config?.periodEnd as string | undefined
  const asOf = (config?.asOf as string | undefined) // tests pass this for determinism
  if (!start || !end) return null
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  const now = asOf ? new Date(asOf).getTime() : Date.now()
  if (!(e > s)) return null
  const frac = (now - s) / (e - s)
  return frac < 0 ? 0 : frac > 1 ? 1 : frac
}

function directionFor(account: string): 'under-good' | 'over-good' {
  // Income is good-when-over-target; everything else (expenses) good-when-under (§4.4).
  return account.startsWith('Income') ? 'over-good' : 'under-good'
}

interface VarianceRow {
  account: string
  currency: string
  budget: string
  actual: string
  remaining: string
  pctUsed: number | null
  pace: string | null
  direction: 'under-good' | 'over-good'
}

function buildVarianceRow(
  account: string,
  currency: string,
  budgetM: Money,
  actualM: Money,
  frac: number | null,
): VarianceRow {
  const remaining = sub(budgetM, actualM)
  const pctUsed = budgetM === '0' ? null : toNumber(div(actualM, budgetM))
  const pace = frac === null ? null : mul(budgetM, toMoney(frac))
  return {
    account,
    currency,
    budget: budgetM,
    actual: actualM,
    remaining,
    pctUsed,
    pace: pace === null ? null : pace,
    direction: directionFor(account),
  }
}

/** Is `descendant` strictly under `ancestor` in the account tree? */
function isStrictlyUnder(descendant: string, ancestor: string): boolean {
  return descendant.startsWith(ancestor + ':')
}

/** Is `account` at-or-under `node` (inclusive subtree)? */
function isUnderInclusive(account: string, node: string): boolean {
  return account === node || isStrictlyUnder(account, node)
}

/**
 * joinBudgetActual — budget-vs-actual variance.
 *
 * Flat mode (default): one row per budgeted (account, currency).
 * Remainder mode (config.totalAccount, §13): additionally emits a synthetic
 * "Unbudgeted" row and a "Total" row, with an overAllocated flag, using the
 * maximal-named-subtree rule so nested budgets don't double-count.
 */
function joinBudgetActual(inputs: unknown[], config?: TransformConfig): unknown {
  const budgets = asRows(inputs[0])
  const actuals = asRows(inputs[1])
  const frac = elapsedFraction(config)

  if (config?.totalAccount) {
    return remainderMode(budgets, actuals, String(config.totalAccount))
  }

  // Flat mode. Actuals roll up INCLUSIVELY to each budgeted account (§4.1
  // inclusive-parent): a budget on a parent is compared against the parent's
  // own spend and all descendants. Descendant budgets still get their own row.
  const rows: VarianceRow[] = []
  for (const b of budgets) {
    const account = String(b.account ?? '')
    const currency = String(b.currency ?? '')
    const budgetM = toMoneyOr0(b.budget)
    let actualM = zero()
    for (const a of actuals) {
      if (String(a.currency ?? '') === currency && isUnderInclusive(String(a.account ?? ''), account)) {
        actualM = add(actualM, toMoneyOr0(a.actual))
      }
    }
    rows.push(buildVarianceRow(account, currency, budgetM, actualM, frac))
  }
  return rows
}

/** A budget row plus the remainder-mode role/flags, as a flat renderable row. */
interface RemainderRow {
  account: string
  currency: string
  budget: string | null
  actual: string
  remaining: string | null
  pctUsed: number | null
  kind: 'named' | 'unbudgeted' | 'total'
  overAllocated?: boolean
  noTotalBudget?: boolean
  note?: string
}

function sumInclusive(actuals: Record<string, unknown>[], node: string, currency: string): Money {
  let sum = zero()
  for (const a of actuals) {
    if (String(a.currency ?? '') === currency && isUnderInclusive(String(a.account ?? ''), node)) {
      sum = add(sum, toMoneyOr0(a.actual))
    }
  }
  return sum
}

function makeRemainderRow(
  account: string,
  currency: string,
  budget: Money | null,
  actual: Money,
  kind: RemainderRow['kind'],
  extra: Partial<RemainderRow> = {},
): RemainderRow {
  return {
    account,
    currency,
    budget,
    actual,
    remaining: budget === null ? null : sub(budget, actual),
    pctUsed: budget === null || budget === '0' ? null : toNumber(div(actual, budget)),
    kind,
    ...extra,
  }
}

/**
 * Remainder mode (§13): emits a FLAT rows array the generic table/gauge viz
 * renders directly — one inclusive line per named account, a synthetic
 * "Unbudgeted" line, and a "Total" line. The maximal-named-subtree rule keeps
 * nested budgets from double-counting; overAllocated / noTotalBudget are
 * row-level flags.
 */
function remainderMode(
  budgets: Record<string, unknown>[],
  actuals: Record<string, unknown>[],
  totalAccount: string,
): RemainderRow[] {
  // Budgets strictly under the total node (the "named" carve-outs).
  const named = budgets.filter((b) => isStrictlyUnder(String(b.account ?? ''), totalAccount))
  const namedAccounts = named.map((b) => String(b.account))
  // Maximal named = no other named account is a strict ancestor.
  const maximalNamed = namedAccounts.filter(
    (n) => !namedAccounts.some((m) => m !== n && isStrictlyUnder(n, m)),
  )

  const totalBudgetRow = budgets.find((b) => String(b.account) === totalAccount)
  const currency = String((totalBudgetRow ?? named[0])?.currency ?? actuals[0]?.currency ?? '')

  const totalActual = sumInclusive(actuals, totalAccount, currency)
  // Named actual: spend under ANY maximal-named prefix (set membership → once).
  let namedActual = zero()
  for (const a of actuals) {
    const acct = String(a.account ?? '')
    if (String(a.currency ?? '') === currency && maximalNamed.some((n) => isUnderInclusive(acct, n))) {
      namedActual = add(namedActual, toMoneyOr0(a.actual))
    }
  }
  const namedBudget = maximalNamed.reduce((sum: Money, n) => {
    const row = budgets.find((b) => String(b.account) === n)
    return add(sum, toMoneyOr0(row?.budget))
  }, zero())

  const noTotalBudget = totalBudgetRow === undefined
  const totalBudget = noTotalBudget ? null : toMoneyOr0(totalBudgetRow!.budget)
  const remainderActual = sub(totalActual, namedActual)
  const remainderBudget = totalBudget === null ? null : sub(totalBudget, namedBudget)
  const overAllocated = remainderBudget !== null && lt(remainderBudget, zero())

  // One inclusive line per named account (may overlap when nested, by §4.1).
  const namedRows: RemainderRow[] = named.map((b) =>
    makeRemainderRow(String(b.account), currency, toMoneyOr0(b.budget), sumInclusive(actuals, String(b.account), currency), 'named'),
  )

  // Surface the edge cases so they're not silent blank columns (§13.2 B2, edge cases).
  const totalNote = noTotalBudget ? `No total budget set on ${totalAccount}` : undefined
  const unbudgetedNote = noTotalBudget
    ? 'Set a total budget to compute the remainder'
    : overAllocated
      ? 'Over-allocated: named budgets exceed the total'
      : undefined

  return [
    ...namedRows,
    makeRemainderRow('Unbudgeted', currency, remainderBudget, remainderActual, 'unbudgeted', { overAllocated, note: unbudgetedNote }),
    makeRemainderRow('Total', currency, totalBudget, totalActual, 'total', { noTotalBudget, note: totalNote }),
  ]
}

/**
 * budgetSummary — collapse budgets + actuals into ONE aggregate row for a ring /
 * KPIs: { account:"All budgets", currency, budget, actual, remaining, pctUsed
 * (0-1), pctUsedPct (0-100) }. Uses the maximal-named-subtree rule (§13.2) so a
 * nested pair (Insurance parent + Insurance:Health child) is not double-counted:
 * only the top-most budgeted accounts contribute, and actuals are summed once by
 * set membership under those prefixes. Currency-scoped inputs (the dashboard
 * filters by currency) → one row; the currency is taken from the first row.
 */
function budgetSummary(inputs: unknown[]): unknown {
  const budgets = asRows(inputs[0])
  const actuals = asRows(inputs[1])
  if (budgets.length === 0) return []
  const currency = String(budgets[0].currency ?? actuals[0]?.currency ?? '')
  const named = budgets.map((b) => String(b.account ?? ''))
  const maximalNamed = named.filter((n) => !named.some((m) => m !== n && isStrictlyUnder(n, m)))

  const budget = maximalNamed.reduce((s: Money, n) => {
    const row = budgets.find((b) => String(b.account) === n)
    return add(s, toMoneyOr0(row?.budget))
  }, zero())
  let actual = zero()
  for (const a of actuals) {
    if (String(a.currency ?? '') === currency && maximalNamed.some((n) => isUnderInclusive(String(a.account ?? ''), n))) {
      actual = add(actual, toMoneyOr0(a.actual))
    }
  }
  const remaining = sub(budget, actual)
  const pctUsed = budget === '0' ? 0 : toNumber(div(actual, budget))
  const pctUsedPct = Math.round(pctUsed * 100)
  return [{
    account: 'All budgets',
    currency,
    budget,
    actual,
    remaining,
    pctUsed,
    pctUsedPct,
    // `value`/`name` alias so a gauge chart (reads .value) can render the ring directly.
    value: pctUsedPct,
    name: 'Used',
  }]
}

/**
 * unbudgetedSpending — the "leak" list: actual rows for accounts NOT covered by
 * any budget, sorted by spend descending. Inclusive-parent aware (§4.1): an
 * account is "covered" if it, or any ancestor, is budgeted in the same currency —
 * so a budget on Expenses:Insurance covers Insurance:Health. Inputs: [budgets,
 * actuals]. Chain a `limit` for a top-N. Output rows keep { account, currency,
 * actual } for a table / list.
 */
function unbudgetedSpending(inputs: unknown[]): unknown {
  const budgets = asRows(inputs[0])
  const actuals = asRows(inputs[1])
  const covered = (acct: string, cur: string): boolean =>
    budgets.some((b) => String(b.currency ?? '') === cur && isUnderInclusive(acct, String(b.account ?? '')))
  const rows = actuals.filter((a) => !covered(String(a.account ?? ''), String(a.currency ?? '')))
  return [...rows].sort((x, y) => sign(sub(toMoneyOr0(y.actual), toMoneyOr0(x.actual))))
}

/**
 * groupBy — collapse rows to one per distinct `key` value, summing each field in
 * `sum` exactly (Money). The key field(s) are carried through (first-seen value);
 * first-seen order is preserved. Config: { key: string | string[], sum: string[] }.
 * The catalog primitive for "totals over time or category" — e.g. roll
 * budget_for_range(groupBy:"period") per-account-per-period rows up to a per-period
 * total budget (`{ key: "period", sum: ["budget"] }`) for a trailing-months chart.
 */
function groupBy(inputs: unknown[], config?: TransformConfig): unknown {
  const rows = asRows(inputs[0])
  const keys = Array.isArray(config?.key)
    ? (config!.key as string[])
    : config?.key
      ? [String(config.key)]
      : []
  const sumFields = Array.isArray(config?.sum) ? (config!.sum as string[]) : []
  if (keys.length === 0) return rows
  const groups = new Map<string, Record<string, unknown>>()
  const order: string[] = []
  for (const row of rows) {
    const gk = keys.map((k) => String(row[k] ?? '')).join(' ')
    let g = groups.get(gk)
    if (!g) {
      g = {}
      for (const k of keys) g[k] = row[k]
      for (const f of sumFields) g[f] = zero()
      groups.set(gk, g)
      order.push(gk)
    }
    for (const f of sumFields) g[f] = add(g[f] as Money, toMoneyOr0(row[f]))
  }
  return order.map((gk) => groups.get(gk)!)
}

/**
 * appendTotal — append a grand-total row summing `field` over ALL input rows,
 * then (optionally) keep only the first `count` data rows above it. Computing the
 * total before slicing means a "top N + Total" table still totals the full set,
 * not just the rows shown. Config: { field (default "actual"), labelField
 * (default "account"), label (default "Total"), count? }. The total row carries
 * `isTotal: true`.
 */
function appendTotal(inputs: unknown[], config?: TransformConfig): unknown {
  const rows = asRows(inputs[0])
  const field = String(config?.field ?? 'actual')
  const labelField = String(config?.labelField ?? 'account')
  const label = String(config?.label ?? 'Total')
  const count = config?.count as number | undefined
  const total = rows.reduce((s: Money, r) => add(s, toMoneyOr0(r[field])), zero())
  const shown = typeof count === 'number' ? rows.slice(0, count) : rows
  return [...shown, { [labelField]: label, [field]: total, isTotal: true }]
}

/**
 * runningSum — burn-down / cumulative. Config: { fields: string[], orderBy }.
 * Accumulates each named field over rows sorted by orderBy, appending a
 * `cumulative<Field>` column per field.
 */
function runningSum(inputs: unknown[], config?: TransformConfig): unknown {
  const rows = asRows(inputs[0])
  const fields = (config?.fields as string[] | undefined) ?? []
  const orderBy = config?.orderBy as string | undefined
  const sorted = orderBy
    ? [...rows].sort((a, b) => String(a[orderBy] ?? '').localeCompare(String(b[orderBy] ?? '')))
    : [...rows]

  const running: Record<string, Money> = {}
  for (const f of fields) running[f] = zero()

  return sorted.map((row) => {
    const out: Record<string, unknown> = { ...row }
    for (const f of fields) {
      running[f] = add(running[f], toMoneyOr0(row[f]))
      out[`cumulative${f.charAt(0).toUpperCase()}${f.slice(1)}`] = running[f]
    }
    return out
  })
}

interface PeriodRow { period: string; budget: Money; actual: Money }

/**
 * Merge per-period budgets (inputs[0]) and per-period actuals (inputs[1]) into
 * one row per period — the union of periods, sorted, zero-filled. Shared by
 * joinByPeriod and envelopeRollover so the merge scaffold lives in one place.
 */
function mergePeriods(inputs: unknown[]): PeriodRow[] {
  const budgetByPeriod = new Map<string, Money>()
  for (const b of asRows(inputs[0])) budgetByPeriod.set(String(b.period ?? ''), toMoneyOr0(b.budget))
  const actualByPeriod = new Map<string, Money>()
  for (const a of asRows(inputs[1])) actualByPeriod.set(String(a.period ?? ''), toMoneyOr0(a.actual))
  const periods = Array.from(new Set([...budgetByPeriod.keys(), ...actualByPeriod.keys()])).sort()
  return periods.map((period) => ({
    period,
    budget: budgetByPeriod.get(period) ?? zero(),
    actual: actualByPeriod.get(period) ?? zero(),
  }))
}

/**
 * joinByPeriod — merge per-period budgets and per-period actuals into one row
 * per period: { period, budget, actual }. The non-rollover building block for
 * burn-down (→ runningSum) and historical month-by-month views.
 */
function joinByPeriod(inputs: unknown[]): unknown {
  return mergePeriods(inputs)
}

/**
 * envelopeRollover — stateless rollover (§14). Inputs: per-period budgets and
 * per-period actuals. Emits, per period: { period, budget, actual, available,
 * carryover, overspent }. Carryover is the cumulative (budget − actual);
 * negative carries forward (R2, no clamp).
 */
function envelopeRollover(inputs: unknown[]): unknown {
  let carryover = zero() // carryover at end of previous period
  const out: Record<string, unknown>[] = []
  for (const { period, budget, actual } of mergePeriods(inputs)) {
    const available = add(carryover, budget) // what you can spend this period
    const endCarryover = sub(available, actual)
    out.push({
      period,
      budget,
      actual,
      available,
      carryover: endCarryover,
      overspent: lt(endCarryover, zero()),
    })
    carryover = endCarryover
  }
  return out
}

// ============================================================================
// Catalog + dispatch
// ============================================================================

/**
 * The transform catalog: name → function. This is the client-side registry the
 * recipe DAG addresses by name. Budget transforms (joinBudgetActual, runningSum,
 * envelopeRollover) are registered here in Phase B1.
 */
export const transformCatalog: Record<string, TransformFn> = {
  none: noneTransform,
  firstRow: firstRowTransform,
  firstValue: firstValueTransform,
  sortBy: sortByTransform,
  limit: limitTransform,
  pluck: pluckTransform,
  where: whereTransform,
  pivot: pivotTransform,
  joinBudgetActual,
  joinByPeriod,
  budgetSummary,
  unbudgetedSpending,
  appendTotal,
  groupBy,
  runningSum,
  envelopeRollover,
}

/** Names of all registered transforms (for validation / discoverability). */
export function transformNames(): string[] {
  return Object.keys(transformCatalog)
}

/**
 * Apply a named transform-catalog function to its resolved step inputs.
 *
 * @param fn     - Transform name (must exist in the catalog).
 * @param inputs - Resolved outputs of the steps referenced by `inputs`.
 * @param config - Transform-specific configuration.
 * @param ctx    - Execution context (resolved recipe parameters).
 */
export function applyTransform(
  fn: string,
  inputs: unknown[],
  config?: TransformConfig,
  ctx?: TransformContext,
): unknown {
  const transformFn = transformCatalog[fn]
  if (!transformFn) {
    throw new Error(`Unknown transform: ${fn}`)
  }
  return transformFn(inputs, config, ctx)
}
