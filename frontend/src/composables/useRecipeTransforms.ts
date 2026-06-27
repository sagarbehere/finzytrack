import type {
  TransformConfig,
  TransformContext,
  PivotData,
  PivotRow,
} from '@/types/recipes'
import { add, sub, mul, div, toMoney, toNumber, zero, lt, type Money } from '@/utils/money'

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

    // Handle numeric comparison
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return order === 'asc' ? aVal - bVal : bVal - aVal
    }

    // Handle string comparison
    const aStr = String(aVal ?? '')
    const bStr = String(bVal ?? '')
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

  const actualByKey = new Map<string, Money>()
  for (const a of actuals) {
    const k = key(String(a.account ?? ''), String(a.currency ?? ''))
    actualByKey.set(k, add(actualByKey.get(k) ?? zero(), toMoneyOr0(a.actual)))
  }

  if (config?.totalAccount) {
    return remainderMode(budgets, actuals, actualByKey, String(config.totalAccount), frac)
  }

  // Flat mode.
  const rows: VarianceRow[] = []
  for (const b of budgets) {
    const account = String(b.account ?? '')
    const currency = String(b.currency ?? '')
    const budgetM = toMoneyOr0(b.budget)
    const actualM = actualByKey.get(key(account, currency)) ?? zero()
    rows.push(buildVarianceRow(account, currency, budgetM, actualM, frac))
  }
  return rows
}

interface RemainderResult {
  rows: VarianceRow[]
  unbudgeted: { account: string; budget: string | null; actual: string }
  total: { account: string; budget: string | null; actual: string }
  overAllocated: boolean
  noTotalBudget: boolean
}

function remainderMode(
  budgets: Record<string, unknown>[],
  actuals: Record<string, unknown>[],
  actualByKey: Map<string, Money>,
  totalAccount: string,
  frac: number | null,
): RemainderResult {
  // Budgets strictly under the total node (the "named" carve-outs).
  const named = budgets.filter((b) => isStrictlyUnder(String(b.account ?? ''), totalAccount))
  const namedAccounts = named.map((b) => String(b.account))
  // Maximal named = no other named account is a strict ancestor.
  const maximalNamed = namedAccounts.filter(
    (n) => !namedAccounts.some((m) => m !== n && isStrictlyUnder(n, m)),
  )

  const totalBudgetRow = budgets.find((b) => String(b.account) === totalAccount)
  const currency = String((totalBudgetRow ?? named[0])?.currency ?? actuals[0]?.currency ?? '')

  // Inclusive actual under the total node.
  let totalActual = zero()
  for (const a of actuals) {
    if (isUnderInclusive(String(a.account ?? ''), totalAccount)) {
      totalActual = add(totalActual, toMoneyOr0(a.actual))
    }
  }
  // Named actual: spend under ANY maximal-named prefix (set membership → once).
  let namedActual = zero()
  for (const a of actuals) {
    const acct = String(a.account ?? '')
    if (maximalNamed.some((n) => isUnderInclusive(acct, n))) {
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

  // Per-named line (inclusive budget/actual — may overlap when nested, by §4.1).
  const rows: VarianceRow[] = named.map((b) => {
    const account = String(b.account)
    const budgetM = toMoneyOr0(b.budget)
    const actualM = actualByKey.get(key(account, currency)) ?? zero()
    return buildVarianceRow(account, currency, budgetM, actualM, frac)
  })

  return {
    rows,
    unbudgeted: { account: 'Unbudgeted', budget: remainderBudget, actual: remainderActual },
    total: { account: 'Total', budget: totalBudget, actual: totalActual },
    overAllocated,
    noTotalBudget,
  }
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

/**
 * envelopeRollover — stateless rollover (§14). Inputs: per-period budgets and
 * per-period actuals. Emits, per period: { period, budget, actual, available,
 * carryover, overspent }. Carryover is the cumulative (budget − actual);
 * negative carries forward (R2, no clamp).
 */
function envelopeRollover(inputs: unknown[]): unknown {
  const budgets = asRows(inputs[0])
  const actuals = asRows(inputs[1])

  const budgetByPeriod = new Map<string, Money>()
  for (const b of budgets) budgetByPeriod.set(String(b.period ?? ''), toMoneyOr0(b.budget))
  const actualByPeriod = new Map<string, Money>()
  for (const a of actuals) actualByPeriod.set(String(a.period ?? ''), toMoneyOr0(a.actual))

  const periods = Array.from(new Set([...budgetByPeriod.keys(), ...actualByPeriod.keys()])).sort()

  let carryover = zero() // carryover at end of previous period
  const out: Record<string, unknown>[] = []
  for (const period of periods) {
    const budget = budgetByPeriod.get(period) ?? zero()
    const actual = actualByPeriod.get(period) ?? zero()
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
  pivot: pivotTransform,
  joinBudgetActual,
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
