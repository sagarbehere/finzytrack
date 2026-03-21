import { ref } from 'vue'
import type {
  WidgetRecipe,
  JsonWidgetRecipe,
  SimpleTransformType,
  TransformConfig,
  ValueFormat,
  QueryEngineType,
  PivotData,
  PivotRow,
} from '@/types/recipes'
import { LedgerService } from '@/services/generated-api'
import type { QueryRequest } from '@/services/generated-api'
import { formatAmount, formatSignedAmount } from '@/utils/currencyFormat'

/**
 * Union type for any widget recipe (TypeScript or JSON)
 */
export type AnyWidgetRecipe = WidgetRecipe | JsonWidgetRecipe

/**
 * Check if a recipe is a JSON recipe (has string/object transform instead of function)
 */
function isJsonRecipe(recipe: AnyWidgetRecipe): recipe is JsonWidgetRecipe {
  return typeof recipe.transform !== 'function'
}

/**
 * Check if a transform is a config object (vs simple string type)
 */
function isTransformConfig(
  transform: string | TransformConfig | undefined
): transform is TransformConfig {
  return typeof transform === 'object' && transform !== null && 'type' in transform
}

// ============================================================================
// Predefined Transforms
// ============================================================================

/**
 * Simple transforms (no configuration needed)
 */
const simpleTransforms: Record<SimpleTransformType, (rows: Record<string, unknown>[]) => unknown> = {
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

/**
 * Configurable transforms
 */
const configurableTransforms: Record<
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

    // Collect unique column keys and per-row data
    const columnsSet = new Set<string>()
    const rowsMap = new Map<string, Map<string, number>>()

    for (const row of rows) {
      const rowLabel = String(row[rowField] ?? '')
      const colKey = String(row[columnField] ?? '')
      const value = Number(row[valueField]) || 0

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

    // Build pivot rows
    const columnTotals: Record<string, number> = {}
    for (const col of columns) columnTotals[col] = 0
    let grandTotal = 0

    const pivotRows: PivotRow[] = []
    for (const [label, colMap] of rowsMap) {
      const values: Record<string, number> = {}
      let rowTotal = 0

      for (let i = 0; i < rawColumns.length; i++) {
        const amount = colMap.get(rawColumns[i]) || 0
        if (amount !== 0) {
          values[columns[i]] = amount
          rowTotal += amount
          columnTotals[columns[i]] += amount
        }
      }

      grandTotal += rowTotal
      pivotRows.push({ label, values, total: rowTotal })
    }

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
// Predefined Formats
// ============================================================================

/**
 * Predefined format functions for JSON recipes
 * These can format both numbers and strings depending on the format type
 */
export const predefinedFormats: Record<ValueFormat, (value: unknown) => string> = {
  // Number formats
  currency: (value) => {
    const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
    return formatAmount(num, 'USD')
  },

  signedCurrency: (value) => {
    const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
    return formatSignedAmount(num, 'USD')
  },

  percent: (value) => {
    const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
    return num.toLocaleString('en-US', {
      style: 'percent',
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    })
  },

  number: (value) => {
    const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
    return num.toLocaleString('en-US', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    })
  },

  compact: (value) => {
    const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
    if (Math.abs(num) >= 1000000) {
      return (num / 1000000).toFixed(1) + 'M'
    }
    if (Math.abs(num) >= 1000) {
      return (num / 1000).toFixed(1) + 'k'
    }
    return num.toFixed(0)
  },

  // Date formats
  date: (value) => {
    const str = String(value)
    const date = new Date(str)
    if (isNaN(date.getTime())) return str
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  },

  dateShort: (value) => {
    const str = String(value)
    const date = new Date(str)
    if (isNaN(date.getTime())) return str
    return date.toLocaleDateString('en-US', {
      year: '2-digit',
      month: 'numeric',
      day: 'numeric',
    })
  },

  // Account name formats
  accountName: (value) => {
    const str = String(value)
    const segments = str.split(':')
    return segments[segments.length - 1] || str
  },

  accountName2: (value) => {
    const str = String(value)
    const segments = str.split(':')
    if (segments.length >= 2) {
      return segments.slice(-2).join(':')
    }
    return str
  },
}

/**
 * Composable for executing recipe queries and transforms.
 * Supports both TypeScript recipes (with function transforms) and
 * JSON recipes (with predefined transforms).
 */
export function useRecipeExecutor() {
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  /**
   * Interpolate parameter values into SQL query.
   * Replaces :paramName placeholders with escaped values.
   */
  function interpolateParameters(
    sql: string,
    params: Record<string, string | number>
  ): string {
    let result = sql
    for (const [key, value] of Object.entries(params)) {
      const placeholder = `:${key}`
      // Numbers don't need quotes, strings need escaped quotes
      const escaped =
        typeof value === 'number'
          ? String(value)
          : `'${String(value).replace(/'/g, "''")}'`
      result = result.replaceAll(placeholder, escaped)
    }
    return result
  }

  /**
   * Apply transform to query results.
   * Handles both function transforms (TypeScript) and predefined transforms (JSON).
   */
  function applyTransform(
    recipe: AnyWidgetRecipe,
    rows: Record<string, unknown>[]
  ): unknown {
    if (!recipe.transform) {
      // No transform specified - return rows as-is
      return rows
    }

    if (typeof recipe.transform === 'function') {
      // TypeScript recipe with function transform
      return recipe.transform(rows)
    }

    // JSON recipe with predefined transform
    if (isTransformConfig(recipe.transform)) {
      // Configurable transform with options
      const transformFn = configurableTransforms[recipe.transform.type]
      if (!transformFn) {
        console.warn(`[Recipe: ${recipe.id}] Unknown configurable transform: ${recipe.transform.type}`)
        return rows
      }
      return transformFn(rows, recipe.transform)
    }

    // Simple string transform
    const transformFn = simpleTransforms[recipe.transform as SimpleTransformType]
    if (!transformFn) {
      console.warn(`[Recipe: ${recipe.id}] Unknown transform: ${recipe.transform}`)
      return rows
    }
    return transformFn(rows)
  }

  /**
   * Execute a recipe with given parameters.
   * Returns transformed data ready for visualization.
   */
  async function executeRecipe(
    recipe: AnyWidgetRecipe,
    parameters: Record<string, string | number>,
    dbType: QueryEngineType = 'sqlite'
  ): Promise<unknown> {
    isLoading.value = true
    error.value = null

    try {
      // Interpolate parameters into SQL
      const sql = interpolateParameters(recipe.query, parameters)
      console.log(`[Recipe: ${recipe.id}] Executing SQL:`, sql)

      // Execute query via API
      const queryRequest: QueryRequest = { query: sql }
      const response = await LedgerService.executeQuery(queryRequest, dbType)

      if (!response.success || !response.data) {
        throw new Error(response.error?.message || 'Query failed: No data returned')
      }

      const rows = response.data.rows as Record<string, unknown>[]
      console.log(`[Recipe: ${recipe.id}] Query returned ${rows.length} rows`)

      // Apply transform
      const data = applyTransform(recipe, rows)

      return data
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to execute recipe'
      error.value = message
      console.error(`[Recipe: ${recipe.id}] Execution error:`, err)
      throw err
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Build initial parameter values from recipe defaults.
   */
  function getDefaultParameters(
    recipe: AnyWidgetRecipe
  ): Record<string, string | number> {
    const params: Record<string, string | number> = {}
    if (recipe.parameters) {
      for (const param of recipe.parameters) {
        params[param.name] = param.default
      }
    }
    return params
  }

  /**
   * Get format function for a value format type
   */
  function getFormatFunction(format?: ValueFormat): ((value: number) => string) | undefined {
    if (!format) return undefined
    return predefinedFormats[format]
  }

  return {
    executeRecipe,
    interpolateParameters,
    getDefaultParameters,
    applyTransform,
    getFormatFunction,
    isJsonRecipe,
    isLoading,
    error,
  }
}
