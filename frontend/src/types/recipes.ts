/**
 * Recipe System Type Definitions
 *
 * Recipes define data-driven dashboard widgets and layouts.
 * Each widget contains: SQL query, transform function, and visualization config.
 */

// Multi-currency KPI values
export interface CurrencyAmount {
  amount: number
  currency: string
}

// Parameter types for recipe inputs
export type RecipeParameterType = 'date' | 'select' | 'number'

export interface RecipeParameterOption {
  value: string | number
  label: string
}

export interface RecipeParameter {
  name: string
  label: string
  type: RecipeParameterType
  default: string | number
  options?: RecipeParameterOption[] // For select type
  optionsFrom?: 'currencies' // Dynamic option source
  min?: number // For number type
  max?: number // For number type
}

// Visualization types
export type VisualizationType = 'chart' | 'kpi' | 'table' | 'pivot'
// Define as const array so it's available at runtime (e.g. for the Analysis chart picker).
// Adding a new entry here automatically extends the ChartType union AND makes it selectable.
export const SUPPORTED_CHART_TYPES = ['bar', 'line', 'pie', 'area', 'scatter'] as const
export type ChartType = (typeof SUPPORTED_CHART_TYPES)[number]

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
}

export interface KPIVisualization {
  type: 'kpi'
  icon?: string
  formatValue?: (value: number) => string
  showTrend?: boolean
  trendField?: string
  multiCurrency?: boolean
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
export type QueryEngineType = 'sqlite' | 'duckdb' | 'beanquery'

// Widget Recipe
export interface WidgetRecipe {
  id: string
  title: string
  description?: string
  parameters?: RecipeParameter[]
  dbType?: QueryEngineType // Query engine for this widget (defaults to dashboard/view setting)
  query: string // SQL or BQL query with :paramName placeholders
  transform?: (rows: Record<string, unknown>[]) => unknown // Transform query results
  visualization: RecipeVisualization
}

// Dashboard Layout
export interface WidgetLayout {
  widgetId: string
  gridArea: string // CSS grid-area: "row-start / col-start / row-end / col-end"
}

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
// ============================================================================

/**
 * Predefined format types for values (replaces custom format functions)
 *
 * Basic formats:
 * - 'currency': Format as USD currency ($1,234)
 * - 'percent': Format as percentage (12.5%)
 * - 'number': Format with thousands separator (1,234)
 * - 'compact': Abbreviate large numbers (1.5M, 12.3k)
 *
 * Specialized formats:
 * - 'signedCurrency': Currency with explicit +/- sign (+$1,234 or -$500)
 * - 'date': Format ISO date as readable (Jan 15, 2025)
 * - 'dateShort': Short date format (1/15/25)
 * - 'accountName': Extract last segment of account path (Expenses:Food:Groceries → Groceries)
 * - 'accountName2': Extract last 2 segments (Expenses:Food:Groceries → Food:Groceries)
 */
export type ValueFormat =
  | 'currency'
  | 'percent'
  | 'number'
  | 'compact'
  | 'signedCurrency'
  | 'date'
  | 'dateShort'
  | 'accountName'
  | 'accountName2'

/**
 * Simple transform types (no configuration needed)
 * - 'none': Pass through rows as-is
 * - 'firstRow': Extract first row as the result
 * - 'firstValue': Extract first value from first row
 */
export type SimpleTransformType = 'none' | 'firstRow' | 'firstValue'

/**
 * Transform configuration object for transforms that need parameters
 */
export interface TransformConfig {
  type: 'sortBy' | 'limit' | 'pluck'
  field?: string // For sortBy, pluck
  order?: 'asc' | 'desc' // For sortBy
  count?: number // For limit
}

/**
 * Transform can be a simple string type or a config object
 */
export type TransformType = SimpleTransformType | TransformConfig

/**
 * JSON-compatible KPI visualization (no functions)
 */
export interface JsonKPIVisualization {
  type: 'kpi'
  icon?: string
  iconColor?: 'blue' | 'green' | 'red' | 'purple' | 'amber'
  valueField?: string // Field name to extract value from (default: 'value')
  format?: ValueFormat // Predefined format (default: 'number')
  showTrend?: boolean
  trendField?: string
  multiCurrency?: boolean
  amountField?: string // Field name for amount (default: 'amount')
  currencyField?: string // Field name for currency code (default: 'currency')
}

/**
 * JSON-compatible link configuration using template strings.
 * Template variables: {{row.fieldName}}, {{column}}, {{value}}, etc.
 */
export interface JsonValueLinkConfig {
  name: string // Route name (e.g., 'transactions')
  query: Record<string, string> // Template strings for query params
}

/**
 * JSON-compatible table column (no functions)
 */
export interface JsonTableColumn {
  key: string
  label: string
  format?: ValueFormat // Predefined format instead of function
  align?: 'left' | 'center' | 'right'
  link?: JsonValueLinkConfig // Template-based link
}

/**
 * JSON-compatible table visualization
 */
export interface JsonTableVisualization {
  type: 'table'
  columns: JsonTableColumn[]
}

/**
 * JSON-compatible pivot visualization
 */
export interface JsonPivotVisualization {
  type: 'pivot'
  rowHeader?: string
  format?: ValueFormat // Predefined format for cell values
  showRowTotals?: boolean
  showColumnTotals?: boolean
  valueLink?: JsonValueLinkConfig // Template-based link for cell values
}

/**
 * JSON-compatible visualization (chart options are already JSON-safe)
 */
export type JsonRecipeVisualization =
  | ChartVisualization
  | JsonKPIVisualization
  | JsonTableVisualization
  | JsonPivotVisualization

/**
 * JSON Widget Recipe - can be loaded from JSON files at runtime
 * No functions, only serializable data
 */
export interface JsonWidgetRecipe {
  id: string
  title: string
  description?: string
  parameters?: RecipeParameter[]
  dbType?: QueryEngineType // Query engine for this widget (defaults to dashboard/view setting)
  query: string
  transform?: TransformType // Predefined transform instead of function
  visualization: JsonRecipeVisualization
}

/**
 * JSON Dashboard Recipe - can be loaded from JSON files at runtime
 */
export interface JsonDashboardRecipe {
  id: string
  title: string
  description?: string
  parameters?: RecipeParameter[]
  layout: {
    columns: number
    gap?: string
    rowHeight?: string
    widgets: WidgetLayout[]
  }
  widgets: JsonWidgetRecipe[]
}

/**
 * Manifest file structure for user recipes
 */
export interface RecipeManifest {
  widgets: string[] // List of widget JSON file paths
  dashboards: string[] // List of dashboard JSON file paths
}

/**
 * Combined registry that can hold both TypeScript and JSON recipes
 */
export interface HybridRecipeRegistry {
  widgets: Record<string, WidgetRecipe | JsonWidgetRecipe>
  dashboards: Record<string, DashboardRecipe | JsonDashboardRecipe>
}
