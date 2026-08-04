/**
 * Recipe System Type Definitions
 *
 * Recipes define data-driven dashboard widgets and layouts.
 * Each widget contains: SQL query, transform function, and visualization config.
 *
 * The JSON-recipe types (Json*Recipe, Json*Visualization, Transform,
 * RecipeParameter, WidgetLayout, ChartType, ValueFormat, RecipeId) are
 * generated from recipe.schema.json — see recipes.generated.ts. This file
 * holds only the runtime/code-defined types (function-typed fields, Hybrid
 * registries, the runtime SUPPORTED_CHART_TYPES const, etc.) and re-exports
 * the generated names for consumers.
 */

import type {
  ChartType,
  JsonDashboardRecipe,
  JsonValueLinkConfig,
  JsonWidgetRecipe,
  RecipeParameter,
  Step,
  ValueFormat,
  WidgetLayout,
} from './recipes.generated'

// Multi-currency KPI values
export interface CurrencyAmount {
  amount: number
  currency: string
}

// Parameter helper types referenced only by the runtime ChartVisualization /
// WidgetRecipe types defined later in this file. The richer RecipeParameter is
// generated from recipe.schema.json and re-exported below.
export type RecipeParameterType = (typeof VALID_PARAM_TYPES)[number]

export interface RecipeParameterOption {
  value: string | number
  label: string
}

// Visualization types. The value lists (VIZ_TYPES, SUPPORTED_CHART_TYPES,
// STEP_KINDS, VALID_VALUE_FORMATS, VALID_PARAM_TYPES, QUERY_ENGINES) are
// generated from recipe.schema.json into recipes.enums.generated.ts and
// re-exported below; these unions derive from them so nothing is hand-listed.
export type VisualizationType = (typeof VIZ_TYPES)[number]

/**
 * Context passed to getSeriesClickLink for chart click handling
 */
export interface ChartClickContext {
  seriesName: string
  seriesIndex: number
  dataIndex: number
  data: Record<string, unknown> // The row from the dataset
  parameters: Record<string, string | number> // Current widget parameters
}

export interface ChartVisualization {
  type: 'chart'
  chartType: ChartType
  options: Record<string, unknown> // ECharts options
  /**
   * Function to generate a navigation link when a chart series element is clicked.
   * Return null/undefined for no link.
   */
  getSeriesClickLink?: (context: ChartClickContext) => ValueLinkConfig | null | undefined
  /**
   * Template-based click link applied to all series (JSON recipes).
   * Uses {{data.fieldName}}, {{seriesName}}, {{parameters.paramName}} interpolation.
   * Overridden per-series by seriesClickLinks.
   */
  clickLink?: JsonValueLinkConfig
  /**
   * Per-series click links (JSON recipes). Keys are series names.
   * Set a key to null to explicitly disable click-through for that series.
   * Takes precedence over clickLink for matched series names.
   *
   * Example:
   *   { "Income": { "name": "transactions", "query": { "accountContains": "Income" } },
   *     "Expenses": { "name": "transactions", "query": { "accountContains": "Expenses" } },
   *     "Savings": null }
   */
  seriesClickLinks?: Record<string, JsonValueLinkConfig | null>
  /**
   * Predefined format applied to all series data labels at render time.
   * Injects a formatter function — no JS needed in JSON recipes.
   * Common values: 'compact' (14.2k), 'currency' ($14,200), 'number' (14,200).
   */
  seriesLabelFormat?: ValueFormat
  /**
   * Predefined format applied to the y-axis tick labels at render time.
   */
  yAxisLabelFormat?: ValueFormat
  /**
   * Predefined format applied to the x-axis tick labels at render time.
   */
  xAxisLabelFormat?: ValueFormat
}

export interface KPIVisualization {
  type: 'kpi'
  icon?: string
  formatValue?: (value: number) => string
  showTrend?: boolean
  trendField?: string
  multiCurrency?: boolean
  clickLink?: JsonValueLinkConfig
}

/**
 * Resolved dashboard-parameter assignments a "select" click writes (param name →
 * value), used for master-detail drill-down. See the `select` link action.
 */
export type SelectParams = Record<string, string>

export interface TableColumn {
  key: string
  label: string
  /**
   * Format a cell value. Receives the whole row as an optional second argument so
   * a currency-aware column can format each cell in its own currency (per-row
   * `currencyField`; see dev-docs/dashboard-multi-currency.md §6.2).
   */
  format?: (value: unknown, row?: Record<string, unknown>) => string
  align?: 'left' | 'center' | 'right'
  /**
   * Function to generate link for cell values in this column.
   * Return null/undefined for no link.
   */
  getLink?: (context: TableLinkContext) => ValueLinkConfig | null | undefined
  /**
   * Function to resolve a "select" action (dashboard params to set on click)
   * for cell values in this column — the alternative to getLink (navigation).
   * Return null/undefined for no action.
   */
  getSelect?: (context: TableLinkContext) => SelectParams | null | undefined
}

export interface TableVisualization {
  type: 'table'
  columns: TableColumn[]
}

/**
 * Link configuration for clickable values in tables/pivot tables.
 * Values can link to other views (e.g., transactions) with filters.
 */
export interface ValueLinkQuery {
  accountContains?: string
  dateFrom?: string
  dateTo?: string
  payeeContains?: string
  narrationContains?: string
  [key: string]: string | undefined
}

export interface ValueLinkConfig {
  name: string // Route name (e.g., 'transactions')
  query: ValueLinkQuery
}

/**
 * Context passed to getValueLink function for pivot tables
 */
export interface PivotLinkContext {
  rowLabel: string
  rowData: PivotRow
  column: string
  columnIndex: number
  value: number
}

/**
 * Context passed to getValueLink function for tables
 */
export interface TableLinkContext {
  row: Record<string, unknown>
  rowIndex: number
  column: TableColumn
  value: unknown
}

export interface PivotVisualization {
  type: 'pivot'
  rowHeader?: string // Label for the row header column (default: 'Account')
  valueFormat?: (value: number) => string // Format for cell values
  showRowTotals?: boolean // Show row totals column (default: true)
  showColumnTotals?: boolean // Show column totals row (default: true)
  /**
   * Function to generate link for a cell value.
   * Return null/undefined for no link.
   */
  getValueLink?: (context: PivotLinkContext) => ValueLinkConfig | null | undefined
}

/**
 * Data structure for pivot table visualization.
 * Transform function should return this shape.
 */
export interface PivotData {
  columns: string[] // Column headers (e.g., month names)
  rows: PivotRow[]
  columnTotals?: Record<string, number> // Totals for each column
  grandTotal?: number
  /**
   * Optional metadata for each column (e.g., raw date values).
   * Used by getValueLink to access original data.
   */
  columnMeta?: Record<string, unknown>[]
}

export interface PivotRow {
  label: string // Row label (e.g., account name)
  values: Record<string, number> // Column name -> value
  total?: number // Row total
  /**
   * Optional pre-computed links for each column.
   * Alternative to using getValueLink function.
   */
  links?: Record<string, ValueLinkConfig>
  /**
   * Optional metadata for this row (e.g., full account path).
   * Used by getValueLink to access original data.
   */
  meta?: Record<string, unknown>
}

export type RecipeVisualization =
  | ChartVisualization
  | KPIVisualization
  | TableVisualization
  | PivotVisualization

// Query engine types (derived from the generated QUERY_ENGINES const).
export type QueryEngineType = (typeof QUERY_ENGINES)[number]

// ============================================================================
// Recipe pipeline steps (DAG model)
//
// The three step kinds and their JSON shapes (Step, QueryStep, ComputeStep,
// TransformStep, StepId, Steps) are generated from recipe.schema.json and
// re-exported below. StepKind is the runtime const mirror of the schema's
// `kind` discriminator — kept here because json-schema-to-typescript emits
// only the type, not a runtime value to enumerate.
// ============================================================================

// STEP_KINDS is generated from recipe.schema.json (see re-exports below).
export type StepKind = (typeof STEP_KINDS)[number]

/**
 * Transform-step configuration. The accepted shape depends on the transform
 * `fn` (e.g. pivot needs rowField/columnField/valueField). Validated at the
 * transform/server layer, so the type is intentionally open.
 */
export interface TransformConfig {
  type?: string
  [key: string]: unknown
}

/**
 * Context passed to every transform-catalog function alongside its inputs.
 * Carries the resolved recipe parameters so transforms can be time-aware
 * (e.g. linear pace from the period fraction).
 */
export interface TransformContext {
  params: Record<string, string | number>
}

// Widget Recipe — an inline widget inside a dashboard. A DAG of named steps
// feeding a visualization (no standalone storage; see refactored-dashboard-recipes.md §3.0).
export interface WidgetRecipe {
  id: string
  title: string
  description?: string
  helpText?: string // Shown as ⓘ tooltip in the widget header
  parameters?: RecipeParameter[]
  steps: Step[] // Data-pipeline DAG; execution order is the topo-sort of {{steps.*}} refs
  output: string // Id of the step whose output feeds the visualization
  visualization: RecipeVisualization
}

// (WidgetLayout is generated from recipe.schema.json and re-exported below.)

export interface DashboardRecipe {
  schemaVersion?: number // Recipe format version (2 for the steps/DAG format)
  id: string
  title: string
  description?: string
  parameters?: RecipeParameter[] // Dashboard-level parameters shared by widgets
  steps?: Step[] // Optional dashboard-level shared steps ({{dashboard.steps.*}})
  layout: {
    columns: number
    gap?: string
    rowHeight?: string
    widgets: WidgetLayout[]
  }
  widgets: WidgetRecipe[]
}

// Registry for looking up dashboards by ID (dashboard is the only recipe type).
export interface RecipeRegistry {
  dashboards: Record<string, DashboardRecipe>
}

// ============================================================================
// JSON Recipe Types (for user-defined recipes loaded at runtime)
//
// JSON-recipe types — including JsonWidgetRecipe, JsonDashboardRecipe, the
// JSON visualization variants, RecipeParameter, TransformConfig, ChartType,
// ValueFormat, WidgetLayout, and RecipeId — are GENERATED from
// recipe.schema.json. They live in recipes.generated.ts and are re-exported
// at the bottom of this file. Do not duplicate them here.
//
// Hand-written types in this file (CurrencyAmount, the runtime
// {Chart,KPI,Table,Pivot}Visualization with function fields, WidgetRecipe,
// DashboardRecipe, etc.) cover code-defined recipes and runtime helpers.
// ============================================================================

// VALID_VALUE_FORMATS is generated from recipe.schema.json (see re-exports below).

/**
 * Simple transform types (no configuration needed)
 * - 'none': Pass through rows as-is
 * - 'firstRow': Extract first row as the result
 * - 'firstValue': Extract first value from first row
 */
export type SimpleTransformType = 'none' | 'firstRow' | 'firstValue'

// Re-export the runtime enum consts generated from recipe.schema.json. The local
// import keeps `typeof <CONST>` resolvable for the derived unions above
// (StepKind, VisualizationType, RecipeParameterType, QueryEngineType).
import {
  STEP_KINDS,
  VIZ_TYPES,
  VALID_PARAM_TYPES,
  QUERY_ENGINES,
} from './recipes.enums.generated'
export {
  STEP_KINDS,
  VIZ_TYPES,
  SUPPORTED_CHART_TYPES,
  VALID_VALUE_FORMATS,
  VALID_PARAM_TYPES,
  QUERY_ENGINES,
} from './recipes.enums.generated'

// Re-export the JSON-recipe types generated from recipe.schema.json.
// This is the single source of truth for the JSON shape; consumer code keeps
// importing from '@/types/recipes' transparently.
export type {
  ChartType,
  ComputeStep,
  JsonBudgetProgressVisualization,
  JsonChartVisualization,
  JsonDashboardRecipe,
  JsonKPIVisualization,
  JsonPivotVisualization,
  JsonRecipeVisualization,
  JsonTableColumn,
  JsonTableVisualization,
  JsonValueLinkConfig,
  JsonRouteLinkConfig,
  JsonSelectLinkConfig,
  JsonWidgetRecipe,
  QueryStep,
  RecipeId,
  RecipeParameter,
  Step,
  StepId,
  Steps,
  TransformStep,
  ValueFormat,
  WidgetLayout,
} from './recipes.generated'

import type { JsonDashboardRecipe as _JsonDashboardRecipe } from './recipes.generated'

/**
 * Manifest file structure for user recipes (path lists). Not in the JSON
 * recipe schema because it's not a recipe — it indexes recipes. Dashboards
 * only — standalone widgets were removed in the DAG refactor.
 */
export interface RecipeManifest {
  dashboards: string[]
}

/**
 * Registry of loaded dashboards (dashboard is the only recipe type). Holds
 * both any TypeScript code-defined dashboards and JSON dashboards loaded at runtime.
 */
export interface HybridRecipeRegistry {
  dashboards: Record<string, DashboardRecipe | _JsonDashboardRecipe>
}
