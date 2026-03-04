<template>
  <div
    :class="[
      'bg-white dark:bg-gray-800 overflow-hidden shadow rounded-lg border dark:border-gray-700 flex flex-col h-full',
      hasKPIClickLink() ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors' : '',
    ]"
    @click="hasKPIClickLink() && handleKPIClick()"
  >
    <!-- Header -->
    <div
      class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between"
    >
      <h3 class="text-sm font-medium text-gray-900 dark:text-white flex items-center gap-1.5">
        {{ recipe.title }}
        <span v-if="getHelpText()" class="group relative">
          <span class="text-gray-400 dark:text-gray-500 text-xs cursor-help select-none">ⓘ</span>
          <span class="invisible group-hover:visible absolute left-0 top-full mt-1 z-10 px-2.5 py-1.5 text-xs font-normal text-white bg-gray-800 dark:bg-gray-900 rounded shadow-lg w-64">
            {{ getHelpText() }}
          </span>
        </span>
      </h3>
      <!-- Widget-level parameters (those not already provided by dashboard) -->
      <RecipeParameters
        v-if="widgetOnlyParameters.length > 0"
        :parameters="widgetOnlyParameters"
        v-model="localParameters"
        class="ml-4"
      />
    </div>

    <!-- Content -->
    <div class="flex-1 p-4 min-h-0 overflow-hidden">
      <!-- Loading state -->
      <div
        v-if="isLoading"
        class="h-full flex items-center justify-center text-gray-400 dark:text-gray-500"
      >
        <svg
          class="animate-spin h-8 w-8"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            class="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            stroke-width="4"
          ></circle>
          <path
            class="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          ></path>
        </svg>
      </div>

      <!-- Error state -->
      <div
        v-else-if="error"
        class="h-full flex items-center justify-center text-red-500 dark:text-red-400"
      >
        <div class="text-center">
          <p class="text-sm">{{ error }}</p>
          <button
            @click="executeQuery"
            class="mt-2 text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400"
          >
            Retry
          </button>
        </div>
      </div>

      <!-- Visualization -->
      <template v-else-if="data !== null">
        <!-- KPI -->
        <RecipeKPI
          v-if="recipe.visualization.type === 'kpi'"
          :value="getKPIValue()"
          :icon="getKPIIcon()"
          :iconColor="getKPIIconColor()"
          :formatValue="getKPIFormatFunction()"
          :showTrend="recipe.visualization.showTrend"
          :trend="getTrendValue()"
          :values="getKPIValues()"
        />

        <!-- Chart -->
        <RecipeChart
          v-else-if="recipe.visualization.type === 'chart'"
          :chartOptions="recipe.visualization.options"
          :data="Array.isArray(data) ? data : []"
          :clickable="hasChartClickHandler()"
          class="h-full"
          @series-click="handleChartSeriesClick"
        />

        <!-- Table -->
        <RecipeTable
          v-else-if="recipe.visualization.type === 'table'"
          :data="Array.isArray(data) ? data : []"
          :columns="getTableColumns()"
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
        />
      </template>

      <!-- Empty state -->
      <div
        v-else
        class="h-full flex items-center justify-center text-gray-400 dark:text-gray-500"
      >
        <p class="text-sm">No data</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import type {
  KPIVisualization,
  JsonKPIVisualization,
  ChartVisualization,
  ChartClickContext,
  TableColumn,
  JsonTableColumn,
  JsonValueLinkConfig,
  JsonPivotVisualization,
  ValueFormat,
  PivotData,
  PivotVisualization,
  PivotLinkContext,
  ValueLinkConfig,
  CurrencyAmount,
} from '@/types/recipes'
import {
  useRecipeExecutor,
  type AnyWidgetRecipe,
  predefinedFormats,
} from '@/composables/useRecipeExecutor'
import RecipeChart from './RecipeChart.vue'
import RecipeKPI from './RecipeKPI.vue'
import RecipeTable from './RecipeTable.vue'
import RecipePivotTable from './RecipePivotTable.vue'
import RecipeParameters from './RecipeParameters.vue'

interface Props {
  recipe: AnyWidgetRecipe
  dashboardParameters?: Record<string, string | number>
}

const props = defineProps<Props>()

const router = useRouter()
const { executeRecipe, getDefaultParameters, isLoading, error } = useRecipeExecutor()

// Local parameters (for widget-level params not controlled by dashboard)
const localParameters = ref<Record<string, string | number>>({})

// Widget parameters that are NOT provided by the dashboard (shown in widget header)
const widgetOnlyParameters = computed(() => {
  if (!props.recipe.parameters) return []
  if (!props.dashboardParameters) return props.recipe.parameters
  return props.recipe.parameters.filter((p) => !(p.name in props.dashboardParameters!))
})

// Merged parameters: dashboard params override local params
const mergedParameters = computed(() => ({
  ...getDefaultParameters(props.recipe),
  ...localParameters.value,
  ...(props.dashboardParameters || {}),
}))

// Data from query execution
const data = ref<unknown>(null)

// Execute query
async function executeQuery() {
  try {
    data.value = await executeRecipe(props.recipe, mergedParameters.value, props.recipe.dbType)
  } catch {
    // Error is already set by useRecipeExecutor
  }
}

// Check if visualization is a JSON KPI (has format string instead of formatValue function)
function isJsonKPIVisualization(
  viz: KPIVisualization | JsonKPIVisualization
): viz is JsonKPIVisualization {
  return 'format' in viz || 'valueField' in viz
}

// Helper to extract KPI value from data
function getKPIValue(): number {
  if (data.value === null) return 0

  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return 0

  // Check for valueField in JSON KPI
  if (isJsonKPIVisualization(viz) && viz.valueField) {
    if (typeof data.value === 'object' && data.value !== null) {
      const obj = data.value as Record<string, unknown>
      if (viz.valueField in obj) {
        const val = obj[viz.valueField]
        if (typeof val === 'number') return val
      }
    }
  }

  // Fallback: extract value from various data shapes
  if (typeof data.value === 'number') return data.value
  if (Array.isArray(data.value) && data.value.length > 0) {
    const firstRow = data.value[0]
    for (const value of Object.values(firstRow)) {
      if (typeof value === 'number') return value
    }
  }
  if (typeof data.value === 'object' && data.value !== null) {
    const obj = data.value as Record<string, unknown>
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
  if (data.value === null) return undefined

  // For TypeScript recipes: transform returns CurrencyAmount[] directly
  if (Array.isArray(data.value)) {
    const amountField = (isJsonKPIVisualization(viz) && viz.amountField) || 'amount'
    const currencyField = (isJsonKPIVisualization(viz) && viz.currencyField) || 'currency'

    return data.value
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

// Get widget help text
function getHelpText(): string | undefined {
  return props.recipe.helpText
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
    return viz.formatValue
  }

  // JSON recipe with format string
  if (isJsonKPIVisualization(viz) && viz.format) {
    return predefinedFormats[viz.format]
  }

  return undefined
}

// Helper to extract trend value
function getTrendValue(): number | null {
  const viz = props.recipe.visualization
  if (viz.type !== 'kpi') return null
  if (!viz.showTrend || !viz.trendField) return null
  if (typeof data.value === 'object' && data.value !== null) {
    const obj = data.value as Record<string, unknown>
    if (viz.trendField in obj) {
      const trend = obj[viz.trendField]
      if (typeof trend === 'number') return trend
    }
  }
  return null
}

/**
 * Resolve a JSON template link config into a ValueLinkConfig by interpolating
 * template variables like {{row.fieldName}}, {{column}}, {{value}}.
 */
function resolveTemplateLink(
  template: JsonValueLinkConfig,
  vars: Record<string, unknown>,
): ValueLinkConfig | null {
  const query: Record<string, string> = {}
  for (const [key, tmpl] of Object.entries(template.query)) {
    query[key] = tmpl.replace(/\{\{(\w+(?:\.\w+)*)\}\}/g, (_, path: string) => {
      const parts = path.split('.')
      let current: unknown = vars
      for (const part of parts) {
        if (current === null || current === undefined || typeof current !== 'object') return ''
        current = (current as Record<string, unknown>)[part]
      }
      return current === null || current === undefined ? '' : String(current)
    })
  }
  return { name: template.name, query }
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
              if (typeof value === 'number') {
                return predefinedFormats[jsonCol.format as ValueFormat](value)
              }
              return String(value ?? '—')
            }
          : undefined,
      }
      if (jsonCol.link) {
        const linkTemplate = jsonCol.link
        result.getLink = (context) =>
          resolveTemplateLink(linkTemplate, {
            row: context.row,
            value: context.value,
            column: context.column.key,
          })
      }
      return result
    }
    // Check for JSON column without format but with link
    if ('link' in col && col.link && typeof col.link === 'object' && 'name' in col.link) {
      const jsonCol = col as JsonTableColumn
      const result: TableColumn = {
        key: jsonCol.key,
        label: jsonCol.label,
        align: jsonCol.align,
      }
      const linkTemplate = jsonCol.link!
      result.getLink = (context) =>
        resolveTemplateLink(linkTemplate, {
          row: context.row,
          value: context.value,
          column: context.column.key,
        })
      return result
    }
    // TypeScript column with function
    return col as TableColumn
  })
}

// Get pivot data (transform should return PivotData shape)
function getPivotData(): PivotData {
  if (data.value && typeof data.value === 'object' && 'columns' in data.value && 'rows' in data.value) {
    return data.value as PivotData
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
    return viz.valueFormat
  }

  // JSON recipe with format string
  if ('format' in viz && typeof viz.format === 'string') {
    return (value: number) => predefinedFormats[viz.format as ValueFormat](value)
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
    return (context: PivotLinkContext) =>
      resolveTemplateLink(linkTemplate, {
        row: {
          label: context.rowLabel,
          ...context.rowData.meta,
        },
        column: context.column,
        columnIndex: context.columnIndex,
        value: context.value,
      })
  }

  return undefined
}

// Check if chart visualization has a click handler
function hasChartClickHandler(): boolean {
  const viz = props.recipe.visualization
  if (viz.type !== 'chart') return false
  const chartViz = viz as ChartVisualization
  return typeof chartViz.getSeriesClickLink === 'function' || !!chartViz.clickLink
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
      parameters: mergedParameters.value,
    }
    const link = chartViz.getSeriesClickLink(context)
    if (link) {
      router.push({ name: link.name, query: link.query })
    }
    return
  }

  // JSON recipe with template-based click link
  if (chartViz.clickLink) {
    const link = resolveTemplateLink(chartViz.clickLink, {
      data: clickData.data,
      seriesName: clickData.seriesName,
      parameters: mergedParameters.value,
    })
    if (link) {
      router.push({ name: link.name, query: link.query })
    }
  }
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

  // Compute date convenience vars from year+month parameters
  const params = mergedParameters.value
  const year = String(params.year || new Date().getFullYear())
  const month = String(params.month || new Date().getMonth() + 1).padStart(2, '0')
  const lastDay = new Date(Number(year), Number(month), 0).getDate()
  const dateFrom = `${year}-${month}-01`
  const dateTo = `${year}-${month}-${String(lastDay).padStart(2, '0')}`

  const link = resolveTemplateLink(clickLink, {
    parameters: params,
    dateFrom,
    dateTo,
  })

  if (link) {
    router.push({ name: link.name, query: link.query })
  }
}

// Re-execute when parameters change
watch(mergedParameters, () => executeQuery(), { deep: true })

// Persist widget-level parameter selections when they change
watch(localParameters, (newParams) => {
  if (!props.recipe.id || !props.recipe.parameters?.length) return
  const all = getStorageAdapter().get<Record<string, Record<string, string | number>>>(STORAGE_KEYS.WIDGET_SETTINGS) ?? {}
  all[props.recipe.id] = newParams
  getStorageAdapter().set(STORAGE_KEYS.WIDGET_SETTINGS, all)
}, { deep: true })

// Initial execution — restore saved parameters if available
onMounted(() => {
  const defaults = getDefaultParameters(props.recipe)
  if (props.recipe.id && props.recipe.parameters?.length) {
    const all = getStorageAdapter().get<Record<string, Record<string, string | number>>>(STORAGE_KEYS.WIDGET_SETTINGS) ?? {}
    const saved = all[props.recipe.id]
    localParameters.value = saved ? { ...defaults, ...saved } : defaults
  } else {
    localParameters.value = defaults
  }
  executeQuery()
})
</script>
