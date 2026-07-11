/**
 * Core catalog transforms whose behavior matters for money dashboards:
 *   - `where` (row-predicate filter) — added for KPI slicing of multi-row
 *     results (e.g. joinBudgetActual remainder mode).
 *   - `sortBy` numeric ordering over decimal-STRING Money fields.
 */

import { describe, it, expect } from 'vitest'
import { applyTransform } from '@/composables/useRecipeTransforms'

const rows = [
  { account: 'Expenses:Rent', remaining: '-600.00', kind: 'named' },
  { account: 'Expenses:Food', remaining: '50.00', kind: 'named' },
  { account: 'Unbudgeted', remaining: '120.00', kind: 'unbudgeted' },
  { account: 'Total', remaining: '1200.00', kind: 'total' },
]

describe('where transform', () => {
  it('equals: keeps only matching rows', () => {
    const out = applyTransform('where', [rows], { field: 'kind', equals: 'total' }) as Record<string, unknown>[]
    expect(out).toHaveLength(1)
    expect(out[0].account).toBe('Total')
  })

  it('notEquals: excludes matching rows', () => {
    const out = applyTransform('where', [rows], { field: 'kind', notEquals: 'total' }) as unknown[]
    expect(out).toHaveLength(3)
  })

  it('in: keeps rows whose value is in the list', () => {
    const out = applyTransform('where', [rows], { field: 'kind', in: ['unbudgeted', 'total'] }) as Record<string, unknown>[]
    expect(out.map((r) => r.kind).sort()).toEqual(['total', 'unbudgeted'])
  })

  it('no field / no predicate: passthrough', () => {
    expect(applyTransform('where', [rows], {}) as unknown[]).toHaveLength(4)
  })
})

describe('sortBy over decimal-string Money fields', () => {
  it('orders numerically, not lexicographically (asc)', () => {
    const out = applyTransform('sortBy', [rows], { field: 'remaining', order: 'asc' }) as Record<string, unknown>[]
    expect(out.map((r) => r.remaining)).toEqual(['-600.00', '50.00', '120.00', '1200.00'])
  })

  it('orders numerically (desc) — most over budget last', () => {
    const out = applyTransform('sortBy', [rows], { field: 'remaining', order: 'desc' }) as Record<string, unknown>[]
    expect(out.map((r) => r.remaining)).toEqual(['1200.00', '120.00', '50.00', '-600.00'])
  })

  it('falls back to locale order for non-numeric fields', () => {
    const out = applyTransform('sortBy', [rows], { field: 'account', order: 'asc' }) as Record<string, unknown>[]
    expect(out[0].account).toBe('Expenses:Food')
    expect(out[out.length - 1].account).toBe('Unbudgeted')
  })
})

describe('groupBy transform', () => {
  const periodRows = [
    { account: 'A', period: '2026-01', budget: '100' },
    { account: 'B', period: '2026-01', budget: '50.5' },
    { account: 'A', period: '2026-02', budget: '100' },
    { account: 'B', period: '2026-02', budget: '50.5' },
  ]

  it('sums a field per key, exactly, preserving first-seen order', () => {
    const out = applyTransform('groupBy', [periodRows], { key: 'period', sum: ['budget'] }) as Record<string, unknown>[]
    expect(out).toEqual([
      { period: '2026-01', budget: '150.5' },
      { period: '2026-02', budget: '150.5' },
    ])
  })

  it('supports a composite key and multiple sum fields', () => {
    const r = [
      { account: 'A', period: 'p1', budget: '10', actual: '4' },
      { account: 'A', period: 'p1', budget: '5', actual: '1' },
      { account: 'A', period: 'p2', budget: '7', actual: '3' },
    ]
    const out = applyTransform('groupBy', [r], { key: ['account', 'period'], sum: ['budget', 'actual'] }) as Record<string, unknown>[]
    expect(out).toEqual([
      { account: 'A', period: 'p1', budget: '15', actual: '5' },
      { account: 'A', period: 'p2', budget: '7', actual: '3' },
    ])
  })

  it('returns [] for empty input', () => {
    expect(applyTransform('groupBy', [[]], { key: 'period', sum: ['budget'] })).toEqual([])
  })
})

describe('appendTotal transform', () => {
  const r = [
    { account: 'A', actual: '10' },
    { account: 'B', actual: '5.25' },
    { account: 'C', actual: '4' },
  ]

  it('appends a total row summing all input (isTotal), keeping data rows', () => {
    const out = applyTransform('appendTotal', [r], { field: 'actual', label: 'Total' }) as Record<string, unknown>[]
    expect(out).toHaveLength(4)
    expect(out[3]).toEqual({ account: 'Total', actual: '19.25', isTotal: true })
  })

  it('totals ALL rows even when sliced to top count', () => {
    const out = applyTransform('appendTotal', [r], { field: 'actual', count: 2 }) as Record<string, unknown>[]
    expect(out.map((x) => x.account)).toEqual(['A', 'B', 'Total'])
    expect(out[2].actual).toBe('19.25') // full total, not just the shown two
  })
})
