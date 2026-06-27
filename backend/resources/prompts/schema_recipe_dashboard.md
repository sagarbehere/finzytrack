## Dashboard Recipe JSON Schema

You generate **dashboard recipes** — JSON files that define multi-widget dashboards with CSS Grid
layout. Each dashboard contains inline widget definitions with SQL queries, transforms, and
visualization configs. The `write_recipe` tool validates and saves them.

### Top-level dashboard structure

```json
{
  "id": "my-dashboard-id",
  "title": "Human-Readable Title",
  "description": "Optional one-line description shown in the dashboard picker",
  "parameters": [],
  "layout": {
    "columns": 12,
    "gap": "1.5rem",
    "rowHeight": "140px",
    "widgets": []
  },
  "widgets": []
}
```

**Required fields:** `id`, `title`, `layout`, `widgets`

- `id`: Lowercase letters, numbers, and hyphens. Must be unique across all dashboards.
- `parameters`: Dashboard-level parameters shared by all widgets (see Parameters section).
- `layout.columns`: Use 12 for multi-widget grids. Use 6 for simpler layouts.
- `layout.rowHeight`: Default `"140px"`. Use `"200px"` for chart-heavy dashboards.
- `layout.widgets`: Array of `{ "widgetId": "...", "gridArea": "row-start / col-start / row-end / col-end" }`.
- `widgets`: Array of inline widget definitions (see Widget structure below).

### CSS Grid layout

Each widget placement uses CSS Grid `gridArea`: `"row-start / col-start / row-end / col-end"`.

**Common patterns (12-column grid):**
- Full-width KPI row (4 KPIs): each spans 3 columns
  - `"1 / 1 / 2 / 4"`, `"1 / 4 / 2 / 7"`, `"1 / 7 / 2 / 10"`, `"1 / 10 / 2 / 13"`
- Full-width chart: `"2 / 1 / 5 / 13"` (3 rows tall)
- Half-width charts: `"2 / 1 / 5 / 7"` and `"2 / 7 / 5 / 13"`
- Single-widget dashboard: use `columns: 6`, one widget at `"1 / 1 / 5 / 7"`

**Rules:**
- Every `widgetId` in `layout.widgets` MUST have a matching widget `id` in the `widgets` array.
- Row/column indices are 1-based. Column end must not exceed `columns + 1`.

### Widget structure (inline in dashboard)

A widget is a **DAG of named steps** feeding a visualization. Each step is `sql`,
`compute`, or `transform`; `output` names the step whose result the viz renders.

```json
{
  "id": "widget-id",
  "title": "Widget Title",
  "description": "Optional description",
  "helpText": "Optional tooltip shown as ⓘ icon",
  "parameters": [],
  "steps": [
    { "id": "rows", "kind": "sql", "query": "SELECT ... FROM postings WHERE ..." },
    { "id": "out", "kind": "transform", "fn": "firstRow", "inputs": ["{{steps.rows}}"] }
  ],
  "output": "out",
  "visualization": { "type": "kpi" }
}
```

**Required fields:** `id`, `title`, `steps`, `output`, `visualization`.

**Step kinds:**
- `sql` — `{ id, kind:"sql", query }`. A leaf data source over the SQLite mirror.
  Uses `:paramName` placeholders only; it **cannot** reference other steps
  (`{{...}}` is invalid in `query`). Combine sources in a `transform`.
- `compute` — `{ id, kind:"compute", fn, args }`. Calls a server-side function
  (fixed catalog — call `get_compute_functions`). `args` are small scalars; values
  may be `{{params.x}}` / `{{steps.x}}` templates.
- `transform` — `{ id, kind:"transform", fn, inputs, config? }`. Calls a client
  transform (catalog below) over the outputs of the steps named in `inputs`
  (each a `{{steps.<id>}}` or `{{dashboard.steps.<id>}}` reference).

A simplest single-query widget is `[ {sql}, {transform fn:"none"} ]` with
`output` on the transform. Step ids are lowercase-with-hyphens, unique per widget.

**Dashboard shared steps:** the dashboard may declare a top-level `steps` array
(no `output`). They run once and widgets reference them via `{{dashboard.steps.<id>}}`
— use this to compute an expensive value once and feed many widgets.

**Fixed-catalog rule:** `compute` functions and `transform`s are a fixed,
server-provided catalog. You **select** from it — you cannot invent a `fn` name or
emit code. If nothing fits, say so.

### Parameters

Parameters create interactive controls (dropdowns, date pickers) that inject values into the SQL
query via `:paramName` placeholders.

```json
"parameters": [
  {
    "name": "year",
    "label": "Year",
    "type": "select",
    "default": { "$gen": "currentYear" },
    "optionsFrom": "years"
  },
  {
    "name": "currency",
    "label": "Currency",
    "type": "select",
    "default": { "$gen": "defaultCurrency" },
    "optionsFrom": "currencies"
  },
  {
    "name": "limit",
    "label": "Show Top",
    "type": "number",
    "default": 10,
    "min": 5,
    "max": 50
  }
]
```

- `type`: `"select"`, `"number"`, or `"date"`
- `optionsFrom: "currencies"` — dynamically populates from the user's ledger currencies
- `optionsFrom: "years"` — dynamically populates from years present in the ledger data
- Dashboard-level parameters cascade to all widgets. Widget-level parameters override dashboard ones.
- In the query, reference as `:year`, `:currency`, `:limit`, etc.

### Generators ($gen)

Use generators for dynamic default values and option lists:

| Generator | Output | Example usage |
|-----------|--------|---------------|
| `currentYear` | Current year number | `{ "$gen": "currentYear" }` |
| `currentMonth` | Current month (1-12) | `{ "$gen": "currentMonth" }` |
| `monthOptions` | Array of month options (Jan-Dec) | `{ "$gen": "monthOptions" }` |
| `quarterOptions` | Array of quarter options | `{ "$gen": "quarterOptions" }` |
| `today` | Today's date string | `{ "$gen": "today" }` |
| `startOfMonth` | First day of month | `{ "$gen": "startOfMonth", "offset": -1 }` |
| `endOfMonth` | Last day of month | `{ "$gen": "endOfMonth" }` |
| `defaultCurrency` | User's default currency | `{ "$gen": "defaultCurrency" }` |

### SQL query rules

- SQLite-compatible only. Only SELECT statements.
- Use `:paramName` for parameter placeholders.
- Use `strftime()` for date operations (not DATE_TRUNC or EXTRACT).
- The `postings` table schema is described in the postings schema section.
- Money columns (`amount`, `cost_amount`, `price_amount`) are stored as `TEXT` (Decimal-as-string). Always wrap them in `CAST(amount AS REAL)` before `SUM`/`AVG`/arithmetic. Comparisons (`WHERE amount > 0`) and grouping work without an explicit cast.
- Always `GROUP BY currency` or filter `WHERE currency = :currency` when summing amounts.
- Use `HAVING` to filter out zero-value rows.
- Include `ORDER BY` when results have a natural ordering.
- **Always test your SQL with `execute_query` before building the recipe.**

### Transform catalog (fixed)

A `transform` step calls one of these by `fn` over its `inputs`. The first input
is the primary rowset; `config` shapes behavior.

| fn | inputs | config | output |
|---|---|---|---|
| `none` | `[rows]` | — | rows unchanged |
| `firstRow` | `[rows]` | — | the first row object |
| `firstValue` | `[rows]` | — | first value of the first row |
| `sortBy` | `[rows]` | `{ field, order }` | sorted rows |
| `limit` | `[rows]` | `{ count }` | first N rows |
| `pluck` | `[rows]` | `{ field }` | array of one field |
| `pivot` | `[rows]` | `{ rowField, columnField, valueField, formatColumn?, sortRowsBy? }` | PivotData |
| `joinBudgetActual` | `[budgets, actuals]` | `{ totalAccount?, periodStart?, periodEnd? }` | variance rows (flat; remainder mode adds Unbudgeted+Total when `totalAccount` is set) |
| `joinByPeriod` | `[budgetsByPeriod, actualsByPeriod]` | — | `[{ period, budget, actual }]` |
| `runningSum` | `[rows]` | `{ fields:[…], orderBy }` | rows + cumulative<Field> columns |
| `envelopeRollover` | `[budgetsByPeriod, actualsByPeriod]` | — | `[{ period, budget, actual, available, carryover, overspent }]` |

Budget transforms pair with the `budget_for_range` compute function — see
`get_budget_guide` and `get_compute_functions`.

### Visualization types

#### KPI — Single metric display

```json
{
  "type": "kpi",
  "icon": "↑",
  "iconColor": "green",
  "multiCurrency": true,
  "format": "currency",
  "clickLink": {
    "name": "transactions",
    "query": { "accountContains": "Income", "dateFrom": "{{dateFrom}}", "dateTo": "{{dateTo}}" }
  }
}
```

- `icon`: Single character (↑ ↓ $ % # or any Unicode)
- `iconColor`: `"blue"`, `"green"`, `"red"`, `"purple"`, `"amber"`
- `multiCurrency: true` — Query must return `currency` and `amount` columns. Groups amounts by currency.
- `format`: `"currency"`, `"number"`, `"compact"`, `"percent"` (optional, auto-detected)
- For single-value KPI, query should return one row with an `amount` or `value` column.
  Use a `firstRow` transform step if needed.

#### Bar chart

```json
{
  "type": "chart",
  "chartType": "bar",
  "seriesLabelFormat": "compact",
  "yAxisLabelFormat": "compact",
  "options": {
    "legend": { "data": ["Expenses", "Income"], "top": 0, "left": "left" },
    "grid": { "top": 40, "bottom": 40, "left": 50, "right": 20 },
    "xAxis": { "type": "category" },
    "yAxis": { "type": "value" },
    "series": [
      {
        "name": "Expenses",
        "type": "bar",
        "encode": { "x": "month_label", "y": "expenses" },
        "itemStyle": { "color": "#E8A951" },
        "label": { "show": true, "position": "top", "fontSize": 10 }
      }
    ]
  }
}
```

- `encode` maps query column names to chart dimensions: `{ "x": "column_name", "y": "column_name" }`
- For horizontal bars: swap — `xAxis: { type: "value" }`, `yAxis: { type: "category" }`, encode `{ "x": "amount", "y": "name" }`
- Multiple series: add multiple objects to `series[]`

#### Line chart

Same as bar chart but with `"chartType": "line"` and series `"type": "line"`.
Add `"smooth": true` for smooth curves. Add `"areaStyle": {}` for area fill.

#### Pie chart

```json
{
  "type": "chart",
  "chartType": "pie",
  "options": {
    "tooltip": { "trigger": "item" },
    "series": [
      {
        "type": "pie",
        "radius": ["30%", "60%"],
        "encode": { "itemName": "name", "value": "value" },
        "label": { "show": true, "formatter": "{b}: {d}%" }
      }
    ]
  }
}
```

- Query must return `name` and `value` columns.
- Use `HAVING value > 0` — pie charts cannot display negative values.
- `radius: ["30%", "60%"]` creates a donut chart. Use `"50%"` for solid pie.

#### Treemap

```json
{
  "type": "chart",
  "chartType": "treemap",
  "options": {
    "tooltip": { "trigger": "item" },
    "series": [
      {
        "type": "treemap",
        "roam": false,
        "breadcrumb": { "show": false },
        "label": { "show": true, "formatter": "{b}" },
        "itemStyle": { "borderColor": "#fff", "borderWidth": 2, "gapWidth": 2 }
      }
    ]
  }
}
```

**CRITICAL treemap rules:**
- Query MUST return rows with `name` and `value` columns (exactly these names).
- Do NOT use `encode` in the series config. The app injects data directly into `series[0].data`.
- Use `HAVING value > 0` — treemaps cannot display negative or zero values.
- Do NOT set label colors — the treemap component auto-adjusts label contrast.

#### Table

```json
{
  "type": "table",
  "columns": [
    { "key": "account", "label": "Account" },
    { "key": "total", "label": "Total", "align": "right" }
  ]
}
```

- `columns[].key` maps to query column names.

#### Pivot table

```json
{
  "type": "pivot",
  "rowHeader": "Account",
  "showRowTotals": true,
  "showColumnTotals": true,
  "valueLink": {
    "name": "transactions",
    "query": {
      "accountContains": "{{row.label}}",
      "dateFrom": "{{columnMeta.startDate}}",
      "dateTo": "{{columnMeta.endDate}}"
    }
  }
}
```

Requires a `pivot` transform step on the widget:
```json
{ "id": "pivoted", "kind": "transform", "fn": "pivot", "inputs": ["{{steps.rows}}"],
  "config": { "rowField": "account", "columnField": "year_month", "valueField": "amount",
              "formatColumn": "monthYear", "sortRowsBy": "total_desc" } }
```
with `output: "pivoted"`.

### Tooltips — keep them simple

Charts use ECharts internally. The runtime injects a **currency-aware tooltip
formatter** automatically when you set the trigger and nothing else. So:

```json
"tooltip": { "trigger": "axis" }      // bar, line — runtime formats values
"tooltip": { "trigger": "item" }      // pie, treemap — runtime formats values
```

**Never put a string template in `tooltip.formatter`.** Specifically:

- `"formatter": "{c}"`, `"formatter": "{b}: {c}"`, etc. **break** on dataset-
  driven charts (which is how all our recipes work). ECharts substitutes `{c}`
  with the row object instead of the value, so the tooltip shows the literal
  string `[object Object]`.
- The runtime defensively strips such formatters and logs a warning, but the
  validator will also reject the recipe — so don't generate them.

If a series label needs a template (`"label": { "formatter": "{b}" }`) that's
fine — series labels work differently and `{b}` (data name) is safe there.
The risk is *only* in `tooltip.formatter`.

### Format strings

Available for `seriesLabelFormat`, `yAxisLabelFormat`, `xAxisLabelFormat`, and KPI `format`:

| Format | Output | Use for |
|--------|--------|---------|
| `"currency"` | $14,200 | Dollar amounts |
| `"compact"` | 14.2k | Large numbers |
| `"number"` | 14,200 | Plain numbers |
| `"percent"` | 42% | Percentages |
| `"accountName"` | Groceries | Last segment of account path |
| `"accountName2"` | Food:Groceries | Last 2 segments |

### Click-through links

Make values clickable to navigate to the transactions view with filters.

**Template variables:**
- `{{data.columnName}}` — value from the data row (charts)
- `{{row.label}}` — row label (pivot tables)
- `{{columnMeta.startDate}}` / `{{columnMeta.endDate}}` — column date range (pivot)
- `{{parameters.paramName}}` — current parameter value
- `{{dateFrom}}` / `{{dateTo}}` — shorthand computed from year/month parameters

**Per-series override** (charts with multiple series):
```json
"seriesClickLinks": {
  "Income": { "name": "transactions", "query": { "accountContains": "Income" } },
  "Expenses": { "name": "transactions", "query": { "accountContains": "Expenses" } },
  "Savings": null
}
```
Set a series to `null` to explicitly disable click-through for that series.

### Full examples

For a complete reference combining structure, parameters, SQL, and click-through links,
call `read_recipe` on an existing widget or dashboard (see `list_recipes`). Examples like
`year-summary` and `month-summary` cover the common multi-widget patterns: KPI row →
full-width chart → pivot table.

<!-- BEGIN AUTO-GENERATED FROM recipe.schema.json — do not edit by hand -->

### Type reference (generated from `recipe.schema.json`)

The following section is generated from the authoritative JSON Schema. Use it as the ground truth when the prose above is unclear. The top-level recipe must match either `JsonWidgetRecipe` or `JsonDashboardRecipe`.

#### `JsonDashboardRecipe`
The only recipe type. Owns layout, parameters, optional shared steps, and an array of inline widget definitions.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schemaVersion` | `'2'` | yes | Recipe format version. Always 2 for the steps/DAG format. Files without it are rejected (run the migration). |
| `id` | `RecipeId` | yes |  |
| `title` | `string` | yes |  |
| `description` | `string` | — |  |
| `parameters` | `RecipeParameter[]` | — |  |
| `steps` | `Steps` | — | Optional dashboard-level shared steps. Run once per dashboard render; widgets reference them via {{dashboard.steps.<id>}}. There is no dashboard-level output. |
| `layout` | `object` | yes |  |
| `widgets` | `JsonWidgetRecipe[]` | yes | Inline widget definitions (non-empty). Each widget's id is the layout.widgets[].widgetId target within this dashboard. |

#### `JsonWidgetRecipe`
An inline widget inside a dashboard. Widgets are never stored or referenced standalone — each lives in its enclosing dashboard's widgets[]. schemaVersion is stamped on the dashboard, not here.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `RecipeId` | yes |  |
| `title` | `string` | yes |  |
| `description` | `string` | — |  |
| `helpText` | `string` | — | Tooltip shown as ⓘ icon. |
| `parameters` | `RecipeParameter[]` | — |  |
| `steps` | `Steps` | yes |  |
| `output` | `string` | yes | Id of the step whose output feeds the visualization. Required; must name a step in this widget's steps[]. |
| `visualization` | `JsonRecipeVisualization` | yes |  |

#### `ChartType`

Type: `'bar' | 'line' | 'pie' | 'area' | 'scatter' | 'treemap' | 'funnel' | 'gauge' | 'calendar' | 'sankey' | 'radar' | 'sunburst'`

#### `ComputeStep`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `StepId` | yes |  |
| `kind` | `'compute'` | yes |  |
| `fn` | `string` | yes | Name of a server-side compute function (fixed catalog; see get_compute_functions). Validated server-side. |
| `args` | `object` | — | Scalar arguments for the compute function. Values may be literals or {{params.x}} / {{steps.x}} / {{dashboard.steps.x}} template strings. Pass only small scalars — bulk data is read server-side by the function, not shuttled through the client. |

#### `JsonChartVisualization`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `'chart'` | yes |  |
| `chartType` | `ChartType` | yes |  |
| `options` | `object` | — | ECharts options object. |
| `clickLink` | `JsonValueLinkConfig` | — |  |
| `seriesClickLinks` | `Record<string, JsonValueLinkConfig | null>` | — | Per-series click links keyed by series name. Set to null to disable for that series. |
| `seriesLabelFormat` | `ValueFormat` | — |  |
| `yAxisLabelFormat` | `ValueFormat` | — |  |
| `xAxisLabelFormat` | `ValueFormat` | — |  |

#### `JsonKPIVisualization`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `'kpi'` | yes |  |
| `icon` | `string` | — | Single character (↑ ↓ $ % # or any Unicode). |
| `iconColor` | `'blue' | 'green' | 'red' | 'purple' | 'amber'` | — |  |
| `valueField` | `string` | — | Column to read the value from (default: 'value'). |
| `format` | `ValueFormat` | — |  |
| `showTrend` | `boolean` | — |  |
| `trendField` | `string` | — |  |
| `multiCurrency` | `boolean` | — | Group amounts by currency. Query must return currency and amount columns. |
| `amountField` | `string` | — |  |
| `currencyField` | `string` | — |  |
| `clickLink` | `JsonValueLinkConfig` | — |  |

#### `JsonPivotVisualization`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `'pivot'` | yes |  |
| `rowHeader` | `string` | — | Label for the row header column (default: 'Account'). |
| `format` | `ValueFormat` | — |  |
| `showRowTotals` | `boolean` | — |  |
| `showColumnTotals` | `boolean` | — |  |
| `valueLink` | `JsonValueLinkConfig` | — |  |

#### `JsonRecipeVisualization`

Type: `JsonChartVisualization | JsonKPIVisualization | JsonTableVisualization | JsonPivotVisualization`

#### `JsonTableColumn`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | `string` | yes |  |
| `label` | `string` | yes |  |
| `format` | `ValueFormat` | — |  |
| `align` | `'left' | 'center' | 'right'` | — |  |
| `link` | `JsonValueLinkConfig` | — |  |

#### `JsonTableVisualization`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `'table'` | yes |  |
| `columns` | `JsonTableColumn[]` | yes |  |

#### `JsonValueLinkConfig`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | yes | Vue route name, e.g. 'transactions'. |
| `query` | `Record<string, string>` | yes | Template strings interpolated with {{data.field}}, {{row.label}}, {{parameters.x}}, {{dateFrom}}, {{dateTo}}. |

#### `RecipeId`
Lowercase letters, numbers, and hyphens. Must start and end alphanumeric (e.g. 'my-dashboard-name').

Type: `string`

#### `RecipeParameter`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | yes | SQL placeholder name (referenced as :name in queries). |
| `label` | `string` | yes | Human-readable label shown in the parameter UI. |
| `type` | `'date' | 'select' | 'number'` | yes |  |
| `default` | `string | number | object` | — | Default value, or a generator object {"$gen": "name"} resolved at runtime. |
| `options` | `object[] | object` | — | Either a literal array of {value, label} options, or a generator reference like {"$gen": "monthOptions"} resolved to an array at runtime. |
| `optionsFrom` | `'currencies' | 'years'` | — | Populate options dynamically from the user's ledger. |
| `min` | `number` | — |  |
| `max` | `number` | — |  |

#### `SqlStep`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `StepId` | yes |  |
| `kind` | `'sql'` | yes |  |
| `query` | `string` | yes | SQL SELECT against the ledger mirror. Use :paramName for recipe-parameter placeholders. {{...}} step references are NOT allowed here — a SQL step is a leaf data source and cannot read another step's rows. |
| `dbType` | `'sqlite' | 'beanquery'` | — | Query engine for this step (defaults to sqlite). |

#### `Step`
A single node in a recipe's data-pipeline DAG. Discriminated on `kind`.

Type: `SqlStep | ComputeStep | TransformStep`

#### `StepId`
Step identifier, unique within a recipe. Referenced from other steps as {{steps.<id>}} (or {{dashboard.steps.<id>}} for a dashboard shared step). Lowercase letters, numbers, and hyphens.

Type: `string`

#### `Steps`
The recipe's data pipeline as an array of named steps. Array order is for readability only; execution order is the topological sort of {{steps.*}} references. Step ids must be unique.

Type: `Step[]`

#### `TransformStep`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `StepId` | yes |  |
| `kind` | `'transform'` | yes |  |
| `fn` | `string` | yes | Name of a client-side transform from the fixed catalog (none, firstRow, firstValue, sortBy, limit, pluck, pivot, joinBudgetActual, runningSum, envelopeRollover, ...). Validated server-side. |
| `inputs` | `string[]` | yes | Ordered {{steps.<id>}} / {{dashboard.steps.<id>}} references to the step outputs this transform consumes. |
| `config` | `object` | — | Transform-specific configuration; the accepted shape depends on fn (see the transform catalog in the schema doc). |

#### `ValueFormat`
Predefined value formatter applied at render time.

Type: `'currency' | 'percent' | 'number' | 'compact' | 'signedCurrency' | 'date' | 'dateShort' | 'accountName' | 'accountName2'`

#### `WidgetLayout`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `widgetId` | `string` | yes |  |
| `gridArea` | `string` | yes | CSS grid-area: 'row-start / col-start / row-end / col-end' (1-based, e.g. '1 / 1 / 2 / 4'). |

<!-- END AUTO-GENERATED -->
