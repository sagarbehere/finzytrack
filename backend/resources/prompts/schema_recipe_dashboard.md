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
    "options": { "$gen": "yearRange", "count": 6 }
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
- Dashboard-level parameters cascade to all widgets. Widget-level parameters override dashboard ones.
- In the query, reference as `:year`, `:currency`, `:limit`, etc.

### Generators ($gen)

Use generators for dynamic default values and option lists:

| Generator | Output | Example usage |
|-----------|--------|---------------|
| `currentYear` | Current year number | `{ "$gen": "currentYear" }` |
| `currentMonth` | Current month (1-12) | `{ "$gen": "currentMonth" }` |
| `yearRange` | Array of year options | `{ "$gen": "yearRange", "count": 5 }` |
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

### Format strings

Available for `seriesLabelFormat`, `yAxisLabelFormat`, `xAxisLabelFormat`, and KPI `format`:

| Format | Output | Use for |
|--------|--------|---------|
| `"currency"` | $14,200 | Dollar amounts |
| `"compact"` | 14.2k | Large numbers |
| `"compactCurrency"` | $14.2k | Large currency amounts |
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

### Complete single-widget dashboard example

For a user request like "show me a bar chart of my top 10 expense categories":

```json
{
  "id": "top-expenses-bar",
  "title": "Top Expense Categories",
  "description": "Bar chart showing highest expense categories",
  "parameters": [
    {
      "name": "year",
      "label": "Year",
      "type": "select",
      "default": { "$gen": "currentYear" },
      "options": { "$gen": "yearRange", "count": 5 }
    },
    {
      "name": "currency",
      "label": "Currency",
      "type": "select",
      "default": { "$gen": "defaultCurrency" },
      "optionsFrom": "currencies"
    }
  ],
  "layout": {
    "columns": 6,
    "gap": "1.5rem",
    "rowHeight": "200px",
    "widgets": [
      { "widgetId": "top-expenses-chart", "gridArea": "1 / 1 / 4 / 7" }
    ]
  },
  "widgets": [
    {
      "id": "top-expenses-chart",
      "title": "Top 10 Expense Categories",
      "query": "SELECT REPLACE(account, 'Expenses:', '') AS name, account, SUM(amount) AS total FROM postings WHERE account_type = 'Expenses' AND year = :year AND currency = :currency GROUP BY account HAVING total > 0 ORDER BY total DESC LIMIT 10",
      "visualization": {
        "type": "chart",
        "chartType": "bar",
        "seriesLabelFormat": "currency",
        "xAxisLabelFormat": "compact",
        "yAxisLabelFormat": "accountName",
        "options": {
          "grid": { "left": 120, "right": 24, "top": 16, "bottom": 16 },
          "xAxis": { "type": "value" },
          "yAxis": { "type": "category", "axisLabel": { "width": 100, "overflow": "truncate" } },
          "series": [
            {
              "name": "Amount",
              "type": "bar",
              "encode": { "x": "total", "y": "name" },
              "itemStyle": { "color": "#6366f1" },
              "label": { "show": true, "position": "right" }
            }
          ]
        },
        "clickLink": {
          "name": "transactions",
          "query": {
            "accountContains": "{{data.account}}",
            "dateFrom": "{{parameters.year}}-01-01",
            "dateTo": "{{parameters.year}}-12-31"
          }
        }
      }
    }
  ]
}
```

### Multi-widget dashboard example

For "show me a monthly overview with income, expenses, and a spending breakdown":

Use 12-column grid. Row 1: KPI cards (3 cols each). Rows 2-4: full-width chart. Rows 5-8: pivot.
See the existing `year-summary` or `month-summary` dashboards via `read_recipe` for reference.
