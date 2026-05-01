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
  Transform,
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
export type RecipeParameterType = 'date' | 'select' | 'number'

export interface RecipeParameterOption {
  value: string | number
  label: string
}

// Visualization types
export type VisualizationType = 'chart' | 'kpi' | 'table' | 'pivot'
// Runtime const array of chart types (used by the Analysis chart picker etc.).
// The corresponding `ChartType` *type* is generated from recipe.schema.json
// and re-exported below; both must list the same values.
export const SUPPORTED_CHART_TYPES = ['bar', 'line', 'pie', 'area', 'scatter', 'treemap', 'funnel', 'gauge', 'calendar'] as const

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

export interface TableColumn {
  key: string
  label: string
  format?: (value: unknown) => string
  align?: 'left' | 'center' | 'right'
  /**
   * Function to generate link for cell values in this column.
   * Return null/undefined for no link.
   */
  getLink?: (context: TableLinkContext) => ValueLinkConfig | null | undefined
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

// Query engine types
export type QueryEngineType = 'sqlite' | 'beanquery'

// Widget Recipe
export interface WidgetRecipe {
  id: string
  title: string
  description?: string
  helpText?: string // Shown as ⓘ tooltip in the widget header
  parameters?: RecipeParameter[]
  dbType?: QueryEngineType // Query engine for this widget (defaults to dashboard/view setting)
  query: string // SQL or BQL query with :paramName placeholders
  transform?: (rows: Record<string, unknown>[]) => unknown // Transform query results
  visualization: RecipeVisualization
}

// (WidgetLayout is generated from recipe.schema.json and re-exported below.)

export interface DashboardRecipe {
  id: string
  title: string
  description?: string
  parameters?: RecipeParameter[] // Dashboard-level parameters shared by widgets
  layout: {
    columns: number
    gap?: string
    rowHeight?: string
    widgets: WidgetLayout[]
  }
  widgets: WidgetRecipe[]
}

// Registry for looking up recipes by ID
export interface RecipeRegistry {
  widgets: Record<string, WidgetRecipe>
  dashboards: Record<string, DashboardRecipe>
}

// Execution result types
export interface RecipeExecutionResult {
  data: unknown
  loading: boolean
  error: string | null
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

/**
 * Runtime list of value-format identifiers, kept here as a const array because
 * `json-schema-to-typescript` only emits the `ValueFormat` *type* — not a
 * runtime value to enumerate. Must match the enum in recipe.schema.json.
 */
export const VALID_VALUE_FORMATS = [
  'currency',
  'percent',
  'number',
  'compact',
  'signedCurrency',
  'date',
  'dateShort',
  'accountName',
  'accountName2',
] as const

/**
 * Simple transform types (no configuration needed)
 * - 'none': Pass through rows as-is
 * - 'firstRow': Extract first row as the result
 * - 'firstValue': Extract first value from first row
 */
export type SimpleTransformType = 'none' | 'firstRow' | 'firstValue'

// Re-export the JSON-recipe types generated from recipe.schema.json.
// This is the single source of truth for the JSON shape; consumer code keeps
// importing from '@/types/recipes' transparently.
export type {
  ChartType,
  JsonChartVisualization,
  JsonDashboardRecipe,
  JsonKPIVisualization,
  JsonPivotVisualization,
  JsonRecipeVisualization,
  JsonTableColumn,
  JsonTableVisualization,
  JsonValueLinkConfig,
  JsonWidgetRecipe,
  RecipeId,
  RecipeParameter,
  Transform,
  TransformConfig,
  ValueFormat,
  WidgetLayout,
} from './recipes.generated'

import type { Transform as _Transform, JsonWidgetRecipe as _JsonWidgetRecipe, JsonDashboardRecipe as _JsonDashboardRecipe } from './recipes.generated'

/** Backwards-compat alias for the union. */
export type TransformType = _Transform

/**
 * Manifest file structure for user recipes (path lists). Not in the JSON
 * recipe schema because it's not a recipe — it indexes recipes.
 */
export interface RecipeManifest {
  widgets: string[]
  dashboards: string[]
}

/**
 * Combined registry that can hold both TypeScript code-defined recipes and
 * JSON recipes loaded at runtime.
 */
export interface HybridRecipeRegistry {
  widgets: Record<string, WidgetRecipe | _JsonWidgetRecipe>
  dashboards: Record<string, DashboardRecipe | _JsonDashboardRecipe>
}
