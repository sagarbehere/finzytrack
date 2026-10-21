// Dashboard actions are declared by the recipe, never inferred from its steps;
// Update prices also needs something held to price. See dev-docs/dashboard-actions.md.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const m = vi.hoisted(() => ({ rows: [] as unknown[] }))
vi.mock('@/services/generated-api', () => ({
  PricesService: {
    updatePrices: vi.fn(),
    getPriceOverview: vi.fn(async () => ({ data: { rows: m.rows, today: '2026-10-21' } })),
  },
}))

import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import { PricesService } from '@/services/generated-api'
import type { JsonDashboardRecipe } from '@/types/recipes'
import { useNotifications } from '@/composables/useNotifications'

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

const held = (over: Record<string, unknown> = {}) => ({
  commodity: 'VOO', quote_currency: 'USD', cadence: 'daily', held: true, count: 1,
  latest: { date: '2026-10-15', number: '1', source: 'sidecar' },
  out_of_date: false, not_fetched_reason: null, ...over,
})

async function render(d: JsonDashboardRecipe) {
  const wrapper = mount(RecipeDashboard, {
    props: { dashboard: d },
    global: { stubs: { RecipeWidget: true, RecipeParameters: true, MetadataHintBanner: true, RouterLink: { template: '<a><slot /></a>' } } },
  })
  await flushPromises()
  return wrapper
}

async function hasButton(d: JsonDashboardRecipe): Promise<boolean> {
  return (await render(d)).findAll('button').some(b => b.text() === 'Update prices')
}

beforeEach(() => {
  m.rows = [held()]
  vi.mocked(PricesService.getPriceOverview).mockClear()
})

describe('RecipeDashboard actions', () => {
  it('shows Update prices only when the recipe declares it', async () => {
    expect(await hasButton(dashboard({ actions: ['update-prices'] }, valuation))).toBe(true)
    expect(await hasButton(dashboard({ actions: ['update-prices'] }, sqlOnly))).toBe(true)
  })

  it('does not infer it from a valuation step', async () => {
    expect(await hasButton(dashboard({}, valuation))).toBe(false)
    expect(await hasButton(dashboard({ actions: [] }, valuation))).toBe(false)
    expect(PricesService.getPriceOverview).not.toHaveBeenCalled()
  })

  it('hides it when nothing held needs prices', async () => {
    m.rows = []
    expect(await hasButton(dashboard({ actions: ['update-prices'] }, sqlOnly))).toBe(false)
    m.rows = [held({ held: false }), held({ commodity: 'VMFXX', cadence: 'never', not_fetched_reason: 'Never fetched (money market)' })]
    expect(await hasButton(dashboard({ actions: ['update-prices'] }, sqlOnly))).toBe(false)
  })

  it('shows the date of the prices in use, and how many are out of date', async () => {
    m.rows = [held(), held({ commodity: 'BND', cadence: 'monthly', latest: { date: '2026-08-31', number: '1', source: 'sidecar' }, out_of_date: true })]
    const text = (await render(dashboard({ actions: ['update-prices'] }, sqlOnly))).find('[data-test="prices-as-of"]').text()
    expect(text.replace(/\s+/g, ' ')).toBe('Prices as of Oct 15 · 1 out of date')
  })

  it('says so when held holdings have no price yet', async () => {
    m.rows = [held({ latest: null, out_of_date: true })]
    const text = (await render(dashboard({ actions: ['update-prices'] }, sqlOnly))).find('[data-test="prices-as-of"]').text()
    expect(text.replace(/\s+/g, ' ')).toBe('No prices yet · 1 out of date')
  })
  it('says prices are up to date when no holding needed a new one', async () => {
    vi.mocked(PricesService.updatePrices).mockResolvedValueOnce({
      success: true,
      data: { added: 0, updated: 0, total: 3, as_of: '2026-10-15', symbols: [], skipped: [], failed: [] },
    } as never)
    const wrapper = await render(dashboard({ actions: ['update-prices'] }, sqlOnly))
    await wrapper.findAll('button').find(b => b.text() === 'Update prices')!.trigger('click')
    await flushPromises()
    const n = useNotifications().allNotifications.value[0]
    expect([n.type, n.title, n.message]).toEqual(['success', 'Prices up to date', 'No holding needed a new price.'])
  })
})
