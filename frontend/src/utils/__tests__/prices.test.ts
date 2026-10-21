// Prices tab helpers: display, input checks, and the PUT /api/prices batches.
// See dev-docs/investment-view.md §10.2.
import { describe, it, expect } from 'vitest'
import type { PriceOverviewRow, PricePoint } from '@/services/generated-api'
import {
  addRequest,
  cadenceLabel,
  editRequest,
  formatAge,
  formatPrice,
  formatShortDate,
  isCommodityCode,
  priceFreshness,
  removeRequest,
  validatePriceInput,
  withReplace,
} from '@/utils/prices'

const point = (date: string, number: string): PricePoint =>
  ({ date, number, quote_currency: 'USD', source: 'sidecar' }) as PricePoint

describe('formatPrice', () => {
  it('keeps the written decimals, at least two', () => {
    expect(formatPrice('400', 'USD')).toBe('400.00 USD')
    expect(formatPrice('1234.5678', 'USD')).toBe('1,234.5678 USD')
    expect(formatPrice('0.000123', 'BTC')).toBe('0.000123 BTC')
    expect(formatPrice('1.2500', 'USD')).toBe('1.2500 USD')
  })
  it('omits the currency when there is none', () => {
    expect(formatPrice('12.5', null)).toBe('12.50')
  })
})

describe('dates', () => {
  it('drops the year in the current year', () => {
    expect(formatShortDate('2026-10-14', '2026-10-20')).toBe('Oct 14')
    expect(formatShortDate('2024-03-01', '2026-10-20')).toBe('Mar 1, 2024')
  })
  it('describes the age', () => {
    expect([-3, 0, 1, 6, 59, 60, 400, 800].map(formatAge)).toEqual([
      'in the future', 'today', 'yesterday', '6 days ago', '59 days ago', '2 months ago', '13 months ago', '2 years ago',
    ])
  })
  it('labels cadences', () => {
    expect(['daily', 'monthly', 'computed', 'never'].map(c => cadenceLabel(c as never))).toEqual([
      'Daily', 'Monthly', 'Computed', 'Not fetched',
    ])
  })
})

describe('validatePriceInput', () => {
  const ok = { date: '2026-10-20', commodity: 'VOO', quote: 'USD', number: '400.10' }
  it('accepts a valid price, including codes with . - _', () => {
    expect(validatePriceInput(ok)).toEqual({})
    expect(validatePriceInput({ ...ok, commodity: 'BRK.B', number: '1234.5' })).toEqual({})
    expect(isCommodityCode('ELEC-DAYS')).toBe(true)
  })
  it('rejects a date after today (the user\'s local date)', () => {
    expect(validatePriceInput({ ...ok, date: '2026-10-21' }, '2026-10-20').date).toBe("A price can't be dated after today.")
    expect(validatePriceInput({ ...ok, date: '2026-10-20' }, '2026-10-20')).toEqual({})
  })
  it('rejects bad fields', () => {
    expect(validatePriceInput({ date: '', commodity: 'voo', quote: '', number: '0' })).toEqual({
      date: 'Enter a date.',
      commodity: 'Use an uppercase code, e.g. VOO or BRK.B.',
      quote: 'Use an uppercase code, e.g. USD.',
      number: 'Enter a price above zero.',
    })
    for (const number of ['-1', 'abc', '1e3', 'Infinity', '']) {
      expect(validatePriceInput({ ...ok, number }).number).toBe('Enter a price above zero.')
    }
  })
  it('refuses commas rather than guessing (12,5 must not become 125)', () => {
    for (const number of ['12,5', '1,234.50']) {
      expect(validatePriceInput({ ...ok, number }).number).toBe('Use a dot for decimals, without commas, e.g. 1234.50.')
    }
  })
})

describe('batches', () => {
  it('adds without replacing a taken date; withReplace allows it', () => {
    const req = addRequest({ date: '2026-10-20', commodity: ' VOO ', quote: 'USD', number: '1234.50' })
    expect(req).toEqual({
      set: [{ date: '2026-10-20', commodity: 'VOO', quote_currency: 'USD', number: '1234.50', replace: false }],
    })
    expect(withReplace(req).set?.[0].replace).toBe(true)
  })
  it('edits the number in place', () => {
    expect(editRequest(point('2026-10-14', '400.00'), 'VOO', { date: '2026-10-14', number: '401' })).toEqual({
      set: [{ date: '2026-10-14', commodity: 'VOO', quote_currency: 'USD', number: '401' }],
    })
  })
  it('moves a price to a new date as remove + set', () => {
    expect(editRequest(point('2026-10-14', '400.00'), 'VOO', { date: '2026-10-15', number: '400.00' })).toEqual({
      set: [{ date: '2026-10-15', commodity: 'VOO', quote_currency: 'USD', number: '400.00', replace: false }],
      remove: [{ date: '2026-10-14', commodity: 'VOO', quote_currency: 'USD' }],
    })
  })
  it('returns null when nothing changed (same value, other spelling)', () => {
    expect(editRequest(point('2026-10-14', '400.00'), 'VOO', { date: '2026-10-14', number: '400' })).toBeNull()
  })
  it('removes by key', () => {
    expect(removeRequest(point('2026-10-14', '400.00'), 'VOO')).toEqual({
      remove: [{ date: '2026-10-14', commodity: 'VOO', quote_currency: 'USD' }],
    })
  })
})


function overviewRow(over: Record<string, unknown>): PriceOverviewRow {
  return {
    commodity: 'VOO', quote_currency: 'USD', cadence: 'daily', held: true,
    latest: null, count: 0, out_of_date: false, not_fetched_reason: null, ...over,
  } as unknown as PriceOverviewRow
}
const latest = (date: string) => ({ date, number: '1', source: 'sidecar' }) as PriceOverviewRow['latest']

describe('priceFreshness', () => {
  it('counts only held holdings the fetcher prices', () => {
    const f = priceFreshness([
      overviewRow({ commodity: 'VOO', latest: latest('2026-10-15') }),
      overviewRow({ commodity: 'BND', cadence: 'monthly', latest: latest('2026-09-30'), out_of_date: true }),
      overviewRow({ commodity: 'OLD', held: false, latest: latest('2026-10-20') }),               // sold out
      overviewRow({ commodity: 'VMFXX', cadence: 'never', not_fetched_reason: 'Never fetched (money market)' }),
      overviewRow({ commodity: 'IB', cadence: 'computed', not_fetched_reason: 'Needs an issue month' }),
    ])
    expect(f).toEqual({ priceable: 2, asOf: '2026-10-15', outOfDate: 1 })
  })

  it('has nothing to price for a ledger with no holdings', () => {
    expect(priceFreshness([])).toEqual({ priceable: 0, asOf: null, outOfDate: 0 })
  })

  it('reports no date while held holdings have no price yet', () => {
    expect(priceFreshness([overviewRow({ out_of_date: true })])).toEqual({ priceable: 1, asOf: null, outOfDate: 1 })
  })
})
