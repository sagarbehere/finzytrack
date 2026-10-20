// Dashboard actions are declared by the recipe, never inferred from its steps.
// See dev-docs/dashboard-actions.md.

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/services/generated-api', () => ({
  PricesService: { updatePrices: vi.fn() },
}))

import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import type { JsonDashboardRecipe } from '@/types/recipes'

function dashboard(over: Partial<JsonDashboardRecipe>, step: Record<string, unknown>): JsonDashboardRecipe {
  return {
    schemaVersion: 2,
    id: 'd',
    title: 'D',
    layout: { columns: 12, widgets: [{ widgetId: 'w', gridArea: '1 / 1 / 2 / 2' }] },
    widgets: [{
      id: 'w', title: 'W', steps: [step], output: 's', visualization: { type: 'kpi' },
    }],
    ...over,
  } as unknown as JsonDashboardRecipe
}

const valuation = { id: 's', kind: 'compute', fn: 'portfolio_series' }
const sqlOnly = { id: 's', kind: 'query', query: 'SELECT * FROM prices' }

function hasButton(d: JsonDashboardRecipe): boolean {
  const wrapper = mount(RecipeDashboard, {
    props: { dashboard: d },
    global: { stubs: { RecipeWidget: true, RecipeParameters: true, MetadataHintBanner: true } },
  })
  return wrapper.findAll('button').some(b => b.text() === 'Update prices')
}

describe('RecipeDashboard actions', () => {
  it('shows Update prices only when the recipe declares it', () => {
    expect(hasButton(dashboard({ actions: ['update-prices'] }, valuation))).toBe(true)
    expect(hasButton(dashboard({ actions: ['update-prices'] }, sqlOnly))).toBe(true)
  })

  it('does not infer it from a valuation step', () => {
    expect(hasButton(dashboard({}, valuation))).toBe(false)
    expect(hasButton(dashboard({ actions: [] }, valuation))).toBe(false)
  })
})
