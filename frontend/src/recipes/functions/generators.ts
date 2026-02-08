/**
 * Generator Functions for JSON Recipes
 *
 * These functions can be invoked from JSON recipes using the { "$gen": "name", ...args } syntax.
 * They generate dynamic values at recipe load time.
 */

import type { RecipeParameterOption } from '@/types/recipes'
import { useConfig } from '@/composables/useConfig'

export interface GeneratorConfig {
  [key: string]: unknown
}

export type GeneratorFunction<T = unknown> = (config: GeneratorConfig) => T

/**
 * Returns the current year as a number
 *
 * Usage: { "$gen": "currentYear" }
 * Returns: 2025 (or whatever the current year is)
 */
export function currentYear(): number {
  return new Date().getFullYear()
}

/**
 * Generates an array of year options for select parameters
 *
 * Usage: { "$gen": "yearRange", "count": 5 }
 * Returns: [{ value: 2025, label: "2025" }, { value: 2024, label: "2024" }, ...]
 *
 * @param config.count - Number of years to include (default: 5)
 * @param config.startYear - Starting year (default: current year)
 */
export function yearRange(config: GeneratorConfig = {}): RecipeParameterOption[] {
  const count = (config.count as number) || 5
  const startYear = (config.startYear as number) || new Date().getFullYear()

  const years: RecipeParameterOption[] = []
  for (let i = 0; i < count; i++) {
    const year = startYear - i
    years.push({ value: year, label: String(year) })
  }
  return years
}

/**
 * Generates month options for select parameters
 *
 * Usage: { "$gen": "monthOptions" }
 * Returns: [{ value: 1, label: "January" }, { value: 2, label: "February" }, ...]
 *
 * @param config.format - "long" (January) or "short" (Jan), default: "long"
 */
export function monthOptions(config: GeneratorConfig = {}): RecipeParameterOption[] {
  const format = (config.format as string) || 'long'
  const longNames = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ]
  const shortNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

  const names = format === 'short' ? shortNames : longNames
  return names.map((name, index) => ({ value: index + 1, label: name }))
}

/**
 * Generates quarter options for select parameters
 *
 * Usage: { "$gen": "quarterOptions" }
 * Returns: [{ value: 1, label: "Q1" }, { value: 2, label: "Q2" }, ...]
 */
export function quarterOptions(): RecipeParameterOption[] {
  return [
    { value: 1, label: 'Q1' },
    { value: 2, label: 'Q2' },
    { value: 3, label: 'Q3' },
    { value: 4, label: 'Q4' },
  ]
}

/**
 * Generates account type options for select parameters
 *
 * Usage: { "$gen": "accountTypeOptions" }
 * Returns: [{ value: "Assets", label: "Assets" }, ...]
 */
export function accountTypeOptions(): RecipeParameterOption[] {
  return [
    { value: 'Assets', label: 'Assets' },
    { value: 'Liabilities', label: 'Liabilities' },
    { value: 'Income', label: 'Income' },
    { value: 'Expenses', label: 'Expenses' },
    { value: 'Equity', label: 'Equity' },
  ]
}

// ============================================================================
// Date Generators
// ============================================================================

/**
 * Helper to format a date as YYYY-MM-DD
 */
function formatDate(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * Returns today's date as a string
 *
 * Usage: { "$gen": "today" }
 * Usage: { "$gen": "today", "format": "iso" }
 * Returns: "2025-02-06" (or other format)
 *
 * @param config.format - "iso" (YYYY-MM-DD, default), "year" (YYYY), "month" (YYYY-MM)
 */
export function today(config: GeneratorConfig = {}): string {
  const format = (config.format as string) || 'iso'
  const now = new Date()

  switch (format) {
    case 'year':
      return String(now.getFullYear())
    case 'month':
      return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    case 'iso':
    default:
      return formatDate(now)
  }
}

/**
 * Returns the first day of a month
 *
 * Usage: { "$gen": "startOfMonth" }
 * Usage: { "$gen": "startOfMonth", "offset": -1 }  // last month
 * Returns: "2025-02-01"
 *
 * @param config.offset - Month offset from current (0 = current, -1 = last month, 1 = next month)
 */
export function startOfMonth(config: GeneratorConfig = {}): string {
  const offset = (config.offset as number) || 0
  const now = new Date()
  const date = new Date(now.getFullYear(), now.getMonth() + offset, 1)
  return formatDate(date)
}

/**
 * Returns the last day of a month
 *
 * Usage: { "$gen": "endOfMonth" }
 * Usage: { "$gen": "endOfMonth", "offset": -1 }  // last month
 * Returns: "2025-02-28"
 *
 * @param config.offset - Month offset from current (0 = current, -1 = last month, 1 = next month)
 */
export function endOfMonth(config: GeneratorConfig = {}): string {
  const offset = (config.offset as number) || 0
  const now = new Date()
  // Day 0 of next month = last day of target month
  const date = new Date(now.getFullYear(), now.getMonth() + offset + 1, 0)
  return formatDate(date)
}

/**
 * Returns the first day of a year
 *
 * Usage: { "$gen": "startOfYear" }
 * Usage: { "$gen": "startOfYear", "offset": -1 }  // last year
 * Returns: "2025-01-01"
 *
 * @param config.offset - Year offset from current (0 = current, -1 = last year)
 */
export function startOfYear(config: GeneratorConfig = {}): string {
  const offset = (config.offset as number) || 0
  const year = new Date().getFullYear() + offset
  return `${year}-01-01`
}

/**
 * Returns the last day of a year
 *
 * Usage: { "$gen": "endOfYear" }
 * Usage: { "$gen": "endOfYear", "offset": -1 }  // last year
 * Returns: "2025-12-31"
 *
 * @param config.offset - Year offset from current (0 = current, -1 = last year)
 */
export function endOfYear(config: GeneratorConfig = {}): string {
  const offset = (config.offset as number) || 0
  const year = new Date().getFullYear() + offset
  return `${year}-12-31`
}

/**
 * Generates common date range preset options
 *
 * Usage: { "$gen": "datePresets" }
 * Returns: [{ value: "last-7-days", label: "Last 7 Days" }, ...]
 */
export function datePresets(): RecipeParameterOption[] {
  return [
    { value: 'today', label: 'Today' },
    { value: 'yesterday', label: 'Yesterday' },
    { value: 'last-7-days', label: 'Last 7 Days' },
    { value: 'last-30-days', label: 'Last 30 Days' },
    { value: 'this-month', label: 'This Month' },
    { value: 'last-month', label: 'Last Month' },
    { value: 'this-quarter', label: 'This Quarter' },
    { value: 'this-year', label: 'This Year' },
    { value: 'last-year', label: 'Last Year' },
  ]
}

/**
 * Returns the configured default currency
 *
 * Usage: { "$gen": "defaultCurrency" }
 * Returns: "USD" (or whatever is configured in accounts.default_currency)
 */
export function defaultCurrency(): string {
  const { config } = useConfig()
  return config.value?.accounts?.default_currency || 'USD'
}

/**
 * Generates currency options from an explicit list
 *
 * Usage: { "$gen": "currencyOptions", "currencies": ["USD", "INR", "EUR"] }
 * Returns: [{ value: "USD", label: "USD" }, { value: "INR", label: "INR" }, ...]
 */
export function currencyOptions(config: GeneratorConfig = {}): RecipeParameterOption[] {
  const currencies = (config.currencies as string[]) || ['USD']
  return currencies.map((code) => ({ value: code, label: code }))
}

/**
 * Registry of all available generators
 */
export const generators: Record<string, GeneratorFunction> = {
  // Value generators
  currentYear,
  defaultCurrency,

  // Option generators
  yearRange,
  monthOptions,
  quarterOptions,
  accountTypeOptions,
  datePresets,
  currencyOptions,

  // Date generators
  today,
  startOfMonth,
  endOfMonth,
  startOfYear,
  endOfYear,
}
