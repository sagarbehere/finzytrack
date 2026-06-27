/**
 * Golden baseline — current recipe transform output (Phase 0).
 *
 * Captured BEFORE the dashboard-recipe DAG refactor
 * (dev-docs/refactored-dashboard-recipes.md §7.0). The committed snapshot is
 * the equivalence guard for the refactor: Phase 1 rewrites the transform
 * catalog to a `(inputs[], config, ctx)` signature, and the migrated seed
 * recipes must render IDENTICAL data through the new DAG executor (§7.2/§7.3).
 *
 * Do NOT regenerate this snapshot to "make it pass" after the refactor — a
 * diff here means the migration or the new catalog changed observable output,
 * which is exactly what this test exists to catch.
 */

import { describe, it, expect } from 'vitest'
import { applyTransform } from '@/composables/useRecipeTransforms'
import type { TransformConfig } from '@/types/recipes'
import { GOLDEN_CASES } from './recipeGolden.fixtures'

/**
 * The committed snapshot was captured from the pre-refactor pipeline (Phase 0).
 * These assertions now run the migrated form — fn + config through the new
 * `(inputs[], config, ctx)` catalog — and must reproduce that snapshot exactly.
 * A diff means the new catalog changed observable output (§7.3).
 */
describe('recipe transform golden baseline (pre-DAG-refactor)', () => {
  for (const c of GOLDEN_CASES) {
    it(`${c.name} (from ${c.source}) renders the same data`, () => {
      // Migration mapping: string transform → fn with no config;
      // object transform → fn = config.type, config = the object (the new
      // catalog ignores the residual `type` key).
      const isConfig = typeof c.transform === 'object'
      const fn = isConfig ? (c.transform as TransformConfig).type! : (c.transform as string)
      const config = isConfig ? (c.transform as TransformConfig) : undefined

      const output = applyTransform(fn, [c.rows], config)
      expect(output).toMatchSnapshot()
    })
  }
})
