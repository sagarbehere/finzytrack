import { ref } from 'vue'
import type {
  WidgetRecipe,
  JsonWidgetRecipe,
  ValueFormat,
  QueryEngineType,
} from '@/types/recipes'
import { LedgerService } from '@/services/generated-api'
import type { QueryRequest } from '@/services/generated-api'
import { formatAmount, formatSignedAmount } from '@/utils/currencyFormat'
import { errorHandler } from '@/utils/ErrorHandler'
import { applyPredefinedTransform } from '@/composables/useRecipeTransforms'

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

// ============================================================================
// Predefined Formats
// ============================================================================

/**
 * Predefined format functions for JSON recipes.
 * These can format both numbers and strings depending on the format type.
 * Uses USD as the default currency — use getFormats(currency) for locale-aware currency formatting.
 */
export const predefinedFormats: Record<ValueFormat, (value: unknown) => string> = getFormats()

/**
 * Create a set of format functions with the given currency for locale-aware formatting.
 * Currency-dependent formats (currency, signedCurrency) use the specified currency code
 * to determine locale and symbol. Non-currency formats are unaffected.
 *
 * @param currency - ISO 4217 currency code (e.g., 'USD', 'INR'). Defaults to 'USD'.
 */
export function getFormats(currency?: string): Record<ValueFormat, (value: unknown) => string> {
  const curr = currency || 'USD'
  return {
    // Number formats
    currency: (value) => {
      const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
      return formatAmount(num, curr)
    },

    signedCurrency: (value) => {
      const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
      return formatSignedAmount(num, curr)
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

    // JSON recipe with predefined transform (simple or configurable)
    return applyPredefinedTransform(recipe.id, recipe.transform, rows)
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
      errorHandler.display(err)
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
        // `default` is typed as `string | number | { $gen, ... }` per the JSON
        // schema, but resolveGenerators (called by useRecipeLoader before the
        // recipe reaches us) replaces every `$gen` object with its scalar
        // result, so by this point the value is always a string or number.
        params[param.name] = param.default as string | number
      }
    }
    return params
  }

  /**
   * Get format function for a value format type, with optional currency for locale-aware formatting.
   */
  function getFormatFunction(format?: ValueFormat, currency?: string): ((value: number) => string) | undefined {
    if (!format) return undefined
    return getFormats(currency)[format]
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
