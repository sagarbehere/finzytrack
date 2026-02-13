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
}

const props = withDefaults(defineProps<Props>(), {
  clickable: false,
})

const emit = defineEmits<{
  seriesClick: [context: { seriesName: string; seriesIndex: number; dataIndex: number; data: Record<string, unknown> }]
}>()

const chartContainer = ref<HTMLElement | null>(null)
let chartInstance: EChartsInstance | null = null

// Detect dark mode
function isDarkMode(): boolean {
  return document.documentElement.classList.contains('dark')
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

  const styledSeries = applySeriesLabelStyles(props.chartOptions.series, dark, textColor)
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
    result.xAxis = Array.isArray(props.chartOptions.xAxis)
      ? props.chartOptions.xAxis.map((axis) => ({
          axisLine: { lineStyle: { color: axisLineColor } },
          axisLabel: { color: textColor },
          splitLine: { lineStyle: { color: axisLineColor, opacity: 0.3 } },
          ...axis,
        }))
      : {
          axisLine: { lineStyle: { color: axisLineColor } },
          axisLabel: { color: textColor },
          splitLine: { lineStyle: { color: axisLineColor, opacity: 0.3 } },
          ...((props.chartOptions.xAxis as object) || {}),
        }
    result.yAxis = Array.isArray(props.chartOptions.yAxis)
      ? props.chartOptions.yAxis.map((axis) => ({
          axisLine: { lineStyle: { color: axisLineColor } },
          axisLabel: { color: textColor },
          splitLine: { lineStyle: { color: axisLineColor, opacity: 0.3 } },
          ...axis,
        }))
      : {
          axisLine: { lineStyle: { color: axisLineColor } },
          axisLabel: { color: textColor },
          splitLine: { lineStyle: { color: axisLineColor, opacity: 0.3 } },
          ...((props.chartOptions.yAxis as object) || {}),
        }
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
    if (chartInstance) {
      chartInstance.setOption(finalOptions.value, true)
    }
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
