<template>
  <div ref="chartContainer" class="w-full h-full min-h-[200px]"></div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts/core'
import {
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  TreemapChart,
  FunnelChart,
  GaugeChart,
  HeatmapChart,
  SankeyChart,
  RadarChart,
  SunburstChart,
} from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DatasetComponent,
  VisualMapComponent,
  CalendarComponent,
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
  FunnelChart,
  GaugeChart,
  HeatmapChart,
  SankeyChart,
  RadarChart,
  SunburstChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DatasetComponent,
  VisualMapComponent,
  CalendarComponent,
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

// Series types that don't use ECharts dataset.source — runtime injects rows
// directly into series[0].data (and sometimes auxiliary fields like .links
// or .indicator). Add a series type here when introducing a new chart type
// that doesn't read from dataset.source.
const DATA_INJECTED_TYPES = new Set([
  'treemap', 'funnel', 'gauge', 'heatmap',
  'sankey', 'radar', 'sunburst',
])

// Series types that don't use cartesian xAxis/yAxis. These also skip the
// default grid/axis configuration the runtime adds for bar/line charts.
const NON_CARTESIAN_TYPES = new Set([
  'pie', 'treemap', 'funnel', 'gauge', 'heatmap',
  'sankey', 'radar', 'sunburst',
])

function _firstSeriesType(): string | undefined {
  const series = props.chartOptions.series
  if (!Array.isArray(series) || series.length === 0) return undefined
  const s = series[0]
  return typeof s === 'object' && s !== null ? (s as { type?: string }).type : undefined
}

function isTreemapChart(): boolean {
  return _firstSeriesType() === 'treemap'
}

function isPieChart(): boolean {
  const series = props.chartOptions.series
  if (!Array.isArray(series)) return false
  return series.every((s) => typeof s === 'object' && s !== null && (s as { type?: string }).type === 'pie')
}

function needsDataInjection(): boolean {
  const t = _firstSeriesType()
  return !!t && DATA_INJECTED_TYPES.has(t)
}

function needsCartesianAxes(): boolean {
  const t = _firstSeriesType()
  return !!t && !NON_CARTESIAN_TYPES.has(t)
}

// Strip risky string-template formatters from a user-supplied tooltip config.
// ECharts replaces `{c}` with the data value, but on dataset-driven charts
// (which is how all our recipes use ECharts) `{c}` resolves to the *row
// object*, rendering as the literal string "[object Object]". The runtime's
// own valueFormatter / function-based formatter (set above) already handles
// this correctly, so the safest move is to drop the user-supplied string
// formatter when it contains `{c}` and let the runtime's default win.
//
// Function formatters from user code are kept as-is.
function sanitizeUserTooltip(tooltip: Record<string, unknown>): Record<string, unknown> {
  const f = tooltip.formatter
  if (typeof f === 'string' && /\{c\b/.test(f)) {
    const { formatter: _stripped, ...rest } = tooltip
    void _stripped
    console.warn(
      '[RecipeChart] Removed unsafe tooltip.formatter string containing {c} ' +
      `(was "${f}"). Dataset-driven charts must not use {c} — it resolves to ` +
      'the row object and renders as "[object Object]". Use only ' +
      '{trigger: "axis"|"item"} and let the runtime format values, or pass a ' +
      'function-typed formatter.'
    )
    return rest
  }
  return tooltip
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

type Row = Record<string, unknown>


/** Build a node tree from rows whose `pathField` is a colon-separated path
 *  (e.g. 'Expenses:Food:Restaurants'). Each leaf carries the row's value. */
function buildAccountTree(rows: Row[], pathField: string, valueField: string) {
  type Node = { name: string; value: number; children: Node[] }
  const root: Node = { name: '__root__', value: 0, children: [] }

  for (const row of rows) {
    const path = String(row[pathField] ?? '')
    const value = Number(row[valueField] ?? 0)
    if (!path || !isFinite(value) || value <= 0) continue
    const segments = path.split(':')
    let cur = root
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i]
      let child = cur.children.find((c) => c.name === seg)
      if (!child) {
        child = { name: seg, value: 0, children: [] }
        cur.children.push(child)
      }
      // Only the leaf carries the row's value; parents are summed below.
      if (i === segments.length - 1) child.value += value
      cur = child
    }
  }

  // Roll up each parent's value as the sum of descendants. ECharts can do this
  // automatically but pre-computing avoids zero-value parent collapse.
  function rollup(n: { value: number; children: { value: number }[] }): number {
    if (n.children.length === 0) return n.value
    n.value = (n.children as { value: number }[])
      .map((c) => rollup(c as { value: number; children: { value: number }[] }))
      .reduce((a, b) => a + b, 0)
    return n.value
  }
  rollup(root)
  // Strip leaves with zero/missing children arrays for cleaner ECharts data.
  function clean(n: Node): unknown {
    return n.children.length === 0
      ? { name: n.name, value: n.value }
      : { name: n.name, value: n.value, children: n.children.map(clean) }
  }
  return root.children.map(clean)
}

/** Inject query rows into the first matching series for chart types that
 *  don't read from dataset.source. Each type has its own per-row shape; the
 *  series may declare `*Field` overrides to point at non-default columns. */
function injectSeriesData(series: EChartsOption['series']): EChartsOption['series'] {
  if (!Array.isArray(series)) return series
  return series.map((s) => {
    if (typeof s !== 'object' || s === null) return s
    const t = (s as { type?: string }).type
    if (!t || !DATA_INJECTED_TYPES.has(t)) return s
    if (!Array.isArray(props.data)) return { ...s, data: props.data } as typeof s
    const rows = props.data as Row[]

    if (t === 'heatmap') {
      // ECharts heatmap-on-calendar wants [date, value] arrays.
      const dateField = (s as { dateField?: string }).dateField || 'date'
      const valueField = (s as { valueField?: string }).valueField || 'value'
      return { ...s, data: rows.map((r) => [r[dateField], r[valueField]]) } as typeof s
    }

    if (t === 'sankey') {
      // Build {nodes, links} from {source, target, value} rows. Nodes are
      // unique source/target names; links are the rows themselves.
      const sourceField = (s as { sourceField?: string }).sourceField || 'source'
      const targetField = (s as { targetField?: string }).targetField || 'target'
      const valueField = (s as { valueField?: string }).valueField || 'value'
      const nodeNames = new Set<string>()
      const links: Array<{ source: string; target: string; value: number }> = []
      for (const r of rows) {
        const src = String(r[sourceField] ?? '')
        const tgt = String(r[targetField] ?? '')
        const v = Number(r[valueField] ?? 0)
        if (!src || !tgt || !isFinite(v) || v <= 0) continue
        nodeNames.add(src)
        nodeNames.add(tgt)
        links.push({ source: src, target: tgt, value: v })
      }
      const nodes = Array.from(nodeNames).map((name) => ({ name }))
      return { ...s, data: nodes, links } as typeof s
    }

    if (t === 'radar') {
      // SQL returns one row per dimension: {category, value}. Runtime extracts
      // categories → indicators, builds a single series.data entry whose
      // `value` array is in matching order.
      const categoryField = (s as { categoryField?: string }).categoryField || 'category'
      const valueField = (s as { valueField?: string }).valueField || 'value'
      const labelField = (s as { labelField?: string }).labelField
      const indicatorMaxRatio = (s as { indicatorMaxRatio?: number }).indicatorMaxRatio || 1.2
      const cats: string[] = []
      const values: number[] = []
      for (const r of rows) {
        const c = String(r[categoryField] ?? '')
        const v = Number(r[valueField] ?? 0)
        if (!c || !isFinite(v)) continue
        cats.push(c)
        values.push(v)
      }
      const max = values.length ? Math.max(...values) * indicatorMaxRatio : 100
      // Push the indicator config onto the *series* so it can be lifted to
      // the top-level radar config below by the renderer.
      const indicators = cats.map((name) => ({ name, max }))
      const subjectName = labelField && rows[0] ? String(rows[0][labelField] ?? 'Series') : 'Series'
      return {
        ...s,
        data: [{ name: subjectName, value: values }],
        // _runtimeIndicators is a private side-channel for lifting the
        // indicator config to the top-level radar block in finalOptions.
        // Cast through unknown — ECharts SeriesOption types don't allow
        // arbitrary fields, but the renderer strips this before render.
        _runtimeIndicators: indicators,
      } as unknown as typeof s
    }

    if (t === 'sunburst') {
      // Build a tree from colon-separated account paths.
      const pathField = (s as { pathField?: string }).pathField || 'account'
      const valueField = (s as { valueField?: string }).valueField || 'value'
      return { ...s, data: buildAccountTree(rows, pathField, valueField) } as typeof s
    }

    // Default: drop rows directly into series.data (treemap, funnel, gauge).
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
  const needsAxes = needsCartesianAxes()
  const injectData = needsDataInjection()

  let styledSeries = applySeriesLabelStyles(props.chartOptions.series, dark, textColor)
  if (props.seriesLabelFormat) {
    styledSeries = applySeriesLabelFormat(styledSeries, props.seriesLabelFormat)
  }
  let finalSeries = injectData ? injectSeriesData(styledSeries) : styledSeries

  // Lift any radar series' runtime-derived indicator up to the top-level
  // `radar` config (ECharts requires it there). Strip the temp field from the
  // series after lifting so it doesn't leak into the rendered options.
  let radarConfig: { indicator: Array<{ name: string; max: number }> } | null = null
  if (Array.isArray(finalSeries)) {
    finalSeries = finalSeries.map((s) => {
      if (typeof s === 'object' && s !== null && '_runtimeIndicators' in s) {
        const { _runtimeIndicators, ...rest } = s as unknown as Record<string, unknown>
        if (Array.isArray(_runtimeIndicators)) {
          radarConfig = { indicator: _runtimeIndicators as Array<{ name: string; max: number }> }
        }
        return rest as unknown as typeof s
      }
      return s
    })
  }

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
      // 'item' for non-cartesian types (per-element tooltip); 'axis' for
      // cartesian charts (bar/line/scatter — tooltip aligned to the x-axis).
      trigger: needsAxes ? 'axis' : 'item',
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
      // Heatmap-on-calendar tooltip: params.value is [date, value]. Without a
      // formatter, ECharts default hides the date — only the number is shown,
      // which is unhelpful for a calendar view.
      ...(_firstSeriesType() === 'heatmap'
        ? {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter: ((params: any) => {
              const v = params.value
              if (!Array.isArray(v) || v.length < 2) return String(params.value ?? '')
              const date = String(v[0] ?? '')
              const num = Number(v[1] ?? 0)
              const formatted = props.currency ? formatAmount(num, props.currency) : num.toLocaleString()
              return `${date}<br/><b>${formatted}</b>`
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
      ...sanitizeUserTooltip((props.chartOptions.tooltip as Record<string, unknown>) || {}),
    },
    legend: {
      textStyle: { color: textColor },
      ...((props.chartOptions.legend as object) || {}),
    },
    series: finalSeries,
  }

  // Pass through chart-type-specific top-level options that the runtime
  // doesn't intercept. `calendar` is required for heatmap-on-calendar,
  // `visualMap` configures color scales (used by heatmap and others).
  const passthroughKeys = ['calendar', 'visualMap', 'radar', 'graphic'] as const
  for (const key of passthroughKeys) {
    const v = (props.chartOptions as Record<string, unknown>)[key]
    if (v !== undefined) {
      ;(result as Record<string, unknown>)[key] = v
    }
  }

  // Merge runtime-derived radar config with anything the recipe supplied.
  // Recipe-provided fields (e.g. axisName styling, splitArea, custom indicator
  // overrides) win; the runtime fills in `indicator` if the recipe didn't
  // compute one itself. This lets a recipe style the axes without losing the
  // auto-derived dimension list.
  if (radarConfig) {
    const derived = radarConfig as { indicator: Array<{ name: string; max: number }> }
    const existing = ((result as { radar?: unknown }).radar as Record<string, unknown> | undefined) || {}
    ;(result as Record<string, unknown>).radar = {
      ...derived,
      ...existing,
      indicator: existing.indicator ?? derived.indicator,
    }
  }

  // For calendar-coordinate heatmaps, derive `calendar.range` from the data's
  // date column when the recipe didn't set a literal range (ECharts requires
  // an explicit range — recipes can't compute this via the SQL parameter
  // system because :year only substitutes inside SQL, not chart options).
  if (
    _firstSeriesType() === 'heatmap' &&
    Array.isArray(props.data) && props.data.length > 0
  ) {
    const cal = (result as { calendar?: Record<string, unknown> }).calendar
    if (cal && (cal.range === undefined || typeof cal.range === 'string' && cal.range.startsWith(':'))) {
      const dates = (props.data as Record<string, unknown>[])
        .map((r) => r.date)
        .filter((d): d is string => typeof d === 'string')
        .sort()
      if (dates.length > 0) {
        cal.range = [dates[0], dates[dates.length - 1]]
      }
    }
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
