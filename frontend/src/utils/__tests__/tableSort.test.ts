// Recipe-table sorting: raw values, empties last, totals pinned, per-currency.
import { describe, it, expect } from 'vitest'
import { sortTableRows } from '@/utils/tableSort'

const order = (rows: { row: Record<string, unknown> }[], key = 'name') => rows.map((r) => r.row[key])

describe('sortTableRows', () => {
  const data = [
    { name: 'b', value: '1200.50', date: '2026-03-01' },
    { name: 'a', value: 95, date: '2025-12-31' },
    { name: 'c', value: null, date: '' },
    { name: 'd', value: '300', date: '2026-01-15' },
  ]

  it("keeps the data's order without a sort, with original indexes", () => {
    expect(sortTableRows(data, null).map((r) => r.index)).toEqual([0, 1, 2, 3])
  })

  it('sorts numbers and numeric strings by value, empties last in both directions', () => {
    expect(order(sortTableRows(data, { key: 'value', dir: 'asc' }))).toEqual(['a', 'd', 'b', 'c'])
    expect(order(sortTableRows(data, { key: 'value', dir: 'desc' }))).toEqual(['b', 'd', 'a', 'c'])
  })

  it('sorts ISO dates and text', () => {
    expect(order(sortTableRows(data, { key: 'date', dir: 'asc' }))).toEqual(['a', 'd', 'b', 'c'])
    expect(order(sortTableRows(data, { key: 'name', dir: 'desc' }))).toEqual(['d', 'c', 'b', 'a'])
  })

  it('compares text naturally and ignores case', () => {
    const rows = [{ name: 'Item 10' }, { name: 'item 2' }, { name: 'Item 1' }]
    expect(order(sortTableRows(rows, { key: 'name', dir: 'asc' }))).toEqual(['Item 1', 'item 2', 'Item 10'])
  })

  it('keeps summary rows at the bottom', () => {
    const rows = [{ name: 'x', v: 5 }, { name: 'Total', v: 100, isTotal: true }, { name: 'y', v: 1 }]
    expect(order(sortTableRows(rows, { key: 'v', dir: 'desc' }))).toEqual(['x', 'y', 'Total'])
  })

  it('sorts a currency-aware column within each currency', () => {
    const rows = [
      { name: 'u1', amt: 10, currency: 'USD' },
      { name: 'i1', amt: 5000, currency: 'INR' },
      { name: 'u2', amt: 30, currency: 'USD' },
      { name: 'i2', amt: 100, currency: 'INR' },
    ]
    expect(order(sortTableRows(rows, { key: 'amt', dir: 'desc' }, 'currency'))).toEqual(['u2', 'u1', 'i1', 'i2'])
  })

  it('keeps ties in their original order', () => {
    const rows = [{ name: 'p', v: 1 }, { name: 'q', v: 1 }, { name: 'r', v: 0 }]
    expect(order(sortTableRows(rows, { key: 'v', dir: 'desc' }))).toEqual(['p', 'q', 'r'])
  })
})
