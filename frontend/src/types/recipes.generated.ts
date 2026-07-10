/* Auto-generated from recipe.schema.json — do not edit by hand. Regenerate via npm run generate-recipe-types. */

/**
 * JSON shape of a dashboard or widget recipe authored by the AI assistant or by a user. Source of truth for both validation and the prompt-doc appendix. Function-typed fields (transform, getValueLink, etc.) on the runtime TypeScript types are deliberately excluded — only serialisable fields appear here.
 */
export type FinzyTrackRecipe = JsonDashboardRecipe;
/**
 * Lowercase letters, numbers, and hyphens. Must start and end alphanumeric (e.g. 'my-dashboard-name').
 */
export type RecipeId = string;
/**
 * The recipe's data pipeline as an array of named steps. Array order is for readability only; execution order is the topological sort of {{steps.*}} references. Step ids must be unique.
 *
 * @minItems 1
 */
export type Steps = [Step, ...Step[]];
/**
 * A single node in a recipe's data-pipeline DAG. Discriminated on `kind`.
 */
export type Step = QueryStep | ComputeStep | TransformStep;
/**
 * Step identifier, unique within a recipe. Referenced from other steps as {{steps.<id>}} (or {{dashboard.steps.<id>}} for a dashboard shared step). Lowercase letters, numbers, and hyphens.
 */
export type StepId = string;
/**
 * The recipe's data pipeline as an array of named steps. Array order is for readability only; execution order is the topological sort of {{steps.*}} references. Step ids must be unique.
 *
 * @minItems 1
 */
export type Steps1 = [Step, ...Step[]];
export type JsonRecipeVisualization =
  | JsonChartVisualization
  | JsonKPIVisualization
  | JsonTableVisualization
  | JsonPivotVisualization
  | JsonBudgetProgressVisualization;
export type ChartType =
  | "bar"
  | "line"
  | "pie"
  | "area"
  | "scatter"
  | "treemap"
  | "funnel"
  | "gauge"
  | "calendar"
  | "sankey"
  | "radar"
  | "sunburst";
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

/**
 * The only recipe type. Owns layout, parameters, optional shared steps, and an array of inline widget definitions.
 */
export interface JsonDashboardRecipe {
  /**
   * Recipe format version. Always 2 for the steps/DAG format. Files without it are rejected (run the migration).
   */
  schemaVersion: 2;
  id: RecipeId;
  title: string;
  description?: string;
  parameters?: RecipeParameter[];
  steps?: Steps;
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
   * Inline widget definitions (non-empty). Each widget's id is the layout.widgets[].widgetId target within this dashboard.
   *
   * @minItems 1
   */
  widgets: [JsonWidgetRecipe, ...JsonWidgetRecipe[]];
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
export interface QueryStep {
  id: StepId;
  kind: "query";
  /**
   * A read-only query against the ledger, in the dialect of the chosen `engine` (SQL for sqlite, BQL for beanquery). Use :paramName for recipe-parameter placeholders. {{...}} step references are NOT allowed here — a query step is a leaf data source and cannot read another step's rows.
   */
  query: string;
  /**
   * Query engine for this step. `sqlite` (default) runs SQL against the SQLite mirror; `beanquery` runs Beancount Query Language (BQL).
   */
  engine?: "sqlite" | "beanquery";
}
export interface ComputeStep {
  id: StepId;
  kind: "compute";
  /**
   * Name of a server-side compute function (fixed catalog; see get_compute_functions). Validated server-side.
   */
  fn: string;
  /**
   * Scalar arguments for the compute function. Values may be literals or {{params.x}} / {{steps.x}} / {{dashboard.steps.x}} template strings. Pass only small scalars — bulk data is read server-side by the function, not shuttled through the client.
   */
  args?: {
    [k: string]: unknown;
  };
}
export interface TransformStep {
  id: StepId;
  kind: "transform";
  /**
   * Name of a client-side transform from the fixed catalog (none, firstRow, firstValue, sortBy, limit, pluck, where, pivot, joinBudgetActual, joinByPeriod, runningSum, envelopeRollover). Validated server-side.
   */
  fn: string;
  /**
   * Ordered {{steps.<id>}} / {{dashboard.steps.<id>}} references to the step outputs this transform consumes.
   *
   * @minItems 1
   */
  inputs: [string, ...string[]];
  /**
   * Transform-specific configuration; the accepted shape depends on fn (see the transform catalog in the schema doc).
   */
  config?: {
    [k: string]: unknown;
  };
}
export interface WidgetLayout {
  widgetId: string;
  /**
   * CSS grid-area: 'row-start / col-start / row-end / col-end' (1-based, e.g. '1 / 1 / 2 / 4').
   */
  gridArea: string;
  [k: string]: unknown;
}
/**
 * An inline widget inside a dashboard. Widgets are never stored or referenced standalone — each lives in its enclosing dashboard's widgets[]. schemaVersion is stamped on the dashboard, not here.
 */
export interface JsonWidgetRecipe {
  id: RecipeId;
  title: string;
  description?: string;
  /**
   * Tooltip shown as ⓘ icon.
   */
  helpText?: string;
  parameters?: RecipeParameter[];
  steps: Steps1;
  /**
   * Id of the step whose output feeds the visualization. Required; must name a step in this widget's steps[].
   */
  output: string;
  visualization: JsonRecipeVisualization;
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
  /**
   * Colour the value (and icon) by sign — green when ≥ 0, red when negative. Use for figures where negative is bad, e.g. a Remaining/over-budget KPI.
   */
  colorBySign?: boolean;
  clickLink?: JsonValueLinkConfig;
  [k: string]: unknown;
}
export interface JsonTableVisualization {
  type: "table";
  columns: JsonTableColumn[];
  /**
   * Message shown when the table has no rows (default: 'No data available').
   */
  emptyText?: string;
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
/**
 * Budget-vs-actual progress list: one row per budgeted account with a fill bar (spent vs budget, over-budget in red) and amounts. Consumes the flat rows from the joinBudgetActual transform. Not an ECharts chart — a purpose-built Vue component.
 */
export interface JsonBudgetProgressVisualization {
  type: "budget-progress";
  /**
   * Row field for the account label (default: 'account').
   */
  accountField?: string;
  /**
   * Row field for the budget amount (default: 'budget').
   */
  budgetField?: string;
  /**
   * Row field for the actual spend (default: 'actual').
   */
  actualField?: string;
  /**
   * Row field for remaining = budget - actual (default: 'remaining').
   */
  remainingField?: string;
  /**
   * Row field for the fraction of budget used, e.g. 1.23 = 123% (default: 'pctUsed').
   */
  pctUsedField?: string;
  /**
   * Row field for the currency code (default: 'currency').
   */
  currencyField?: string;
  /**
   * Row field holding 'under-good' | 'over-good' (expense vs income). Default 'direction'; absent → under-good.
   */
  directionField?: string;
  /**
   * Predefined value formatter applied at render time.
   */
  accountFormat?:
    | "currency"
    | "percent"
    | "number"
    | "compact"
    | "signedCurrency"
    | "date"
    | "dateShort"
    | "accountName"
    | "accountName2";
  link?: JsonValueLinkConfig1;
  /**
   * Message shown when there are no rows.
   */
  emptyText?: string;
  [k: string]: unknown;
}
/**
 * Optional per-row click-through (interpolates {{row.<field>}}), e.g. to the category's transactions.
 */
export interface JsonValueLinkConfig1 {
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
