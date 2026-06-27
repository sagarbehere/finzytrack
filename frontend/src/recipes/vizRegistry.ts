/**
 * Visualization registry + runtime input-shape guard (G1, §4.5b).
 *
 * The output of a widget's `output` step is a runtime value the recipe JSON
 * can't statically constrain, so the gate is a runtime shape guard: before the
 * renderer draws, it looks up the grain by (viz.type[, chartType]) and runs
 * `validateInput`. On mismatch the widget shows a clear error instead of a
 * blank panel or a crash.
 *
 * The registry is the single source of truth for the supported grains and their
 * expected shapes — the renderer guard, the docs, and (later) the AI's
 * supported-type list all read from here. `validateInput` codifies the contract
 * the existing RecipeChart/KPI/Table/PivotTable components already consume; it
 * does not invent a new one.
 */

import { SUPPORTED_CHART_TYPES } from '@/types/recipes'

export interface VizGrain {
  /** Short human description of the accepted shape (also AI-doc material). */
  expectedShape: string
  /** Returns an error message on shape mismatch, or null when the data is acceptable. */
  validateInput: (data: unknown) => string | null
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isRowArray(v: unknown): boolean {
  return Array.isArray(v) && v.every((row) => isRecord(row))
}

/** Rows: an array of row objects (empty is fine). */
function rowsValidator(grain: string): (data: unknown) => string | null {
  return (data) =>
    isRowArray(data)
      ? null
      : `${grain} expects an array of row objects; got ${describe(data)}`
}

function describe(v: unknown): string {
  if (v === null) return 'null'
  if (Array.isArray(v)) return `an array of ${v.length === 0 ? 'nothing' : typeof v[0]}`
  return typeof v
}

// ── Grain validators ──────────────────────────────────────────────────────────

const kpiGrain: VizGrain = {
  // KPI extracts a scalar from a number, a single object, or rows — permissive.
  expectedShape: 'a number, a single row object, or an array of rows',
  validateInput: (data) =>
    data === null || typeof data === 'number' || isRecord(data) || Array.isArray(data)
      ? null
      : `kpi expects a number, an object, or an array of rows; got ${describe(data)}`,
}

const tableGrain: VizGrain = {
  expectedShape: 'an array of row objects',
  validateInput: rowsValidator('table'),
}

const pivotGrain: VizGrain = {
  expectedShape: 'a PivotData object ({ columns: [], rows: [] })',
  validateInput: (data) =>
    isRecord(data) && Array.isArray(data.columns) && Array.isArray(data.rows)
      ? null
      : `pivot expects a PivotData object with columns[] and rows[]; got ${describe(data)}`,
}

// Gauge renders a single scalar/row; the other chart types consume a row array
// (ECharts dataset.source, or series[0].data for hierarchical/flow charts).
const gaugeGrain: VizGrain = {
  expectedShape: 'a scalar, a single row, or an array of rows',
  validateInput: (data) =>
    data === null || typeof data === 'number' || isRecord(data) || isRowArray(data)
      ? null
      : `gauge expects a scalar or rows; got ${describe(data)}`,
}

function chartGrain(chartType: string): VizGrain {
  if (chartType === 'gauge') return gaugeGrain
  return {
    expectedShape: `an array of row objects (${chartType} dataset)`,
    validateInput: rowsValidator(`${chartType} chart`),
  }
}

// ── Registry ──────────────────────────────────────────────────────────────────

export const VIZ_REGISTRY = {
  kpi: kpiGrain,
  table: tableGrain,
  pivot: pivotGrain,
  chart: Object.fromEntries(
    SUPPORTED_CHART_TYPES.map((ct) => [ct, chartGrain(ct)]),
  ) as Record<string, VizGrain>,
}

/** All supported grain keys (kpi/table/pivot + every chart type). */
export function supportedGrains(): string[] {
  return ['kpi', 'table', 'pivot', ...SUPPORTED_CHART_TYPES]
}

/**
 * Validate that `data` matches the shape a visualization expects.
 * Returns an error message on mismatch, or null when acceptable.
 */
export function validateVizInput(
  viz: { type?: string; chartType?: string } | null | undefined,
  data: unknown,
): string | null {
  if (!viz || typeof viz.type !== 'string') return 'visualization is missing a type'
  if (viz.type === 'chart') {
    const ct = viz.chartType
    if (!ct || !(ct in VIZ_REGISTRY.chart)) return `unknown chartType "${ct}"`
    return VIZ_REGISTRY.chart[ct].validateInput(data)
  }
  const nonChart: Record<string, VizGrain> = {
    kpi: VIZ_REGISTRY.kpi,
    table: VIZ_REGISTRY.table,
    pivot: VIZ_REGISTRY.pivot,
  }
  const grain = nonChart[viz.type]
  if (!grain) return `unknown visualization type "${viz.type}"`
  return grain.validateInput(data)
}
