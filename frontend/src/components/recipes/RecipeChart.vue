<template>
  <div ref="chartContainer" class="w-full h-full min-h-[200px]"></div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts'
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
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DatasetComponent,
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

  if (Array.isArray(series)) {
    return series.map((s) => {
      if (typeof s === 'object' && s !== null && 'label' in s && s.label) {
        return {
          ...s,
          label: {
            ...(s.label as object),
            ...labelStyle,
          },
        }
      }
      return s
    })
  }

  return series
}

// Build final chart options with data and theme
const finalOptions = computed<EChartsOption>(() => {
  const dark = isDarkMode()
  const textColor = dark ? '#e5e7eb' : '#374151'
  const axisLineColor = dark ? '#4b5563' : '#d1d5db'

  return {
    backgroundColor: 'transparent',
    textStyle: {
      color: textColor,
    },
    title: {
      textStyle: { color: textColor },
      ...((props.chartOptions.title as object) || {}),
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: dark ? '#1f2937' : '#ffffff',
      borderColor: dark ? '#374151' : '#e5e7eb',
      textStyle: { color: textColor },
      ...((props.chartOptions.tooltip as object) || {}),
    },
    legend: {
      textStyle: { color: textColor },
      ...((props.chartOptions.legend as object) || {}),
    },
    grid: {
      containLabel: true,
      left: 16,
      right: 16,
      top: 40,
      bottom: 16,
      ...((props.chartOptions.grid as object) || {}),
    },
    xAxis: Array.isArray(props.chartOptions.xAxis)
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
        },
    yAxis: Array.isArray(props.chartOptions.yAxis)
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
        },
    dataset: {
      source: props.data as Record<string, unknown>[],
    },
    series: applySeriesLabelStyles(props.chartOptions.series, dark, textColor),
  }
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
    const data = (params.value ?? params.data) as Record<string, unknown>
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
