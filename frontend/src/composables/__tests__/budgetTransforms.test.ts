/**
 * Budget transforms — joinBudgetActual (flat + remainder), runningSum,
 * envelopeRollover (§7.3 / dev-docs/budget.md §10.1).
 *
 * Money fields are decimal strings (money-types.md); assertions use exact
 * string/decimal equality, not float-approx.
 */

import { describe, it, expect } from 'vitest'
import { applyTransform } from '@/composables/useRecipeTransforms'

const budgets = (rows: object[]) => rows
const actuals = (rows: object[]) => rows

// ── joinBudgetActual: flat mode ──────────────────────────────────────────────

describe('joinBudgetActual (flat)', () => {
  it('joins budget and actual on (account, currency) with variance fields', () => {
    const out = applyTransform('joinBudgetActual', [
      budgets([{ account: 'Expenses:Food', currency: 'USD', budget: '600' }]),
      actuals([{ account: 'Expenses:Food', currency: 'USD', actual: 312 }]),
    ]) as Record<string, unknown>[]
    expect(out).toHaveLength(1)
    const r = out[0]
    expect(r.budget).toBe('600')
    expect(r.actual).toBe('312')
    expect(r.remaining).toBe('288')
    expect(r.pctUsed).toBeCloseTo(0.52, 5)
    expect(r.direction).toBe('under-good')
  })

  it('rolls actuals up inclusively to a parent budget (§4.1)', () => {
    // Expenses:Insurance is an aggregation node (no direct postings); its
    // children carry the spend and must roll up into the parent's actual.
    const out = applyTransform('joinBudgetActual', [
      budgets([{ account: 'Expenses:Insurance', currency: 'USD', budget: '500' }]),
      actuals([
        { account: 'Expenses:Insurance:Health', currency: 'USD', actual: 450 },
        { account: 'Expenses:Insurance:Dental', currency: 'USD', actual: 30 },
        { account: 'Expenses:Food', currency: 'USD', actual: 999 }, // unrelated, excluded
      ]),
    ]) as Record<string, unknown>[]
    expect(out[0].actual).toBe('480') // 450 + 30, Food excluded
    expect(out[0].remaining).toBe('20')
  })

  it('income accounts are over-good; missing actual is zero', () => {
    const out = applyTransform('joinBudgetActual', [
      budgets([{ account: 'Income:Salary', currency: 'USD', budget: '5000' }]),
      actuals([]),
    ]) as Record<string, unknown>[]
    expect(out[0].direction).toBe('over-good')
    expect(out[0].actual).toBe('0')
    expect(out[0].remaining).toBe('5000')
  })

  it('emits linear pace when period bounds + asOf are given (A3)', () => {
    const out = applyTransform(
      'joinBudgetActual',
      [
        budgets([{ account: 'Expenses:Food', currency: 'USD', budget: '600' }]),
        actuals([{ account: 'Expenses:Food', currency: 'USD', actual: 100 }]),
      ],
      // 15 days into a 30-day window → 50% expected → pace 300.
      { periodStart: '2026-06-01', periodEnd: '2026-07-01', asOf: '2026-06-16' },
    ) as Record<string, unknown>[]
    expect(Number(out[0].pace)).toBeCloseTo(300, 0)
  })
})

// ── joinBudgetActual: remainder mode (§13) ───────────────────────────────────

describe('joinBudgetActual (remainder mode)', () => {
  const find = (rows: Record<string, unknown>[], account: string) =>
    rows.find((r) => r.account === account) as Record<string, unknown>

  it('emits flat named + Unbudgeted + Total rows via the maximal-named-subtree rule', () => {
    const out = applyTransform(
      'joinBudgetActual',
      [
        budgets([
          { account: 'Expenses:Home', currency: 'USD', budget: '5000' },
          { account: 'Expenses:Home:Rent', currency: 'USD', budget: '1500' },
          { account: 'Expenses:Home:Food', currency: 'USD', budget: '600' },
        ]),
        actuals([
          { account: 'Expenses:Home:Rent', currency: 'USD', actual: 1500 },
          { account: 'Expenses:Home:Food', currency: 'USD', actual: 500 },
          { account: 'Expenses:Home:Misc', currency: 'USD', actual: 800 },
        ]),
      ],
      { totalAccount: 'Expenses:Home' },
    ) as Record<string, unknown>[]

    const total = find(out, 'Total')
    const unbudgeted = find(out, 'Unbudgeted')
    expect(total.budget).toBe('5000')
    expect(total.actual).toBe('2800') // 1500 + 500 + 800
    // remainder budget = 5000 − (1500+600) = 2900; remainder actual = 2800 − (1500+500) = 800
    expect(unbudgeted.budget).toBe('2900')
    expect(unbudgeted.actual).toBe('800')
    expect(unbudgeted.overAllocated).toBe(false)
    // named rows are present (Rent, Food)
    expect(out.filter((r) => r.kind === 'named')).toHaveLength(2)
  })

  it('nested named budgets do not double-count (maximal subtree only)', () => {
    const out = applyTransform(
      'joinBudgetActual',
      [
        budgets([
          { account: 'Expenses:Home', currency: 'USD', budget: '1000' },
          { account: 'Expenses:Home:Food', currency: 'USD', budget: '400' },
          { account: 'Expenses:Home:Food:Restaurants', currency: 'USD', budget: '150' },
        ]),
        actuals([
          { account: 'Expenses:Home:Food', currency: 'USD', actual: 200 },
          { account: 'Expenses:Home:Food:Restaurants', currency: 'USD', actual: 120 },
        ]),
      ],
      { totalAccount: 'Expenses:Home' },
    ) as Record<string, unknown>[]

    // maximal named = {Expenses:Home:Food} (Restaurants is under Food).
    // named budget = 400; named actual = 200 + 120 = 320 (counted once via subtree).
    expect(find(out, 'Total').actual).toBe('320')
    expect(find(out, 'Unbudgeted').budget).toBe('600') // 1000 − 400
    expect(find(out, 'Unbudgeted').actual).toBe('0') // 320 − 320
  })

  it('flags over-allocation (named budgets exceed the total)', () => {
    const out = applyTransform(
      'joinBudgetActual',
      [
        budgets([
          { account: 'Expenses:Home', currency: 'USD', budget: '1000' },
          { account: 'Expenses:Home:Rent', currency: 'USD', budget: '1200' },
        ]),
        actuals([]),
      ],
      { totalAccount: 'Expenses:Home' },
    ) as Record<string, unknown>[]
    const unbudgeted = find(out, 'Unbudgeted')
    expect(unbudgeted.overAllocated).toBe(true)
    expect(unbudgeted.budget).toBe('-200')
    expect(unbudgeted.note).toMatch(/over-allocated/i)
  })

  it('reports noTotalBudget when the total node has no budget', () => {
    const out = applyTransform(
      'joinBudgetActual',
      [
        budgets([{ account: 'Expenses:Home:Rent', currency: 'USD', budget: '1200' }]),
        actuals([{ account: 'Expenses:Home:Rent', currency: 'USD', actual: 1200 }]),
      ],
      { totalAccount: 'Expenses:Home' },
    ) as Record<string, unknown>[]
    const total = find(out, 'Total')
    expect(total.noTotalBudget).toBe(true)
    expect(total.budget).toBeNull()
    expect(total.note).toMatch(/no total budget/i) // surfaced, not silent
    expect(find(out, 'Unbudgeted').budget).toBeNull()
    expect(total.actual).toBe('1200') // actuals still work
  })
})

// ── runningSum ───────────────────────────────────────────────────────────────

describe('runningSum', () => {
  it('accumulates each field over rows ordered by orderBy', () => {
    const out = applyTransform(
      'runningSum',
      [[
        { period: '2026-02', budget: '100', actual: '40' },
        { period: '2026-01', budget: '100', actual: '60' },
        { period: '2026-03', budget: '100', actual: '50' },
      ]],
      { fields: ['budget', 'actual'], orderBy: 'period' },
    ) as Record<string, unknown>[]
    expect(out.map((r) => r.period)).toEqual(['2026-01', '2026-02', '2026-03'])
    expect(out.map((r) => r.cumulativeBudget)).toEqual(['100', '200', '300'])
    expect(out.map((r) => r.cumulativeActual)).toEqual(['60', '100', '150'])
  })
})

// ── envelopeRollover (§14) ───────────────────────────────────────────────────

describe('envelopeRollover', () => {
  it('carries unspent forward; carryover = cumulative budget − cumulative actual', () => {
    const out = applyTransform('envelopeRollover', [
      [
        { period: '2026-01', budget: '100' },
        { period: '2026-02', budget: '100' },
        { period: '2026-03', budget: '100' },
      ],
      [
        { period: '2026-01', actual: '60' }, // under by 40
        { period: '2026-02', actual: '90' }, // available 140, spend 90 → carry 50
        { period: '2026-03', actual: '50' }, // available 150, spend 50 → carry 100
      ],
    ]) as Record<string, unknown>[]
    expect(out.map((r) => r.available)).toEqual(['100', '140', '150'])
    expect(out.map((r) => r.carryover)).toEqual(['40', '50', '100'])
    expect(out.every((r) => r.overspent === false)).toBe(true)
  })

  it('negative carryover carries forward (true envelope, no clamp)', () => {
    const out = applyTransform('envelopeRollover', [
      [
        { period: '2026-01', budget: '100' },
        { period: '2026-02', budget: '100' },
      ],
      [
        { period: '2026-01', actual: '130' }, // over by 30 → carry -30
        { period: '2026-02', actual: '50' }, // available 70, spend 50 → carry 20
      ],
    ]) as Record<string, unknown>[]
    expect(out[0].carryover).toBe('-30')
    expect(out[0].overspent).toBe(true)
    expect(out[1].available).toBe('70') // 100 + (-30)
    expect(out[1].carryover).toBe('20')
  })

  it('carries currency through from the budgets input (for a latest-row KPI)', () => {
    const out = applyTransform('envelopeRollover', [
      [
        { period: '2026-01', currency: 'INR', budget: '100' },
        { period: '2026-02', currency: 'INR', budget: '100' },
      ],
      [{ period: '2026-01', actual: '60' }],
    ]) as Record<string, unknown>[]
    expect(out.every((r) => r.currency === 'INR')).toBe(true)
  })

  it('emits month bounds per period (for per-point chart click-through)', () => {
    const out = applyTransform('envelopeRollover', [
      [{ period: '2026-02', currency: 'USD', budget: '100' }],
      [{ period: '2026-02', actual: '60' }],
    ]) as Record<string, unknown>[]
    expect(out[0].dateFrom).toBe('2026-02-01')
    expect(out[0].dateTo).toBe('2026-02-28') // last day of Feb 2026
  })
})
