// `defaultCurrency` opens dashboards in a currency the active ledger uses, so a
// ledger in other currencies than the one chosen in Setup (e.g. the USD/INR demo
// for a EUR user) doesn't open on empty charts.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const m = vi.hoisted(() => ({
  configured: 'EUR' as string | undefined,
  fetched: true,
  commodities: [] as unknown[],
  fetchCommodities: vi.fn(async () => {}),
}))
vi.mock('@/composables/useConfig', async () => {
  const { computed } = await import('vue')
  return { useConfig: () => ({ config: computed(() => ({ accounts: { default_currency: m.configured } })) }) }
})
vi.mock('@/composables/useCommodities', async () => {
  const { computed } = await import('vue')
  return {
    useCommodities: () => ({
      commodityDetails: computed(() => m.commodities),
      hasBeenFetched: computed(() => m.fetched),
      fetchCommodities: m.fetchCommodities,
    }),
  }
})

import { defaultCurrency, pickDefaultCurrency, type CurrencyUsage } from '@/recipes/functions/generators'

const cur = (code: string, transaction_count: number, is_currency = true): CurrencyUsage =>
  ({ code, is_currency, usage: { transaction_count } })

describe('pickDefaultCurrency', () => {
  it('keeps the configured currency when the ledger uses it', () => {
    expect(pickDefaultCurrency('INR', [cur('USD', 900), cur('INR', 40)])).toBe('INR')
  })

  it("falls back to the ledger's most-used currency when it doesn't", () => {
    expect(pickDefaultCurrency('EUR', [cur('INR', 40), cur('USD', 900)])).toBe('USD')
  })

  it('ignores holdings and currencies with no transactions; ties go alphabetically', () => {
    expect(pickDefaultCurrency('EUR', [cur('VOO', 5000, false), cur('EUR', 0), cur('GBP', 3), cur('CHF', 3)])).toBe('CHF')
  })

  it('keeps the configured currency for an empty ledger', () => {
    expect(pickDefaultCurrency('EUR', [])).toBe('EUR')
    expect(pickDefaultCurrency('EUR', [cur('USD', 0)])).toBe('EUR')
  })
})

describe('defaultCurrency generator', () => {
  beforeEach(() => {
    m.configured = 'EUR'
    m.fetched = true
    m.commodities = [cur('USD', 900), cur('INR', 40)]
    m.fetchCommodities.mockClear()
  })

  it("uses the ledger's currency once the commodities are loaded", () => {
    expect(defaultCurrency()).toBe('USD')
    expect(m.fetchCommodities).not.toHaveBeenCalled()
  })

  it('returns the configured currency and loads the commodities when they are not loaded', async () => {
    m.fetched = false                                  // e.g. right after a ledger switch
    expect(defaultCurrency()).toBe('EUR')
    await Promise.resolve()
    expect(m.fetchCommodities).toHaveBeenCalledTimes(1)
  })

  it('falls back to USD when no currency is configured and the ledger uses it', () => {
    m.configured = undefined
    expect(defaultCurrency()).toBe('USD')
  })
})
