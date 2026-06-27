/**
 * Migration correctness + validity of the migrated seed dashboards (§7.2/§7.6).
 *
 * The seed recipes are the dogfood corpus for the v1→v2 migration. After
 * `scripts/migrate_recipes.py` runs over them, every committed seed dashboard
 * must:
 *   - be stamped schemaVersion 2,
 *   - validate cleanly under the new steps/DAG validator,
 *   - carry no legacy `query`/`transform` widget fields,
 *   - have an `output` that names one of its widget's steps.
 *
 * If this fails, the app cannot render the seed dashboards.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { validateJsonDashboardRecipe } from '@/composables/useRecipeValidator'

const SEED_DASHBOARDS_DIR = resolve(
  process.cwd(),
  '..',
  'backend/resources/seed_config/recipes/dashboards',
)

function seedDashboards(): { name: string; recipe: Record<string, unknown> }[] {
  return readdirSync(SEED_DASHBOARDS_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => ({
      name: f,
      recipe: JSON.parse(readFileSync(resolve(SEED_DASHBOARDS_DIR, f), 'utf-8')),
    }))
}

describe('migrated seed dashboards', () => {
  const dashboards = seedDashboards()

  it('finds the seed dashboards', () => {
    expect(dashboards.length).toBeGreaterThan(0)
  })

  for (const { name, recipe } of seedDashboards()) {
    describe(name, () => {
      it('is stamped schemaVersion 2', () => {
        expect(recipe.schemaVersion).toBe(2)
      })

      it('validates cleanly under the steps/DAG validator', () => {
        expect(validateJsonDashboardRecipe(recipe)).toEqual([])
      })

      it('has no legacy query/transform widget fields, and output names a real step', () => {
        const widgets = recipe.widgets as Record<string, unknown>[]
        for (const w of widgets) {
          expect(w.query, `${name}/${w.id} still has legacy query`).toBeUndefined()
          expect(w.transform, `${name}/${w.id} still has legacy transform`).toBeUndefined()
          const steps = w.steps as { id: string }[]
          const stepIds = steps.map((s) => s.id)
          expect(stepIds, `${name}/${w.id} output names a real step`).toContain(w.output)
        }
      })
    })
  }
})
