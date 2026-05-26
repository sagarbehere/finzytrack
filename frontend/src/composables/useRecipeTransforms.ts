import type {
  SimpleTransformType,
  TransformConfig,
  PivotData,
  PivotRow,
} from '@/types/recipes'
import { add, toMoney, toNumber, zero, type Money } from '@/utils/money'

// ============================================================================
// Transform Type Guards
// ============================================================================

/**
 * Check if a transform is a config object (vs simple string type)
 */
export function isTransformConfig(
  transform: string | TransformConfig | undefined
): transform is TransformConfig {
  return typeof transform === 'object' && transform !== null && 'type' in transform
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
// Simple Transforms
// ============================================================================

/**
 * Simple transforms (no configuration needed)
 */
export const simpleTransforms: Record<SimpleTransformType, (rows: Record<string, unknown>[]) => unknown> = {
  none: (rows) => rows,
  firstRow: (rows) => (rows.length > 0 ? rows[0] : {}),
  firstValue: (rows) => {
    if (rows.length === 0) return null
    const firstRow = rows[0]
    const values = Object.values(firstRow)
    return values.length > 0 ? values[0] : null
  },
}

// ============================================================================
// Configurable Transforms
// ============================================================================

/**
 * Configurable transforms (require a TransformConfig with parameters)
 */
export const configurableTransforms: Record<
  TransformConfig['type'],
  (rows: Record<string, unknown>[], config: TransformConfig) => unknown
> = {
  /**
   * Sort rows by a field
   * Config: { type: "sortBy", field: "amount", order: "desc" }
   */
  sortBy: (rows, config) => {
    const field = config.field
    const order = config.order || 'asc'
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
  },

  /**
   * Limit rows to first N
   * Config: { type: "limit", count: 10 }
   */
  limit: (rows, config) => {
    const count = config.count || 10
    return rows.slice(0, count)
  },

  /**
   * Extract a single field from all rows as an array
   * Config: { type: "pluck", field: "account" }
   */
  pluck: (rows, config) => {
    const field = config.field
    if (!field) return rows
    return rows.map((row) => row[field])
  },

  /**
   * Pivot rows into a PivotData structure suitable for RecipePivotTable.
   *
   * Config: {
   *   type: "pivot",
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
  pivot: (rows, config): PivotData => {
    const rowField = config.rowField ?? 'account'
    const columnField = config.columnField ?? 'year_month'
    const valueField = config.valueField ?? 'amount'

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
      if (config.formatColumn === 'monthYear') return formatMonthYear(col)
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
    const sortBy = config.sortRowsBy ?? 'total_desc'
    if (sortBy === 'total_desc') pivotRows.sort((a, b) => (b.total ?? 0) - (a.total ?? 0))
    else if (sortBy === 'total_asc') pivotRows.sort((a, b) => (a.total ?? 0) - (b.total ?? 0))
    else if (sortBy === 'label_asc') pivotRows.sort((a, b) => a.label.localeCompare(b.label))
    else if (sortBy === 'label_desc') pivotRows.sort((a, b) => b.label.localeCompare(a.label))

    return { columns, rows: pivotRows, columnTotals, grandTotal, columnMeta }
  },
}

// ============================================================================
// Apply Transform
// ============================================================================

/**
 * Apply a predefined transform (simple or configurable) to query result rows.
 * For JSON recipes with string or object transforms.
 *
 * @param recipeId - Recipe ID for logging/warnings
 * @param transform - The transform specification (string type or config object)
 * @param rows - Query result rows
 * @returns Transformed data
 */
export function applyPredefinedTransform(
  recipeId: string,
  transform: string | TransformConfig,
  rows: Record<string, unknown>[]
): unknown {
  if (isTransformConfig(transform)) {
    // Configurable transform with options
    const transformFn = configurableTransforms[transform.type]
    if (!transformFn) {
      console.warn(`[Recipe: ${recipeId}] Unknown configurable transform: ${transform.type}`)
      return rows
    }
    return transformFn(rows, transform)
  }

  // Simple string transform
  const transformFn = simpleTransforms[transform as SimpleTransformType]
  if (!transformFn) {
    console.warn(`[Recipe: ${recipeId}] Unknown transform: ${transform}`)
    return rows
  }
  return transformFn(rows)
}
