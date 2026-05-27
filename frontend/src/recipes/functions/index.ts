/**
 * Recipe Functions Registry
 *
 * Central registry for all functions that can be invoked from JSON recipes.
 * This provides a "standard library" of TypeScript functions that JSON recipes
 * can reference by name.
 *
 * Categories:
 * - generators: Create dynamic values (yearRange, currentYear, etc.)
 * - transforms: Process query results (firstRow, firstValue, etc.) - defined in useRecipeExecutor
 * - formats: Format values for display (currency, percent, etc.) - defined in useRecipeExecutor
 */

import { generators, type GeneratorConfig } from './generators'

export { generators } from './generators'
export type { GeneratorConfig, GeneratorFunction } from './generators'

/**
 * Human-readable labels for scalar generators when they are surfaced as
 * "templated" dropdown options. Only generators that produce a scalar value
 * suitable as a parameter default appear here — option-generators
 * (yearRange, monthOptions, etc.) are not templatable.
 */
export const GENERATOR_LABELS: Record<string, string> = {
  currentYear: 'Current Year',
  currentMonth: 'Current Month',
  defaultCurrency: 'Default Currency',
  today: 'Today',
  startOfMonth: 'Start of Month',
  endOfMonth: 'End of Month',
  startOfYear: 'Start of Year',
  endOfYear: 'End of Year',
}

/**
 * Sentinel prefix used to encode a "this selection is a templated generator
 * reference" inside the scalar parameter-value space (string | number).
 * Example: a user choosing "Current Month" stores the literal string
 * "$gen:currentMonth", which is re-resolved each time the dashboard loads.
 *
 * Only no-arg generator references are encoded this way — generators with
 * config args are resolved eagerly at recipe load (see resolveRecipeGenerators).
 */
export const GEN_SENTINEL_PREFIX = '$gen:'

export function isGenSelection(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith(GEN_SENTINEL_PREFIX)
}

export function makeGenSelection(name: string): string {
  return GEN_SENTINEL_PREFIX + name
}

export function genSelectionName(value: string): string {
  return value.slice(GEN_SENTINEL_PREFIX.length)
}

/**
 * Resolve a parameter value to its concrete scalar. If the value is a
 * generator sentinel ("$gen:name"), the generator is invoked now. Otherwise
 * the value passes through.
 */
export function resolveParameterValue(value: string | number): string | number {
  if (!isGenSelection(value)) return value
  const name = genSelectionName(value)
  const fn = generators[name]
  if (!fn) {
    console.warn(`[recipes] Unknown generator in selection "${value}" — passing through as literal`)
    return value
  }
  return fn({}) as string | number
}

/**
 * Resolve every value in a parameter map to its concrete scalar.
 */
export function resolveParameterValues(
  params: Record<string, string | number>,
): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(params)) {
    out[k] = resolveParameterValue(v)
  }
  return out
}

/**
 * Check if a value is a generator reference
 * Generator references are objects with a "$gen" key
 */
export function isGeneratorRef(value: unknown): value is { $gen: string; [key: string]: unknown } {
  return (
    typeof value === 'object' &&
    value !== null &&
    '$gen' in value &&
    typeof (value as Record<string, unknown>).$gen === 'string'
  )
}

/**
 * Resolve a generator reference to its computed value
 *
 * @param ref - The generator reference object { "$gen": "name", ...config }
 * @returns The computed value from the generator function
 * @throws Error if generator is not found
 */
export function resolveGenerator(ref: { $gen: string; [key: string]: unknown }): unknown {
  const { $gen: name, ...config } = ref

  const generator = generators[name]
  if (!generator) {
    throw new Error(`Unknown generator: "${name}". Available generators: ${Object.keys(generators).join(', ')}`)
  }

  return generator(config as GeneratorConfig)
}

/**
 * Recipe-aware generator resolution.
 *
 * Walks a widget or dashboard recipe and resolves $gen references everywhere
 * EXCEPT inside `parameters[].default`. For parameter defaults:
 *   - A no-arg `{ "$gen": "name" }` becomes a sentinel string (e.g. "$gen:currentMonth")
 *     so that the UI can offer "Current Month" as a sticky, re-evaluated option.
 *   - A `{ "$gen": "name", ...args }` with config args is resolved eagerly
 *     (back-compat — it cannot be expressed as a sentinel).
 *
 * Dashboard recipes may embed widgets inline; those are walked recursively.
 */
export function resolveRecipeGenerators<T>(recipe: T): T {
  if (recipe === null || recipe === undefined || typeof recipe !== 'object') {
    return recipe
  }

  if (Array.isArray(recipe)) {
    return recipe.map((item) => resolveRecipeGenerators(item)) as T
  }

  const src = recipe as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(src)) {
    if (key === 'parameters' && Array.isArray(value)) {
      out[key] = value.map((p) => resolveParameterForLoading(p))
    } else if (key === 'widgets' && Array.isArray(value)) {
      // Recurse so inline widgets get parameter-aware treatment too.
      out[key] = value.map((w) => resolveRecipeGenerators(w))
    } else {
      out[key] = resolveGenerators(value)
    }
  }
  return out as T
}

function resolveParameterForLoading(param: unknown): unknown {
  if (!param || typeof param !== 'object') return param
  const p = param as Record<string, unknown>
  const out: Record<string, unknown> = { ...p }
  if (isGeneratorRef(p.default)) {
    const { $gen, ...args } = p.default
    if (Object.keys(args).length === 0) {
      out.default = makeGenSelection($gen)
    } else {
      out.default = resolveGenerator(p.default)
    }
  }
  // `options` is allowed to be a generator-ref or literal array; resolve normally.
  if (p.options !== undefined) {
    out.options = resolveGenerators(p.options)
  }
  return out
}

/**
 * Recursively resolve all generator references in an object
 *
 * This walks through the entire object tree and replaces any
 * { "$gen": "name", ...args } objects with their computed values.
 *
 * Prefer `resolveRecipeGenerators` when processing a whole recipe — that
 * variant preserves no-arg parameter defaults as templated sentinels so the
 * UI can offer them as sticky dropdown options. Use this raw walker only for
 * sub-trees where templated defaults are not relevant.
 *
 * @param obj - The object to process
 * @returns A new object with all generators resolved
 */
export function resolveGenerators<T>(obj: T): T {
  if (obj === null || obj === undefined) {
    return obj
  }

  // Check if this is a generator reference
  if (isGeneratorRef(obj)) {
    return resolveGenerator(obj) as T
  }

  // Handle arrays
  if (Array.isArray(obj)) {
    return obj.map((item) => resolveGenerators(item)) as T
  }

  // Handle plain objects
  if (typeof obj === 'object') {
    const result: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(obj)) {
      result[key] = resolveGenerators(value)
    }
    return result as T
  }

  // Primitives pass through unchanged
  return obj
}
