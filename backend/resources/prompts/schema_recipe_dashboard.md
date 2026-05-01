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

```json
{
  "id": "widget-id",
  "title": "Widget Title",
  "description": "Optional description",
  "helpText": "Optional tooltip shown as ⓘ icon",
  "parameters": [],
  "query": "SELECT ... FROM postings WHERE ...",
  "transform": "firstRow",
  "visualization": { "type": "kpi", ... }
}
```

**Required fields:** `id`, `title`, `query`, `visualization`

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
- Always `GROUP BY currency` or filter `WHERE currency = :currency` when summing amounts.
- Use `HAVING` to filter out zero-value rows.
- Include `ORDER BY` when results have a natural ordering.
- **Always test your SQL with `execute_query` before building the recipe.**

### Transforms

Transforms modify query results before visualization. Optional — most widgets don't need one.

**Simple transforms (string):**
- `"firstRow"` — Use only the first row (for KPI from multi-row query)
- `"firstValue"` — Extract the first value from the first row
- `"none"` — No transform (default)

**Object transforms:**
- `{ "type": "sortBy", "field": "total", "order": "desc" }`
- `{ "type": "limit", "count": 10 }`
- `{ "type": "pivot", "rowField": "account", "columnField": "year_month", "valueField": "amount", "formatColumn": "monthYear", "sortRowsBy": "total_desc" }`

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
  Use `transform: "firstRow"` if needed.

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

Requires a pivot transform on the widget:
```json
"transform": {
  "type": "pivot",
  "rowField": "account",
  "columnField": "year_month",
  "valueField": "amount",
  "formatColumn": "monthYear",
  "sortRowsBy": "total_desc"
}
```

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

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `RecipeId` | yes |  |
| `title` | `string` | yes |  |
| `description` | `string` | — |  |
| `parameters` | `RecipeParameter[]` | — |  |
| `layout` | `object` | yes |  |
| `widgets` | `JsonWidgetRecipe[]` | yes | Inline widget definitions. Empty [] when widgets are loaded by widgetId from the registry. |

#### `JsonWidgetRecipe`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `RecipeId` | yes |  |
| `title` | `string` | yes |  |
| `description` | `string` | — |  |
| `helpText` | `string` | — | Tooltip shown as ⓘ icon. |
| `parameters` | `RecipeParameter[]` | — |  |
| `dbType` | `'sqlite' | 'beanquery'` | — | Query engine override (defaults to dashboard/view setting). |
| `query` | `string` | yes | SQL SELECT (SQLite). Use :paramName for parameter placeholders. |
| `transform` | `Transform` | — |  |
| `visualization` | `JsonRecipeVisualization` | yes |  |

#### `ChartType`

Type: `'bar' | 'line' | 'pie' | 'area' | 'scatter' | 'treemap' | 'funnel' | 'gauge' | 'calendar' | 'sankey' | 'radar' | 'sunburst'`

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

#### `Transform`

Type: `'none' | 'firstRow' | 'firstValue' | TransformConfig`

#### `TransformConfig`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `'sortBy' | 'limit' | 'pluck' | 'pivot'` | yes |  |
| `field` | `string` | — | For sortBy or pluck. |
| `order` | `'asc' | 'desc'` | — |  |
| `count` | `number` | — | For limit transform. |
| `rowField` | `string` | — | For pivot — column whose values become row labels. |
| `columnField` | `string` | — | For pivot — column whose values become column headers. |
| `valueField` | `string` | — | For pivot — column containing cell values. |
| `formatColumn` | `'monthYear' | 'yearMonth'` | — |  |
| `sortRowsBy` | `'total_desc' | 'total_asc' | 'label_asc' | 'label_desc'` | — |  |

#### `ValueFormat`
Predefined value formatter applied at render time.

Type: `'currency' | 'percent' | 'number' | 'compact' | 'signedCurrency' | 'date' | 'dateShort' | 'accountName' | 'accountName2'`

#### `WidgetLayout`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `widgetId` | `string` | yes |  |
| `gridArea` | `string` | yes | CSS grid-area: 'row-start / col-start / row-end / col-end' (1-based, e.g. '1 / 1 / 2 / 4'). |

<!-- END AUTO-GENERATED -->
