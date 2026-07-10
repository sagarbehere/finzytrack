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
 *
 * ── To add a VISUALIZATION TYPE (checklist) ──────────────────────────────────
 * A viz type spans several files (schema, this registry, render components,
 * ECharts internals, the backend gallery, tests); miss one and you get a blank
 * panel, a false "unknown type", or a test failure. This is the
 * registry/render-focused view; for the full cross-cutting map (backend gallery,
 * generated-file automation, which tests catch a missed step) see
 * dev-docs/adding-a-new-visualization-type.md.
 *
 *   New CHART TYPE (bar/line/…):
 *     1. Add it to the `ChartType` enum in frontend/src/types/recipe.schema.json,
 *        then `npm run generate-recipe-types` (updates SUPPORTED_CHART_TYPES).
 *     2. It is picked up here automatically via `chartGrain()` — BUT it gets the
 *        generic "array of row objects" validator. If its real input shape is
 *        NOT plain rows (e.g. sankey nodes/links, treemap/sunburst hierarchy,
 *        calendar date/value), add a dedicated grain + a case in `chartGrain()`,
 *        or the guard will silently accept malformed data (see gaugeGrain).
 *     3. Teach RecipeChart.vue to render it — THREE edits, not one:
 *          a. register the ECharts series component in the `echarts.use([...])`
 *             call (miss it → blank panel);
 *          b. add it to `DATA_INJECTED_TYPES` if the runtime injects rows into
 *             series[0].data instead of dataset.source (hierarchical/flow charts);
 *          c. add it to `NON_CARTESIAN_TYPES` if it has no x/y axes.
 *        (The chartType name isn't always the ECharts series type — e.g. `area`
 *        is a line series with areaStyle.)
 *     4. Add one widget-gallery.json example — ENFORCED by
 *        backend/tests/test_widget_gallery.py (a schema type with no gallery
 *        widget fails). `vizRegistry.test.ts` checks supportedGrains() coverage.
 *
 *   New TOP-LEVEL TYPE (a peer of kpi/table/pivot/chart):
 *     1. Add a JsonXVisualization def + extend the RecipeVisualization oneOf in
 *        recipe.schema.json, then regenerate types.
 *     2. Add a grain to VIZ_REGISTRY below (top-level key). `validateVizInput`
 *        derives its non-chart map from the registry, so no second edit there.
 *     3. Add a v-else-if branch + component in RecipeWidgetRenderer.vue.
 *     4. Add one widget-gallery.json example (one widget per grain; enforced by
 *        test_widget_gallery.py). Update vizRegistry.test.ts / golden tests.
 * ─────────────────────────────────────────────────────────────────────────────
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

// budget-progress renders one fill bar per row (the flat joinBudgetActual output:
// { account, budget, actual, remaining, pctUsed, pace, ... }).
const budgetProgressGrain: VizGrain = {
  expectedShape: 'an array of budget rows ({ account, budget, actual, pctUsed, … })',
  validateInput: rowsValidator('budget-progress'),
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
  'budget-progress': budgetProgressGrain,
  chart: Object.fromEntries(
    SUPPORTED_CHART_TYPES.map((ct) => [ct, chartGrain(ct)]),
  ) as Record<string, VizGrain>,
}

/** Top-level grains that are NOT the `chart` family (kpi/table/pivot/…). */
const NON_CHART_GRAINS: Record<string, VizGrain> = Object.fromEntries(
  Object.entries(VIZ_REGISTRY).filter(([, g]) => 'validateInput' in g),
) as Record<string, VizGrain>

/** All supported grain keys (top-level non-chart grains + every chart type). */
export function supportedGrains(): string[] {
  return [...Object.keys(NON_CHART_GRAINS), ...SUPPORTED_CHART_TYPES]
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
  const grain = NON_CHART_GRAINS[viz.type]
  if (!grain) return `unknown visualization type "${viz.type}"`
  return grain.validateInput(data)
}
