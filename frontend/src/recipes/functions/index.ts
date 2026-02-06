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
 * Recursively resolve all generator references in an object
 *
 * This walks through the entire object tree and replaces any
 * { "$gen": "name", ...args } objects with their computed values.
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
