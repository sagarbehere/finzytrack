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
