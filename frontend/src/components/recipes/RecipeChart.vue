<template>
  <div ref="chartContainer" class="w-full h-full min-h-[200px]"></div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, ScatterChart, TreemapChart } from 'echarts/charts'
import { VisualMapComponent } from 'echarts/components'
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DatasetComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import type { ECharts as EChartsInstance } from 'echarts/core'
import { formatAmount } from '@/utils/currencyFormat'
import { getFormats } from '@/composables/useRecipeExecutor'
import type { ValueFormat } from '@/types/recipes'

// Register ECharts components
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  TreemapChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DatasetComponent,
  VisualMapComponent,
  CanvasRenderer,
])

interface Props {
  chartOptions: EChartsOption
  data: unknown[]
  clickable?: boolean
  currency?: string
  /** Predefined format applied to all series data labels (e.g. 'compact', 'currency'). */
  seriesLabelFormat?: ValueFormat
  /** Predefined format applied to y-axis tick labels. */
  yAxisLabelFormat?: ValueFormat
  /** Predefined format applied to x-axis tick labels. */
  xAxisLabelFormat?: ValueFormat
}

const props = withDefaults(defineProps<Props>(), {
  clickable: false,
})

const emit = defineEmits<{
  seriesClick: [context: { seriesName: string; seriesIndex: number; dataIndex: number; data: Record<string, unknown> }]
}>()

const chartContainer = ref<HTMLElement | null>(null)
let chartInstance: EChartsInstance | null = null

// Reactive dark mode state — updated by MutationObserver so computed properties recompute
const darkMode = ref(document.documentElement.classList.contains('dark'))

// Detect dark mode (reads reactive ref)
function isDarkMode(): boolean {
  return darkMode.value
}

// Apply dark mode label styling to series
function applySeriesLabelStyles(
  series: EChartsOption['series'],
  dark: boolean,
  textColor: string
): EChartsOption['series'] {
  if (!series) return series

  const labelStyle = {
    color: textColor,
    textBorderColor: dark ? '#1f2937' : '#ffffff',
    textBorderWidth: 2,
  }

  // Text border helps readability when labels overlap colored elements (bar, line).
  // For other chart types (pie, scatter), disable text border to avoid a halo effect.
  // Treemap is skipped entirely — it auto-adjusts label colors based on node background.
  const overlayTypes = new Set(['bar', 'line'])
  const skipLabelOverride = new Set(['treemap'])

  if (Array.isArray(series)) {
    return series.map((s) => {
      if (typeof s !== 'object' || s === null) return s
      const seriesType = (s as { type?: string }).type
      if (skipLabelOverride.has(seriesType || '')) return s
      const existingLabel = ('label' in s && s.label ? s.label : {}) as object
      const style = overlayTypes.has(seriesType || '')
        ? labelStyle
        : { color: textColor, textBorderWidth: 0 }
      return {
        ...s,
        label: {
          ...existingLabel,
          ...style,
        },
      }
    })
  }

  return series
}

// Detect if any series is a treemap (treemap doesn't support dataset.source)
function isTreemapChart(): boolean {
  const series = props.chartOptions.series
  if (!Array.isArray(series)) return false
  return series.some((s) => typeof s === 'object' && s !== null && (s as { type?: string }).type === 'treemap')
}

// Detect if all series are pie charts (pie charts don't use axes)
function isPieChart(): boolean {
  const series = props.chartOptions.series
  if (!Array.isArray(series)) return false
  return series.every((s) => typeof s === 'object' && s !== null && (s as { type?: string }).type === 'pie')
}

// Build a formatter function from a ValueFormat string, using the widget's currency for locale-aware formatting
function buildFormatter(format: ValueFormat): (value: unknown) => string {
  const formats = getFormats(props.currency)
  const fn = formats[format]
  if (typeof fn !== 'function') {
    console.warn(`[RecipeChart] Unknown format "${format}", falling back to String()`)
    return (value: unknown) => String(value ?? '')
  }
  return (value: unknown) => fn(value)
}

// Inject label formatters into series that have labels enabled
function applySeriesLabelFormat(
  series: EChartsOption['series'],
  format: ValueFormat,
): EChartsOption['series'] {
  if (!Array.isArray(series)) return series
  const formatter = buildFormatter(format)
  return series.map((s) => {
    if (typeof s !== 'object' || s === null) return s
    const existing = ('label' in s && s.label ? s.label : {}) as Record<string, unknown>
    // Only inject if label is shown; if show is not set, default ECharts hides labels, so skip
    if (!existing.show) return s
    // Don't overwrite an existing formatter (TypeScript recipes set their own)
    if (existing.formatter) return s
    return {
      ...s,
      label: {
        ...existing,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any) => {
          // ECharts passes the full dataset row as params.value for dataset-driven series.
          // Try all encode fields in order (x, y, value) and use the first that
          // resolves to a valid finite number — handles both vertical and horizontal bars.
          const raw = params.value
          if (typeof raw === 'number') return formatter(raw)
          let numVal: number | null = null
          if (typeof raw === 'object' && raw !== null) {
            const encodeConfig = (s as Record<string, unknown>).encode as Record<string, unknown> | undefined
            if (encodeConfig) {
              const fields = Object.values(encodeConfig).flat()
              for (const field of fields) {
                const v = Number((raw as Record<string, unknown>)[String(field)])
                if (isFinite(v)) { numVal = v; break }
              }
            }
          }
          return formatter(numVal ?? Number(raw) ?? 0)
        },
      },
    } as typeof s
  })
}

// Inject a formatter into an axis label config
function applyAxisLabelFormat(
  axis: Record<string, unknown>,
  format: ValueFormat,
): Record<string, unknown> {
  const existing = (axis.axisLabel ?? {}) as Record<string, unknown>
  if (existing.formatter) return axis // Don't overwrite explicit formatters
  const formatter = buildFormatter(format)
  return {
    ...axis,
    axisLabel: {
      ...existing,
      formatter: (value: unknown) => formatter(value),
    },
  }
}

// Inject data into treemap series
function injectTreemapData(series: EChartsOption['series']): EChartsOption['series'] {
  if (!Array.isArray(series)) return series
  return series.map((s) => {
    if (typeof s !== 'object' || s === null) return s
    if ((s as { type?: string }).type !== 'treemap') return s
    return { ...s, data: props.data } as typeof s
  })
}

// Build final chart options with data and theme
const finalOptions = computed<EChartsOption>(() => {
  const dark = isDarkMode()
  const textColor = dark ? '#e5e7eb' : '#374151'
  const axisLineColor = dark ? '#4b5563' : '#d1d5db'
  const treemap = isTreemapChart()
  const pie = isPieChart()
  const needsAxes = !treemap && !pie

  let styledSeries = applySeriesLabelStyles(props.chartOptions.series, dark, textColor)
  if (props.seriesLabelFormat) {
    styledSeries = applySeriesLabelFormat(styledSeries, props.seriesLabelFormat)
  }
  const finalSeries = treemap ? injectTreemapData(styledSeries) : styledSeries

  const result: EChartsOption = {
    backgroundColor: 'transparent',
    textStyle: {
      color: textColor,
    },
    title: {
      textStyle: { color: textColor },
      ...((props.chartOptions.title as object) || {}),
    },
    tooltip: {
      trigger: treemap ? 'item' : 'axis',
      confine: true,
      backgroundColor: dark ? '#1f2937' : '#ffffff',
      borderColor: dark ? '#374151' : '#e5e7eb',
      textStyle: { color: textColor },
      // Currency-aware formatter for item-trigger charts (pie, treemap).
      // Only applied when a currency is known and the recipe hasn't defined its own formatter.
      // Recipe formatters spread below will override this if present.
      ...(props.currency && (treemap || pie)
        ? {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter: ((params: any) => {
              const name = params.name ?? ''
              // For dataset-driven charts (pie uses dataset.source + encode), params.value
              // is the full dataset row, not a plain number. Fall back to params.data.value.
              const raw = typeof params.value === 'number'
                ? params.value
                : (params.data?.value ?? 0)
              const value = formatAmount(Number(raw), props.currency!)
              const percent = params.percent != null ? ` (${params.percent.toFixed(1)}%)` : ''
              return `${name}<br/>${value}${percent}`
            }) as any,
          }
        : {}),
      // For axis-trigger charts (bar, line), use valueFormatter to round/format values.
      // This avoids floating-point noise like 52021.060000000005 in tooltips.
      ...(props.currency && !treemap && !pie
        ? {
            valueFormatter: ((value: number | string) => {
              const num = typeof value === 'number' ? value : parseFloat(String(value))
              if (isNaN(num)) return String(value)
              return formatAmount(num, props.currency!)
            }) as any,
          }
        : !treemap && !pie
        ? {
            valueFormatter: ((value: number | string) => {
              const num = typeof value === 'number' ? value : parseFloat(String(value))
              if (isNaN(num)) return String(value)
              return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            }) as any,
          }
        : {}),
      ...((props.chartOptions.tooltip as object) || {}),
    },
    legend: {
      textStyle: { color: textColor },
      ...((props.chartOptions.legend as object) || {}),
    },
    series: finalSeries,
  }

  // Only include axis/grid for charts that use cartesian coordinates
  if (needsAxes) {
    result.grid = {
      containLabel: true,
      left: 16,
      right: 16,
      top: 40,
      bottom: 16,
      ...((props.chartOptions.grid as object) || {}),
    }

    const baseXAxis = {
      axisLine: { lineStyle: { color: axisLineColor } },
      axisLabel: { color: textColor },
      splitLine: { lineStyle: { color: axisLineColor, opacity: 0.3 } },
    }
    const baseYAxis = {
      axisLine: { lineStyle: { color: axisLineColor } },
      axisLabel: { color: textColor },
      splitLine: { lineStyle: { color: axisLineColor, opacity: 0.3 } },
    }

    const mergeAxis = (base: Record<string, unknown>, override: object) =>
      ({ ...base, ...override }) as Record<string, unknown>

    const rawXAxis = Array.isArray(props.chartOptions.xAxis)
      ? props.chartOptions.xAxis.map((axis) => mergeAxis(baseXAxis, axis))
      : mergeAxis(baseXAxis, (props.chartOptions.xAxis as object) || {})
    const rawYAxis = Array.isArray(props.chartOptions.yAxis)
      ? props.chartOptions.yAxis.map((axis) => mergeAxis(baseYAxis, axis))
      : mergeAxis(baseYAxis, (props.chartOptions.yAxis as object) || {})

    result.xAxis = props.xAxisLabelFormat
      ? Array.isArray(rawXAxis)
        ? rawXAxis.map((ax) => applyAxisLabelFormat(ax, props.xAxisLabelFormat!))
        : applyAxisLabelFormat(rawXAxis, props.xAxisLabelFormat)
      : rawXAxis
    result.yAxis = props.yAxisLabelFormat
      ? Array.isArray(rawYAxis)
        ? rawYAxis.map((ax) => applyAxisLabelFormat(ax, props.yAxisLabelFormat!))
        : applyAxisLabelFormat(rawYAxis, props.yAxisLabelFormat)
      : rawYAxis

    result.dataset = {
      source: props.data as Record<string, unknown>[],
    }
  }

  // Pie charts still need dataset for encode to work, but no axes
  if (pie) {
    result.dataset = {
      source: props.data as Record<string, unknown>[],
    }
  }

  return result
})

// Initialize chart
function initChart() {
  if (!chartContainer.value) return

  const instance = echarts.init(chartContainer.value, undefined, {
    renderer: 'canvas',
  })
  chartInstance = instance
  instance.setOption(finalOptions.value)

  // Emit click events for series elements
  instance.on('click', (params) => {
    // For treemap, params.value is a number (the node value), not the data object.
    // Prefer params.data when params.value is not an object.
    const value = params.value
    const data = (typeof value === 'object' && value !== null ? value : params.data) as Record<string, unknown>
    if (data) {
      emit('seriesClick', {
        seriesName: params.seriesName as string,
        seriesIndex: params.seriesIndex as number,
        dataIndex: params.dataIndex as number,
        data,
      })
    }
  })

  // Show pointer cursor on hoverable series when clickable
  if (props.clickable) {
    instance.getZr().on('mousemove', (params) => {
      const target = params.target
      chartContainer.value!.style.cursor = target ? 'pointer' : 'default'
    })
  }
}

// Update chart when options change
watch(
  finalOptions,
  (newOptions) => {
    if (chartInstance) {
      chartInstance.setOption(newOptions, true)
    }
  },
  { deep: true }
)

// Resize handler
function handleResize() {
  chartInstance?.resize()
}

// Dark mode observer
let darkModeObserver: MutationObserver | null = null

function setupDarkModeObserver() {
  darkModeObserver = new MutationObserver(() => {
    darkMode.value = document.documentElement.classList.contains('dark')
  })
  darkModeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
  })
}

onMounted(() => {
  initChart()
  window.addEventListener('resize', handleResize)
  setupDarkModeObserver()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  darkModeObserver?.disconnect()
  chartInstance?.dispose()
})
</script>
