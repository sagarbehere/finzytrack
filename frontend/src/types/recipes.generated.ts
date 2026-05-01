/* Auto-generated from recipe.schema.json — do not edit by hand. Regenerate via npm run generate-recipe-types. */

/**
 * JSON shape of a dashboard or widget recipe authored by the AI assistant or by a user. Source of truth for both validation and the prompt-doc appendix. Function-typed fields (transform, getValueLink, etc.) on the runtime TypeScript types are deliberately excluded — only serialisable fields appear here.
 */
export type FinzyTrackRecipe = JsonWidgetRecipe | JsonDashboardRecipe;
/**
 * Lowercase letters, numbers, and hyphens. Must start and end alphanumeric (e.g. 'my-dashboard-name').
 */
export type RecipeId = string;
export type Transform = ("none" | "firstRow" | "firstValue") | TransformConfig;
export type TransformConfig = {
  [k: string]: unknown;
} & {
  type: "sortBy" | "limit" | "pluck" | "pivot";
  /**
   * For sortBy or pluck.
   */
  field?: string;
  order?: "asc" | "desc";
  /**
   * For limit transform.
   */
  count?: number;
  /**
   * For pivot — column whose values become row labels.
   */
  rowField?: string;
  /**
   * For pivot — column whose values become column headers.
   */
  columnField?: string;
  /**
   * For pivot — column containing cell values.
   */
  valueField?: string;
  formatColumn?: "monthYear" | "yearMonth";
  sortRowsBy?: "total_desc" | "total_asc" | "label_asc" | "label_desc";
  [k: string]: unknown;
};
export type JsonRecipeVisualization =
  | JsonChartVisualization
  | JsonKPIVisualization
  | JsonTableVisualization
  | JsonPivotVisualization;
export type ChartType = "bar" | "line" | "pie" | "area" | "scatter" | "treemap";
/**
 * Predefined value formatter applied at render time.
 */
export type ValueFormat =
  | "currency"
  | "percent"
  | "number"
  | "compact"
  | "signedCurrency"
  | "date"
  | "dateShort"
  | "accountName"
  | "accountName2";

export interface JsonWidgetRecipe {
  id: RecipeId;
  title: string;
  description?: string;
  /**
   * Tooltip shown as ⓘ icon.
   */
  helpText?: string;
  parameters?: RecipeParameter[];
  /**
   * Query engine override (defaults to dashboard/view setting).
   */
  dbType?: "sqlite" | "beanquery";
  /**
   * SQL SELECT (SQLite). Use :paramName for parameter placeholders.
   */
  query: string;
  transform?: Transform;
  visualization: JsonRecipeVisualization;
  [k: string]: unknown;
}
export interface RecipeParameter {
  /**
   * SQL placeholder name (referenced as :name in queries).
   */
  name: string;
  /**
   * Human-readable label shown in the parameter UI.
   */
  label: string;
  type: "date" | "select" | "number";
  /**
   * Default value, or a generator object {"$gen": "name"} resolved at runtime.
   */
  default?:
    | string
    | number
    | {
        $gen: string;
        [k: string]: unknown;
      };
  /**
   * Either a literal array of {value, label} options, or a generator reference like {"$gen": "monthOptions"} resolved to an array at runtime.
   */
  options?:
    | {
        value: string | number;
        label: string;
        [k: string]: unknown;
      }[]
    | {
        $gen: string;
        [k: string]: unknown;
      };
  /**
   * Populate options dynamically from the user's ledger.
   */
  optionsFrom?: "currencies" | "years";
  min?: number;
  max?: number;
  [k: string]: unknown;
}
export interface JsonChartVisualization {
  type: "chart";
  chartType: ChartType;
  /**
   * ECharts options object.
   */
  options?: {
    [k: string]: unknown;
  };
  clickLink?: JsonValueLinkConfig;
  /**
   * Per-series click links keyed by series name. Set to null to disable for that series.
   */
  seriesClickLinks?: {
    [k: string]: JsonValueLinkConfig | null;
  };
  seriesLabelFormat?: ValueFormat;
  yAxisLabelFormat?: ValueFormat;
  xAxisLabelFormat?: ValueFormat;
  [k: string]: unknown;
}
export interface JsonValueLinkConfig {
  /**
   * Vue route name, e.g. 'transactions'.
   */
  name: string;
  /**
   * Template strings interpolated with {{data.field}}, {{row.label}}, {{parameters.x}}, {{dateFrom}}, {{dateTo}}.
   */
  query: {
    [k: string]: string;
  };
  [k: string]: unknown;
}
export interface JsonKPIVisualization {
  type: "kpi";
  /**
   * Single character (↑ ↓ $ % # or any Unicode).
   */
  icon?: string;
  iconColor?: "blue" | "green" | "red" | "purple" | "amber";
  /**
   * Column to read the value from (default: 'value').
   */
  valueField?: string;
  format?: ValueFormat;
  showTrend?: boolean;
  trendField?: string;
  /**
   * Group amounts by currency. Query must return currency and amount columns.
   */
  multiCurrency?: boolean;
  amountField?: string;
  currencyField?: string;
  clickLink?: JsonValueLinkConfig;
  [k: string]: unknown;
}
export interface JsonTableVisualization {
  type: "table";
  columns: JsonTableColumn[];
  [k: string]: unknown;
}
export interface JsonTableColumn {
  key: string;
  label: string;
  format?: ValueFormat;
  align?: "left" | "center" | "right";
  link?: JsonValueLinkConfig;
  [k: string]: unknown;
}
export interface JsonPivotVisualization {
  type: "pivot";
  /**
   * Label for the row header column (default: 'Account').
   */
  rowHeader?: string;
  format?: ValueFormat;
  showRowTotals?: boolean;
  showColumnTotals?: boolean;
  valueLink?: JsonValueLinkConfig;
  [k: string]: unknown;
}
export interface JsonDashboardRecipe {
  id: RecipeId;
  title: string;
  description?: string;
  parameters?: RecipeParameter[];
  layout: {
    /**
     * Total columns in the grid (12 typical).
     */
    columns: number;
    /**
     * CSS gap, e.g. '1.5rem'.
     */
    gap?: string;
    /**
     * CSS row height, e.g. '140px' or '200px'.
     */
    rowHeight?: string;
    widgets: WidgetLayout[];
    [k: string]: unknown;
  };
  /**
   * Inline widget definitions. Empty [] when widgets are loaded by widgetId from the registry.
   */
  widgets: JsonWidgetRecipe[];
  [k: string]: unknown;
}
export interface WidgetLayout {
  widgetId: string;
  /**
   * CSS grid-area: 'row-start / col-start / row-end / col-end' (1-based, e.g. '1 / 1 / 2 / 4').
   */
  gridArea: string;
  [k: string]: unknown;
}
