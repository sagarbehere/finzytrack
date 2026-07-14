<template>
  <!-- Shape guard (G1): the output step's data must match the viz's expected
       shape. On mismatch, show a clear message instead of a blank panel. -->
  <div
    v-if="vizInputError"
    class="flex h-full items-center justify-center p-4 text-center text-sm text-amber-700 dark:text-amber-400"
  >
    {{ vizInputError }}
  </div>

  <!-- KPI -->
  <RecipeKPI
    v-else-if="recipe.visualization.type === 'kpi'"
    :value="getKPIValue()"
    :icon="getKPIIcon()"
    :iconColor="getKPIIconColor()"
    :formatValue="getKPIFormatFunction()"
    :showTrend="recipe.visualization.showTrend"
    :trend="getTrendValue()"
    :values="getKPIValues()"
    :colorBySign="isJsonKPIVisualization(recipe.visualization) && !!recipe.visualization.colorBySign"
  />

  <!-- Chart -->
  <RecipeChart
    v-else-if="recipe.visualization.type === 'chart'"
    :chartOptions="recipe.visualization.options ?? {}"
    :data="Array.isArray(data) ? data : []"
    :clickable="hasChartClickHandler()"
    :currency="currencyParam"
    :seriesLabelFormat="getChartSeriesLabelFormat()"
    :yAxisLabelFormat="getChartYAxisLabelFormat()"
    :xAxisLabelFormat="getChartXAxisLabelFormat()"
    class="h-full"
    @series-click="handleChartSeriesClick"
  />

  <!-- Table -->
  <RecipeTable
    v-else-if="recipe.visualization.type === 'table'"
    :data="Array.isArray(data) ? data : []"
    :columns="getTableColumns()"
    :emptyText="getTableEmptyText()"
    @select="emit('select', $event)"
  />

  <!-- Pivot Table -->
  <RecipePivotTable
    v-else-if="recipe.visualization.type === 'pivot'"
    :data="getPivotData()"
    :rowHeader="recipe.visualization.rowHeader"
    :valueFormat="getPivotValueFormat()"
    :showRowTotals="recipe.visualization.showRowTotals"
    :showColumnTotals="recipe.visualization.showColumnTotals"
    :getValueLink="getPivotGetValueLink()"
    :getValueSelect="getPivotGetValueSelect()"
    :colorByValue="isJsonPivot(recipe.visualization) ? recipe.visualization.colorByValue : undefined"
    :warnAt="isJsonPivot(recipe.visualization) ? recipe.visualization.warnAt : undefined"
    :colors="isJsonPivot(recipe.visualization) ? recipe.visualization.colors : undefined"
    @select="emit('select', $event)"
  />

  <!-- Budget progress -->
  <RecipeBudgetProgress
    v-else-if="recipe.visualization.type === 'budget-progress'"
    :rows="Array.isArray(data) ? (data as Record<string, unknown>[]) : []"
    :fields="getBudgetProgressFields()"
    :accountFormat="getBudgetProgressAccountFormat()"
    :getRowLink="getBudgetProgressRowLink()"
    :getRowSelect="getBudgetProgressRowSelect()"
    :activeParams="getBudgetProgressActiveParams()"
    :emptyText="recipe.visualization.emptyText"
    :warnAt="isJsonBudgetProgress(recipe.visualization) ? recipe.visualization.warnAt : undefined"
    :colors="isJsonBudgetProgress(recipe.visualization) ? recipe.visualization.colors : undefined"
    @select="emit('select', $event)"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import type {
  KPIVisualization,
  JsonKPIVisualization,
  ChartVisualization,
  ChartClickContext,
  TableColumn,
  TableLinkContext,
  JsonTableColumn,
  JsonValueLinkConfig,
  JsonPivotVisualization,
  ValueFormat,
  PivotData,
  PivotVisualization,
  PivotLinkContext,
  ValueLinkConfig,
  CurrencyAmount,
  JsonBudgetProgressVisualization,
  JsonSelectLinkConfig,
  SelectParams,
} from '@/types/recipes'
import {
  type AnyWidgetRecipe,
  getFormats,
} from '@/composables/useRecipeExecutor'
import { validateVizInput } from '@/recipes/vizRegistry'
import { resolvePath, interpolateString } from '@/recipes/templating'
import RecipeChart from './RecipeChart.vue'
import RecipeKPI from './RecipeKPI.vue'
import RecipeTable from './RecipeTable.vue'
import RecipePivotTable from './RecipePivotTable.vue'
import RecipeBudgetProgress, { type BudgetProgressFields } from './RecipeBudgetProgress.vue'

interface Props {
  recipe: AnyWidgetRecipe
  data: unknown
  mergedParameters: Record<string, string | number>
}

const props = defineProps<Props>()

// A "select" click action bubbles the resolved dashboard params up to
// RecipeDashboard, which merges them into its selections (master-detail).
const emit = defineEmits<{ select: [params: SelectParams] }>()

const router = useRouter()

const currencyParam = computed<string | undefined>(() => {
  const v = props.mergedParameters.currency
  return typeof v === 'string' ? v : undefined
})

const formats = computed(() => getFormats(currencyParam.value))

// G1 shape guard: validate the output step's data against the viz's expected
// shape (§4.5b). Skipped while data is still null/undefined (loading).
const vizInputError = computed<string | null>(() => {
  if (props.data === null || props.data === undefined) return null
  return validateVizInput(props.recipe.visualization, props.data)
})

// Check if visualization is a JSON KPI (has format string instead of formatValue function)
function isJsonKPIVisualization(
  viz: KPIVisualization | JsonKPIVisualization
): viz is JsonKPIVisualization {
  // JSON KPI viz is identified by any of its JSON-only fields. `amountField` /
  // `currencyField` / `iconColor` matter as much as `format` / `valueField`:
  // multi-currency KPIs (e.g. the budget dashboards) set the former but not the
  // latter, and omitting them here made amountField silently default to
  // 'amount', zeroing every value.
  return (
    'format' in viz ||
    'valueField' in viz ||
    'amountField' in viz ||
    'currencyField' in viz ||
    'iconColor' in viz
  )
}

// Helper to extract KPI value from data
function getKPIValue(): number {
  if (props.data === null) return 0

  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return 0

  // Check for valueField in JSON KPI
  if (isJsonKPIVisualization(viz) && viz.valueField) {
    if (typeof props.data === 'object' && props.data !== null) {
      const obj = props.data as Record<string, unknown>
      if (viz.valueField in obj) {
        const val = obj[viz.valueField]
        if (typeof val === 'number') return val
      }
    }
  }

  // Fallback: extract value from various data shapes
  if (typeof props.data === 'number') return props.data
  if (Array.isArray(props.data) && props.data.length > 0) {
    const firstRow = props.data[0]
    for (const value of Object.values(firstRow)) {
      if (typeof value === 'number') return value
    }
  }
  if (typeof props.data === 'object' && props.data !== null) {
    const obj = props.data as Record<string, unknown>
    if ('value' in obj && typeof obj.value === 'number') return obj.value
    for (const value of Object.values(obj)) {
      if (typeof value === 'number') return value
    }
  }
  return 0
}

// Helper to extract multi-currency KPI values from data
function getKPIValues(): CurrencyAmount[] | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return undefined
  if (!viz.multiCurrency) return undefined
  if (props.data === null) return undefined

  // For TypeScript recipes: transform returns CurrencyAmount[] directly
  if (Array.isArray(props.data)) {
    const amountField = (isJsonKPIVisualization(viz) && viz.amountField) || 'amount'
    const currencyField = (isJsonKPIVisualization(viz) && viz.currencyField) || 'currency'

    return props.data
      .map((row: Record<string, unknown>) => ({
        amount: Number(row[amountField]) || 0,
        currency: String(row[currencyField] || 'USD'),
      }))
      .filter((item: CurrencyAmount) => item.amount !== 0)
  }

  return undefined
}

// Get KPI icon
function getKPIIcon(): string | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return undefined
  return viz.icon
}

// Get KPI icon color (for JSON recipes)
function getKPIIconColor(): 'blue' | 'green' | 'red' | 'purple' | 'amber' {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return 'blue'
  if (isJsonKPIVisualization(viz) && viz.iconColor) {
    return viz.iconColor
  }
  return 'blue'
}

// Get format function for KPI (handles both TypeScript and JSON recipes)
function getKPIFormatFunction(): ((value: number) => string) | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return undefined

  // TypeScript recipe with formatValue function
  if ('formatValue' in viz && typeof viz.formatValue === 'function') {
    return viz.formatValue as (value: number) => string
  }

  // JSON recipe with format string
  if (isJsonKPIVisualization(viz) && viz.format) {
    return formats.value[viz.format]
  }

  return undefined
}

// Helper to extract trend value
function getTrendValue(): number | null {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return null
  if (!viz.showTrend || !viz.trendField) return null
  if (typeof props.data === 'object' && props.data !== null) {
    const obj = props.data as Record<string, unknown>
    if (viz.trendField in obj) {
      const trend = obj[viz.trendField]
      if (typeof trend === 'number') return trend
    }
  }
  return null
}

/**
 * Resolve a JSON template link config into a ValueLinkConfig by interpolating
 * click-context variables like {{row.fieldName}}, {{column}}, {{value}}. Shares
 * the {{...}} walker with the executor/validator (@/recipes/templating, G6);
 * only the scope differs (click vars here vs step outputs at execution time).
 */
function resolveTemplateLink(
  template: JsonValueLinkConfig,
  vars: Record<string, unknown>,
): ValueLinkConfig | null {
  // A select action is not a route — callers handle it via resolveSelectParams.
  if (isSelectLink(template)) return null
  const query: Record<string, string> = {}
  for (const [key, tmpl] of Object.entries(template.query)) {
    query[key] = interpolateString(tmpl, (path) => resolvePath(path, vars))
  }
  return { name: template.name, query }
}

/** A link config is a "select" action (writes dashboard params) vs a route. */
function isSelectLink(template: JsonValueLinkConfig): template is JsonSelectLinkConfig {
  return 'select' in template
}

/**
 * Resolve a select link's { param: template } map into concrete { param: value }
 * by interpolating the click-context vars ({{row.*}}, {{data.*}}, {{parameters.*}}).
 */
function resolveSelectParams(
  template: JsonSelectLinkConfig,
  vars: Record<string, unknown>,
): SelectParams {
  const out: SelectParams = {}
  for (const [param, tmpl] of Object.entries(template.select)) {
    out[param] = interpolateString(tmpl, (path) => resolvePath(path, vars))
  }
  return out
}

// Get table columns (handles both TypeScript and JSON recipes)
function getTableColumns(): TableColumn[] {
  const viz = props.recipe.visualization
  if (viz.type !== 'table') return []

  // Convert JSON table columns to TableColumn format
  return viz.columns.map((col: TableColumn | JsonTableColumn) => {
    if ('format' in col && typeof col.format === 'string') {
      // JSON column with format string (and optional template link)
      const jsonCol = col as JsonTableColumn
      const result: TableColumn = {
        key: jsonCol.key,
        label: jsonCol.label,
        align: jsonCol.align,
        format: jsonCol.format
          ? (value: unknown) => {
              // Format numbers and numeric (Money) strings; Money flows through
              // the data path as a decimal string and is display-formatted here.
              if (typeof value === 'number') {
                return formats.value[jsonCol.format as ValueFormat](value)
              }
              if (typeof value === 'string' && value.trim() !== '' && !Number.isNaN(Number(value))) {
                return formats.value[jsonCol.format as ValueFormat](Number(value))
              }
              return String(value ?? '—')
            }
          : undefined,
      }
      if (jsonCol.link) {
        const linkTemplate = jsonCol.link
        const scope = (context: TableLinkContext) => ({
          row: context.row,
          value: context.value,
          column: context.column.key,
          parameters: props.mergedParameters,
        })
        // A grand-total / summary row (isTotal) isn't a real entity — no action.
        result.getLink = (context) =>
          context.row?.isTotal ? null : resolveTemplateLink(linkTemplate, scope(context))
        if (isSelectLink(linkTemplate)) {
          result.getSelect = (context) =>
            context.row?.isTotal ? null : resolveSelectParams(linkTemplate, scope(context))
        }
      }
      return result
    }
    // Check for JSON column without format but with a link/select action
    if ('link' in col && col.link && typeof col.link === 'object' && ('name' in col.link || 'select' in col.link)) {
      const jsonCol = col as JsonTableColumn
      const result: TableColumn = {
        key: jsonCol.key,
        label: jsonCol.label,
        align: jsonCol.align,
      }
      const linkTemplate = jsonCol.link!
      const scope = (context: TableLinkContext) => ({
        row: context.row,
        value: context.value,
        column: context.column.key,
        parameters: props.mergedParameters,
      })
      result.getLink = (context) =>
        context.row?.isTotal ? null : resolveTemplateLink(linkTemplate, scope(context))
      if (isSelectLink(linkTemplate)) {
        result.getSelect = (context) =>
          context.row?.isTotal ? null : resolveSelectParams(linkTemplate, scope(context))
      }
      return result
    }
    // TypeScript column with function
    return col as TableColumn
  })
}

// Custom empty-state message for a table viz (JSON recipes).
function getTableEmptyText(): string | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'table') return undefined
  return (viz as { emptyText?: string }).emptyText
}

// Get pivot data (transform should return PivotData shape)
function getPivotData(): PivotData {
  if (props.data && typeof props.data === 'object' && 'columns' in props.data && 'rows' in props.data) {
    return props.data as PivotData
  }
  // Return empty pivot data if transform didn't return proper shape
  return { columns: [], rows: [] }
}

// Get pivot value format function
function getPivotValueFormat(): ((value: number) => string) | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'pivot') return undefined

  // TypeScript recipe with valueFormat function
  if ('valueFormat' in viz && typeof viz.valueFormat === 'function') {
    return viz.valueFormat as (value: number) => string
  }

  // JSON recipe with format string
  if ('format' in viz && typeof viz.format === 'string') {
    return (value: number) => formats.value[viz.format as ValueFormat](value)
  }

  return undefined
}

// Get pivot getValueLink function
function getPivotGetValueLink(): ((context: PivotLinkContext) => ValueLinkConfig | null | undefined) | undefined {
  const viz = props.recipe.visualization as PivotVisualization
  if (viz.type !== 'pivot') return undefined

  // TypeScript recipe with getValueLink function
  if ('getValueLink' in viz && typeof viz.getValueLink === 'function') {
    return viz.getValueLink
  }

  // JSON recipe with valueLink template
  const jsonViz = viz as unknown as JsonPivotVisualization
  if (jsonViz.valueLink) {
    const linkTemplate = jsonViz.valueLink
    return (context: PivotLinkContext) => {
      // Expose columnMeta for the clicked column so templates can use
      // {{columnMeta.startDate}}, {{columnMeta.endDate}}, {{columnMeta.rawValue}}, etc.
      const pivotData = props.data as PivotData | null
      const colMeta = pivotData?.columnMeta?.[context.columnIndex] ?? {}
      return resolveTemplateLink(linkTemplate, {
        row: {
          label: context.rowLabel,
          ...context.rowData.meta,
        },
        column: context.column,
        columnIndex: context.columnIndex,
        value: context.value,
        columnMeta: colMeta,
      })
    }
  }

  return undefined
}

// Pivot cell "select" action (parallel to getPivotGetValueLink for navigation).
function getPivotGetValueSelect(): ((context: PivotLinkContext) => SelectParams | null) | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'pivot') return undefined
  const jsonViz = viz as unknown as JsonPivotVisualization
  if (!jsonViz.valueLink || !isSelectLink(jsonViz.valueLink)) return undefined
  const linkTemplate = jsonViz.valueLink
  return (context: PivotLinkContext) => {
    const pivotData = props.data as PivotData | null
    const colMeta = pivotData?.columnMeta?.[context.columnIndex] ?? {}
    return resolveSelectParams(linkTemplate, {
      row: { label: context.rowLabel, ...context.rowData.meta },
      column: context.column,
      columnIndex: context.columnIndex,
      value: context.value,
      columnMeta: colMeta,
    })
  }
}

function isJsonPivot(viz: { type?: string }): viz is JsonPivotVisualization {
  return viz.type === 'pivot'
}

// ── budget-progress helpers ─────────────────────────────────────────────────
function isJsonBudgetProgress(
  viz: { type?: string },
): viz is JsonBudgetProgressVisualization {
  return viz.type === 'budget-progress'
}

// Field-name mapping (defaults match the joinBudgetActual flat output).
function getBudgetProgressFields(): BudgetProgressFields {
  const viz = props.recipe.visualization
  const v = viz.type === 'budget-progress' ? (viz as JsonBudgetProgressVisualization) : undefined
  return {
    account: v?.accountField ?? 'account',
    budget: v?.budgetField ?? 'budget',
    actual: v?.actualField ?? 'actual',
    remaining: v?.remainingField ?? 'remaining',
    pctUsed: v?.pctUsedField ?? 'pctUsed',
    currency: v?.currencyField ?? 'currency',
    direction: v?.directionField ?? 'direction',
  }
}

function getBudgetProgressAccountFormat(): ((value: unknown) => string) | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'budget-progress' || !viz.accountFormat) return undefined
  return formats.value[viz.accountFormat as ValueFormat]
}

function getBudgetProgressRowLink(): ((row: Record<string, unknown>) => RouteLocationRaw | null) | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'budget-progress' || !viz.link || isSelectLink(viz.link)) return undefined
  const linkTemplate = viz.link
  return (row: Record<string, unknown>) => {
    // Synthetic rows (e.g. the remainder "Unbudgeted"/"Total" rows) aren't real
    // accounts — no click-through.
    if (row.noLink) return null
    // Scope: the row's fields ({{row.account}}, …) plus the dashboard params
    // ({{parameters.monthStart}}, {{parameters.year}}-01-01, …).
    const cfg = resolveTemplateLink(linkTemplate, { row, parameters: props.mergedParameters })
    return cfg ? { name: cfg.name, query: cfg.query } : null
  }
}

// Budget-progress row "select" action (drives a drill-down widget on click).
function getBudgetProgressRowSelect(): ((row: Record<string, unknown>) => SelectParams | null) | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'budget-progress' || !viz.link || !isSelectLink(viz.link)) return undefined
  const linkTemplate = viz.link
  return (row: Record<string, unknown>) =>
    resolveSelectParams(linkTemplate, { row, parameters: props.mergedParameters })
}

/**
 * The dashboard-parameter values a budget-progress "select" writes for the
 * currently-highlighted row — used to mark the active (drilled-in) row. We
 * compare the *current* param values against what each row would select, so the
 * row that matches the live selection is highlighted. Returns the target
 * param→value map of the live selection, or null when the viz doesn't select.
 */
function getBudgetProgressActiveParams(): SelectParams | null {
  const viz = props.recipe.visualization
  if (viz.type !== 'budget-progress' || !viz.link || !isSelectLink(viz.link)) return null
  const active: SelectParams = {}
  for (const param of Object.keys(viz.link.select)) {
    const v = props.mergedParameters[param]
    if (v !== undefined) active[param] = String(v)
  }
  return active
}

// Check if chart visualization has a click handler
function hasChartClickHandler(): boolean {
  const viz = props.recipe.visualization
  if (viz.type !== 'chart') return false
  const chartViz = viz as ChartVisualization
  return (
    typeof chartViz.getSeriesClickLink === 'function' ||
    !!chartViz.clickLink ||
    !!chartViz.seriesClickLinks
  )
}

// Helpers to pass label/axis format props to RecipeChart
function getChartSeriesLabelFormat(): ValueFormat | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'chart') return undefined
  return (viz as ChartVisualization).seriesLabelFormat
}

function getChartYAxisLabelFormat(): ValueFormat | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'chart') return undefined
  return (viz as ChartVisualization).yAxisLabelFormat
}

function getChartXAxisLabelFormat(): ValueFormat | undefined {
  const viz = props.recipe.visualization
  if (viz.type !== 'chart') return undefined
  return (viz as ChartVisualization).xAxisLabelFormat
}

// Handle chart series click events
function handleChartSeriesClick(clickData: { seriesName: string; seriesIndex: number; dataIndex: number; data: Record<string, unknown> }) {
  const viz = props.recipe.visualization
  if (viz.type !== 'chart') return

  const chartViz = viz as ChartVisualization

  // TypeScript recipe with function-based click handler
  if (typeof chartViz.getSeriesClickLink === 'function') {
    const context: ChartClickContext = {
      ...clickData,
      parameters: props.mergedParameters,
    }
    const link = chartViz.getSeriesClickLink(context)
    if (link) {
      router.push({ name: link.name, query: link.query })
    }
    return
  }

  // JSON recipe with template-based click link (global or per-series)
  if (chartViz.clickLink || chartViz.seriesClickLinks) {
    const scope = {
      data: clickData.data,
      seriesName: clickData.seriesName,
      parameters: props.mergedParameters,
    }
    // Per-series config takes precedence; null means explicitly no link for this series
    if (chartViz.seriesClickLinks && clickData.seriesName in chartViz.seriesClickLinks) {
      const seriesLink = chartViz.seriesClickLinks[clickData.seriesName]
      if (seriesLink === null) return // Explicitly disabled for this series
      runLinkAction(seriesLink, scope)
      return
    }
    // Fall back to global clickLink
    if (chartViz.clickLink) runLinkAction(chartViz.clickLink, scope)
  }
}

/** Dispatch a resolved link action: emit a select, or navigate to a route. */
function runLinkAction(template: JsonValueLinkConfig, vars: Record<string, unknown>) {
  if (isSelectLink(template)) {
    emit('select', resolveSelectParams(template, vars))
    return
  }
  const link = resolveTemplateLink(template, vars)
  if (link) router.push({ name: link.name, query: link.query })
}

// Check if KPI visualization has a click link
function hasKPIClickLink(): boolean {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return false
  return !!(viz as KPIVisualization | JsonKPIVisualization).clickLink
}

// Handle KPI click
function handleKPIClick() {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return

  const clickLink = (viz as KPIVisualization | JsonKPIVisualization).clickLink
  if (!clickLink) return

  // Compute date convenience vars from year+month parameters.
  // When only year is present (no month), span the full calendar year.
  const params = props.mergedParameters
  const year = String(params.year || new Date().getFullYear())
  let dateFrom: string
  let dateTo: string
  if (params.month) {
    const month = String(params.month).padStart(2, '0')
    const lastDay = new Date(Number(year), Number(params.month), 0).getDate()
    dateFrom = `${year}-${month}-01`
    dateTo = `${year}-${month}-${String(lastDay).padStart(2, '0')}`
  } else {
    dateFrom = `${year}-01-01`
    dateTo = `${year}-12-31`
  }

  runLinkAction(clickLink, { parameters: params, dateFrom, dateTo })
}

defineExpose({ hasKPIClickLink, handleKPIClick })
</script>
