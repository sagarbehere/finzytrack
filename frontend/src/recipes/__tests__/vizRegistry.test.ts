/**
 * Viz-input shape guard (G1, §4.5b / §7.1).
 *
 * Each grain rejects a clearly-wrong shape (e.g. a plain rows[] wired to a
 * pivot, or a PivotData object wired to a chart) with the widget error state,
 * and accepts the shape its renderer actually consumes.
 */

import { describe, it, expect } from 'vitest'
import { validateVizInput, supportedGrains, VIZ_REGISTRY } from '@/recipes/vizRegistry'
import { SUPPORTED_CHART_TYPES } from '@/types/recipes'

const rows = [{ account: 'A', value: 1 }]
const pivot = { columns: ['Jan'], rows: [{ label: 'A', values: { Jan: 1 } }] }

describe('validateVizInput', () => {
  it('table accepts a rows array and rejects a PivotData object', () => {
    expect(validateVizInput({ type: 'table' }, rows)).toBeNull()
    expect(validateVizInput({ type: 'table' }, pivot)).toMatch(/array of row objects/)
  })

  it('pivot accepts PivotData and rejects a plain rows array', () => {
    expect(validateVizInput({ type: 'pivot' }, pivot)).toBeNull()
    expect(validateVizInput({ type: 'pivot' }, rows)).toMatch(/PivotData/)
  })

  it('bar chart accepts rows and rejects a PivotData object', () => {
    expect(validateVizInput({ type: 'chart', chartType: 'bar' }, rows)).toBeNull()
    expect(validateVizInput({ type: 'chart', chartType: 'bar' }, pivot)).toMatch(/array of row objects/)
  })

  it('gauge accepts a scalar or a single object', () => {
    expect(validateVizInput({ type: 'chart', chartType: 'gauge' }, 42)).toBeNull()
    expect(validateVizInput({ type: 'chart', chartType: 'gauge' }, { value: 42 })).toBeNull()
  })

  it('kpi is permissive (number, object, or rows)', () => {
    expect(validateVizInput({ type: 'kpi' }, 42)).toBeNull()
    expect(validateVizInput({ type: 'kpi' }, { value: 1 })).toBeNull()
    expect(validateVizInput({ type: 'kpi' }, rows)).toBeNull()
  })

  it('reports unknown viz type and unknown chartType', () => {
    expect(validateVizInput({ type: 'hologram' }, rows)).toMatch(/unknown visualization type/)
    expect(validateVizInput({ type: 'chart', chartType: 'wat' }, rows)).toMatch(/unknown chartType/)
  })

  it('registers every supported chart type', () => {
    for (const ct of SUPPORTED_CHART_TYPES) {
      expect(ct in VIZ_REGISTRY.chart).toBe(true)
    }
    expect(supportedGrains()).toEqual(['kpi', 'table', 'pivot', ...SUPPORTED_CHART_TYPES])
  })
})
