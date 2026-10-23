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
 * A built-in dashboard action. 'update-prices': an Update prices button that fetches new prices for your holdings (the same as Investments → Prices) and then refreshes the dashboard. Below it, the date of the prices in use and how many holdings need a newer price. It appears only while you hold something whose prices are fetched, so it is safe on dashboards everyone uses. Use it on dashboards that show market values.
 */
export type DashboardAction = "update-prices";
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
  | JsonTreeTableVisualization
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
 * A click action on a widget value/row/series: either NAVIGATE to a route or SELECT dashboard parameters (master-detail).
 */
export type JsonValueLinkConfig = JsonRouteLinkConfig | JsonSelectLinkConfig;
/**
 * Predefined value formatter applied at render time.
 */
export type ValueFormat =
  | "currency"
  | "percent"
  | "number"
  | "units"
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
  /**
   * Optional, dismissible offer shown above the dashboard when items in this scope have recognized metadata not set (Investments → Setup & Metadata review). Links to that tab. Phrase it as an offer of what tagging adds, never as missing or incomplete data.
   */
  metadataHint?: {
    /**
     * Which items the offer is about: commodities (holdings), cash and deposit accounts, or income accounts.
     */
    scope: "commodity" | "account" | "income-account";
    /**
     * The offer shown to the user, e.g. 'Want allocation by asset class? Add asset classes to your holdings.'
     */
    text: string;
  };
  /**
   * Optional buttons in the dashboard header, chosen from a fixed list of built-in actions. Nothing is inferred: a dashboard shows an action only if it lists it here, whatever its steps compute.
   */
  actions?: DashboardAction[];
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
  type: "date" | "select" | "number" | "boolean";
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
   * Populate options dynamically from the user's ledger. 'currencies' = commodities that play a currency (unit-of-account) role, i.e. is_currency (USD, INR) — use for currency pickers. 'holdings' = non-currency commodities, i.e. investment holdings (stocks/funds like VOO, VTI). 'commodities' = every commodity, currencies and holdings alike. 'accounts' = all accounts; 'expenseAccounts'/'incomeAccounts' = only that type (value = full account path, label = path below the type root, e.g. 'Expenses:Insurance:Health' → 'Insurance:Health'). 'budgetTotals' = accounts that carry a budget AND have a budgeted descendant — i.e. valid top-down 'total' accounts for a zero-based/catch-all view (includes quoted roots like 'Expenses').
   */
  optionsFrom?:
    | "currencies"
    | "holdings"
    | "commodities"
    | "years"
    | "accounts"
    | "expenseAccounts"
    | "incomeAccounts"
    | "budgetTotals";
  min?: number;
  max?: number;
  /**
   * For a `date` (or `number`) control: bind the input's minimum to another parameter's current value (e.g. a 'to' date whose minimum is the 'from' date). Reactive.
   */
  minParam?: string;
  /**
   * For a `date` (or `number`) control: bind the input's maximum to another parameter's current value (e.g. a 'from' date that can't exceed the 'as of' date). Reactive.
   */
  maxParam?: string;
  /**
   * When true, the parameter is functional (its default applies, it can be set by a `select` click or the URL, and steps read it) but renders NO control in the parameter bar. Use for a parameter driven only by click-to-select master-detail.
   */
  hidden?: boolean;
  /**
   * For a `select` param (typically optionsFrom: 'currencies'): prepend an 'All' option that binds the sentinel value '*'. Used for a dashboard-level currency filter — 'All' (default) shows every currency (KPIs stacked, tables per-currency), a specific pick narrows the whole dashboard. In a query, gate the filter with `WHERE (:currency = '*' OR currency = :currency)`. The '*' value does not override or hide a same-named widget-level currency param (so per-chart pickers stay usable in All-mode). See dev-docs/dashboard-multi-currency.md.
   */
  allowAll?: boolean;
  /**
   * Conditional visibility: this parameter's control is shown only when another parameter's current value equals `equals`. Use e.g. to reveal a date only when a boolean toggle is on. The parameter stays functional when hidden by this rule (its default/last value still feeds steps).
   */
  showWhen?: {
    /**
     * Name of the parameter this one depends on.
     */
    param: string;
    /**
     * Value that `param` must have for this control to show.
     */
    equals: string | number | boolean;
  };
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
   * Name of a client-side transform from the fixed catalog — see the Transform catalog in the schema doc (get_recipe_schema) for the names and each transform's inputs/config/output. The catalog's single source of truth is transforms.catalog.json; validated server-side against it.
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
/**
 * NAVIGATE click action: clicking the value/row/series routes to a Vue route with a templated query.
 */
export interface JsonRouteLinkConfig {
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
}
/**
 * SELECT click action: clicking sets dashboard parameters from the clicked context INSTEAD of navigating, re-running the widgets that depend on them (master-detail drill-down).
 */
export interface JsonSelectLinkConfig {
  /**
   * Dashboard-parameter name → template ({{row.field}}, {{data.field}}, {{parameters.x}}). E.g. {"account": "{{row.account}}"} makes a row click drive an account-scoped drill-down widget.
   */
  select: {
    [k: string]: string;
  };
}
export interface JsonKPIVisualization {
  type: "kpi";
  /**
   * Single character (↑ ↓ $ % # or any Unicode).
   */
  icon?: string;
  /**
   * A theme token ('{{theme.brand}}', '{{theme.valence.good}}', …), a hex/CSS color, or a legacy named color (blue/green/red/purple/amber). Prefer theme tokens.
   */
  iconColor?: string;
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
  /**
   * Optional. A second amount field (per row / per currency) rendered as a small muted sub-line beneath the primary value — e.g. a year-to-date figure under an all-time total. Formatted as currency using the same currencyField.
   */
  secondaryField?: string;
  /**
   * Optional label prefix for the secondary sub-line (e.g. 'YTD').
   */
  secondaryLabel?: string;
  /**
   * Opt-in empty state: shown instead of '$0.00' when the KPI has no non-zero value (e.g. no unattributed interest). Omit to keep showing the zero value.
   */
  emptyText?: string;
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
  /**
   * For a money column in a multi-currency table: the row field holding that row's currency code, so each cell is formatted in its OWN currency (symbol + locale) instead of the single widget `currency` param. Use with a `currency`/`signedCurrency` `format`; on a `units` column it sets the digit grouping (e.g. INR → 10,78,850.400). Without it, all cells format with the widget currency (defaulting to USD), which mis-renders mixed-currency rows. See dev-docs/dashboard-multi-currency.md §6.2.
   */
  currencyField?: string;
  link?: JsonValueLinkConfig;
  [k: string]: unknown;
}
/**
 * Expandable tree table built from flat rows with a hierarchical path (e.g. account names). Every path prefix becomes a collapsible parent row; parent and group rows show aggregates (e.g. totals) of the columns that declare one. The query returns only the leaf rows — no total rows. Users can search, sort siblings by a column, expand to a level, show/hide and resize columns, and copy the visible rows for a spreadsheet; expansion, sort, column choices and widths are remembered.
 */
export interface JsonTreeTableVisualization {
  type: "tree-table";
  /**
   * Row field holding the hierarchical path, e.g. 'account' for 'Assets:Liquid:Checking:BofA'.
   */
  pathField: string;
  /**
   * Path separator (default ':').
   */
  separator?: string;
  /**
   * Leading path segments to leave out of the hierarchy, e.g. 1 to drop a shared 'Assets' root (default 0).
   */
  skipLevels?: number;
  /**
   * Optional row field whose distinct values form the top level, each with its own total — e.g. 'currency', so amounts in different currencies are never added together.
   */
  groupBy?: string;
  /**
   * Header of the tree column (default 'Name').
   */
  label?: string;
  /**
   * Row field with a display label for a row (e.g. an account's display-name). Falls back to the row's last path segment. Parent rows without their own row show their path segment.
   */
  labelField?: string;
  /**
   * A click action on a widget value/row/series: either NAVIGATE to a route or SELECT dashboard parameters (master-detail).
   */
  link?: JsonRouteLinkConfig | JsonSelectLinkConfig;
  /**
   * How many levels start expanded (0 = only the top level shows). Default: everything expanded. The user's own expansion is remembered and wins.
   */
  expandDepth?: number;
  /**
   * Fold a parent that has exactly one child (and no row of its own) into that child, shown as 'Bonds › TreasuryDirect › IBonds', so chains don't repeat the same total. Default true.
   */
  foldSingleChildren?: boolean;
  /**
   * Adds a share column with a small bar: each row's value of `column` as a percentage of its top-level total (base 'total' — the group, or all rows when there is no groupBy) or of its parent (base 'parent').
   */
  share?: {
    /**
     * Key of the value column to take shares of (normally one with aggregate 'sum').
     */
    column: string;
    /**
     * Header of the share column (default '% of total' or '% of parent').
     */
    label?: string;
    /**
     * What the share is of (default 'total').
     */
    base?: "total" | "parent";
    /**
     * Start with the share column hidden.
     */
    hidden?: boolean;
    /**
     * Explanation shown from an info icon next to the share column's heading.
     */
    description?: string;
    [k: string]: unknown;
  };
  /**
   * Row field that, when true (or 1), shows the row dimmed — e.g. a stale account. A parent is dimmed when every row beneath it is.
   */
  dimField?: string;
  /**
   * Value columns shown to the right of the tree column.
   */
  columns: JsonTreeTableColumn[];
  /**
   * Message shown when there are no rows.
   */
  emptyText?: string;
  [k: string]: unknown;
}
export interface JsonTreeTableColumn {
  key: string;
  label: string;
  format?: ValueFormat;
  align?: "left" | "center" | "right";
  /**
   * Row field holding each row's currency code, so money cells format in their own currency (as for table columns). Parent rows have it when everything beneath shares one currency — always true under a currency groupBy.
   */
  currencyField?: string;
  /**
   * How parent and group rows show this column: 'sum' adds up everything beneath (exact decimal arithmetic); 'max'/'min' show the largest/smallest (numbers, or ISO dates — 'max' of a date is the latest). Without it, a parent shows the value only when every row beneath has the same one, else it is blank.
   */
  aggregate?: "sum" | "max" | "min";
  /**
   * Start with this column hidden. Every value column can be shown or hidden from the table's Columns menu; the choice is remembered.
   */
  hidden?: boolean;
  /**
   * Short explanation of the column, shown when hovering or focusing an info icon next to its heading.
   */
  description?: string;
  /**
   * Show this column only on rows that come from the query (e.g. one account), never on parent or group rows — for per-row facts that don't combine, such as a status. Ignored with aggregate.
   */
  leafOnly?: boolean;
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
  /**
   * Tint each cell by its numeric value read as a budget-usage fraction (e.g. pctUsed) — a budget-adherence heat-map. Uses the same green/amber/blue/red status scale as budget-progress.
   */
  colorByValue?: boolean;
  /**
   * With colorByValue: fraction where a cell turns amber ('approaching'). Default 0.85.
   */
  warnAt?: number;
  /**
   * With colorByValue: override the status colours (hex), same keys as budget-progress. Omitted statuses keep the default palette.
   */
  colors?: {
    under?: string;
    approaching?: string;
    exact?: string;
    over?: string;
    [k: string]: unknown;
  };
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
    | "units"
    | "compact"
    | "signedCurrency"
    | "date"
    | "dateShort"
    | "accountName"
    | "accountName2";
  /**
   * Fraction of budget where a bar turns amber ('approaching'). Default 0.85 (85%).
   */
  warnAt?: number;
  /**
   * Override the status bar colours (any CSS/hex colour, applied in both light and dark mode). Omitted statuses keep the default palette (green/amber/blue/red).
   */
  colors?: {
    /**
     * Under budget (default green).
     */
    under?: string;
    /**
     * Approaching the limit (default amber).
     */
    approaching?: string;
    /**
     * Exactly on budget (default blue).
     */
    exact?: string;
    /**
     * Over budget (default red).
     */
    over?: string;
    [k: string]: unknown;
  };
  /**
   * A click action on a widget value/row/series: either NAVIGATE to a route or SELECT dashboard parameters (master-detail).
   */
  link?: JsonRouteLinkConfig | JsonSelectLinkConfig;
  /**
   * Message shown when there are no rows.
   */
  emptyText?: string;
  [k: string]: unknown;
}
