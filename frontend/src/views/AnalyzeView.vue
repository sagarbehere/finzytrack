<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Analyze</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        Query your financial data using natural language or SQL
      </p>
    </div>

    <!-- Natural Language Input -->
    <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 p-4 mb-4">
      <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
        Ask a question in plain English
      </label>
      <p v-if="!llmConfigured" class="text-xs text-amber-600 dark:text-amber-400 mb-2">
        LLM not configured. Set VITE_LLM_API_URL to enable natural language queries.
      </p>
      <textarea
        v-model="nlQuery"
        :disabled="!llmConfigured"
        placeholder="e.g. Show me my top 10 expense categories this year"
        rows="2"
        class="w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm disabled:opacity-50 disabled:cursor-not-allowed"
        @keydown.meta.enter="handleGenerateSQL"
        @keydown.ctrl.enter="handleGenerateSQL"
      />
      <button
        @click="handleGenerateSQL"
        :disabled="!nlQuery.trim() || isGenerating || !llmConfigured"
        class="mt-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed text-sm flex items-center gap-2"
      >
        <svg v-if="isGenerating" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>{{ isGenerating ? 'Generating...' : 'Generate SQL' }}</span>
      </button>
    </div>

    <!-- SQL Editor -->
    <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 p-4 mb-4">
      <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
        SQL Query
      </label>
      <textarea
        v-model="sqlQuery"
        placeholder="SELECT account, SUM(amount) as total FROM postings WHERE account_type = 'Expenses' GROUP BY account ORDER BY total DESC LIMIT 10"
        rows="6"
        spellcheck="false"
        class="w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 font-mono text-sm"
        @keydown.meta.enter="handleExecuteSQL"
        @keydown.ctrl.enter="handleExecuteSQL"
      />
      <div class="mt-2 flex items-center gap-2">
        <button
          @click="handleExecuteSQL"
          :disabled="!sqlQuery.trim() || isExecuting"
          class="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed text-sm flex items-center gap-2"
        >
          <svg v-if="isExecuting" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{{ isExecuting ? 'Executing...' : 'Execute' }}</span>
        </button>
        <button
          @click="clearSQL"
          class="px-4 py-2 bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-md hover:bg-gray-300 dark:hover:bg-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2 text-sm"
        >
          Clear
        </button>
        <span v-if="executionTime !== null" class="text-xs text-gray-500 dark:text-gray-400 ml-auto">
          {{ resultRows.length }} row{{ resultRows.length !== 1 ? 's' : '' }} in {{ executionTime }}ms
        </span>
      </div>
    </div>

    <!-- Error Display -->
    <div v-if="errorMessage" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 mb-4">
      <div class="flex items-start">
        <div class="flex-shrink-0">
          <svg class="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clip-rule="evenodd" />
          </svg>
        </div>
        <div class="ml-3">
          <h3 class="text-sm font-medium text-red-800 dark:text-red-300">Error</h3>
          <p class="mt-1 text-sm text-red-700 dark:text-red-400 whitespace-pre-wrap">{{ errorMessage }}</p>
        </div>
      </div>
    </div>

    <!-- Results Panel with Tabs -->
    <div v-if="resultColumns.length > 0" class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 overflow-hidden">
      <!-- Tab Bar -->
      <div class="border-b border-gray-200 dark:border-gray-700">
        <nav class="flex -mb-px">
          <button
            @click="activeTab = 'table'"
            :class="[
              'px-4 py-3 text-sm font-medium border-b-2 focus:outline-none',
              activeTab === 'table'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300 dark:hover:border-gray-600',
            ]"
          >
            Table
          </button>
          <button
            @click="activeTab = 'chart'"
            :class="[
              'px-4 py-3 text-sm font-medium border-b-2 focus:outline-none',
              activeTab === 'chart'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300 dark:hover:border-gray-600',
            ]"
          >
            Chart
          </button>
        </nav>
      </div>

      <!-- Table Tab -->
      <div v-if="activeTab === 'table'" class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead class="bg-gray-50 dark:bg-gray-900">
            <tr>
              <th
                v-for="col in resultColumns"
                :key="col"
                class="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider whitespace-nowrap"
              >
                {{ col }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
            <tr
              v-for="(row, idx) in resultRows"
              :key="idx"
              class="hover:bg-gray-50 dark:hover:bg-gray-700/50"
            >
              <td
                v-for="col in resultColumns"
                :key="col"
                class="px-4 py-2 text-sm text-gray-900 dark:text-gray-200 whitespace-nowrap"
              >
                {{ formatCellValue(row[col]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Chart Tab -->
      <div v-if="activeTab === 'chart'" class="p-4">
        <!-- Chart Controls -->
        <div class="flex flex-wrap items-end gap-4 mb-4">
          <!-- Chart Type -->
          <div>
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Type</label>
            <div class="flex gap-1">
              <button
                v-for="ct in chartTypes"
                :key="ct"
                @click="chartType = ct"
                :class="[
                  'px-3 py-1.5 text-xs rounded-md font-medium capitalize',
                  chartType === ct
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600',
                ]"
              >
                {{ ct }}
              </button>
            </div>
          </div>

          <!-- X / Category Column -->
          <div class="min-w-[140px]">
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              {{ chartType === 'pie' ? 'Name' : 'X Axis' }}
            </label>
            <select
              v-model="chartXColumn"
              class="w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm py-1.5"
            >
              <option value="">-- select --</option>
              <option v-for="col in resultColumns" :key="col" :value="col">{{ col }}</option>
            </select>
          </div>

          <!-- Y / Value Columns -->
          <div class="min-w-[200px]">
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              {{ chartType === 'pie' ? 'Value' : 'Y Axis' }}
              <span v-if="chartType !== 'pie'" class="font-normal">(multi-select)</span>
            </label>
            <select
              v-if="chartType === 'pie'"
              v-model="chartYColumnSingle"
              class="w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm py-1.5"
            >
              <option value="">-- select --</option>
              <option v-for="col in numericColumns" :key="col" :value="col">{{ col }}</option>
            </select>
            <select
              v-else
              v-model="chartYColumns"
              multiple
              :size="Math.min(numericColumns.length, 4)"
              class="w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm py-1"
            >
              <option v-for="col in numericColumns" :key="col" :value="col">{{ col }}</option>
            </select>
          </div>
        </div>

        <!-- Chart -->
        <div v-if="chartReady" class="h-[400px]">
          <RecipeChart :chart-options="builtChartOptions" :data="resultRows" />
        </div>
        <div v-else class="h-[400px] flex items-center justify-center text-gray-400 dark:text-gray-500 text-sm">
          Select columns above to render a chart.
        </div>
      </div>
    </div>

    <!-- Empty State (no results yet) -->
    <div v-else-if="!errorMessage && !isExecuting" class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 p-6">
      <div class="text-center py-8 text-gray-500 dark:text-gray-400">
        <p class="text-sm">Write an SQL query or ask a question above, then execute to see results here.</p>
        <p class="text-xs mt-2 text-gray-400 dark:text-gray-500">
          Tip: Press Cmd+Enter (or Ctrl+Enter) to execute quickly.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { ref, computed, watch } from 'vue'
  import { generateSQL, isSQLAssistantConfigured } from '@/services/sqlAssistant'
  import { LedgerService } from '@/services/generated-api'
  import type { QueryRequest } from '@/services/generated-api'
  import { useToast } from '@/composables/useNotifications'
  import RecipeChart from '@/components/recipes/RecipeChart.vue'
  import { SUPPORTED_CHART_TYPES, type ChartType } from '@/types/recipes'
  import type { EChartsOption } from 'echarts'

  const toast = useToast()

  // --- Query state ---
  const nlQuery = ref('')
  const sqlQuery = ref('')
  const isGenerating = ref(false)
  const isExecuting = ref(false)
  const errorMessage = ref('')
  const resultColumns = ref<string[]>([])
  const resultRows = ref<Record<string, unknown>[]>([])
  const executionTime = ref<number | null>(null)
  const llmConfigured = isSQLAssistantConfigured()

  // --- Results tab state ---
  const activeTab = ref<'table' | 'chart'>('table')

  // --- Chart config state ---
  const chartTypes = SUPPORTED_CHART_TYPES
  const chartType = ref<ChartType>('bar')
  const chartXColumn = ref('')
  const chartYColumns = ref<string[]>([])
  // For pie charts, only one Y column is meaningful
  const chartYColumnSingle = ref('')

  /** Columns whose first non-null value is a number */
  const numericColumns = computed(() => {
    if (resultRows.value.length === 0) return resultColumns.value
    return resultColumns.value.filter((col) => {
      const sample = resultRows.value.find((r) => r[col] !== null && r[col] !== undefined)
      return sample ? typeof sample[col] === 'number' : false
    })
  })

  /** The effective Y columns list, accounting for pie's single-select */
  const effectiveYColumns = computed(() => {
    if (chartType.value === 'pie') {
      return chartYColumnSingle.value ? [chartYColumnSingle.value] : []
    }
    return chartYColumns.value
  })

  /** True when enough config is set to render a chart */
  const chartReady = computed(() => {
    return chartXColumn.value !== '' && effectiveYColumns.value.length > 0
  })

  /** Build ECharts options from user selections */
  const builtChartOptions = computed<EChartsOption>(() => {
    const x = chartXColumn.value
    const yCols = effectiveYColumns.value
    const type = chartType.value

    if (type === 'pie') {
      return {
        tooltip: { trigger: 'item' },
        series: [
          {
            type: 'pie',
            radius: ['30%', '70%'],
            encode: { itemName: x, value: yCols[0] },
          },
        ],
      }
    }

    // For area charts, ECharts uses type 'line' with areaStyle
    const echartsSeriesType = type === 'area' ? 'line' : type

    const series = yCols.map((yCol) => ({
      name: yCol,
      type: echartsSeriesType,
      encode: { x, y: yCol },
      ...(type === 'area' ? { areaStyle: {} } : {}),
    }))

    return {
      tooltip: { trigger: 'axis' },
      legend: yCols.length > 1 ? { data: yCols } : undefined,
      xAxis: { type: 'category' as const },
      yAxis: { type: 'value' as const },
      series,
    }
  })

  // Auto-select sensible defaults when results change
  watch(resultColumns, (cols) => {
    if (cols.length === 0) return

    // Pick first non-numeric column as X, first numeric as Y
    const numCols = numericColumns.value
    const nonNumCols = cols.filter((c) => !numCols.includes(c))

    chartXColumn.value = nonNumCols.length > 0 ? nonNumCols[0] : cols[0]
    if (numCols.length > 0) {
      chartYColumns.value = [numCols[0]]
      chartYColumnSingle.value = numCols[0]
    } else {
      chartYColumns.value = []
      chartYColumnSingle.value = ''
    }
  })

  // --- Handlers ---

  async function handleGenerateSQL() {
    if (!nlQuery.value.trim() || isGenerating.value) return

    isGenerating.value = true
    errorMessage.value = ''
    try {
      const sql = await generateSQL(nlQuery.value.trim())
      sqlQuery.value = sql
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to generate SQL'
      errorMessage.value = msg
      toast.error('Generation Failed', msg)
    } finally {
      isGenerating.value = false
    }
  }

  async function handleExecuteSQL() {
    if (!sqlQuery.value.trim() || isExecuting.value) return

    isExecuting.value = true
    errorMessage.value = ''
    resultColumns.value = []
    resultRows.value = []
    executionTime.value = null

    const startTime = performance.now()

    try {
      const request: QueryRequest = { query: sqlQuery.value.trim() }
      const response = await LedgerService.executeQuery(request, 'sqlite')

      executionTime.value = Math.round(performance.now() - startTime)

      if (!response.success) {
        const msg = (response as Record<string, unknown>).message as string || 'Query execution failed'
        throw new Error(msg)
      }

      const data = response.data
      if (!data || !data.rows) {
        resultColumns.value = []
        resultRows.value = []
        return
      }

      const rows = data.rows as Record<string, unknown>[]
      resultRows.value = rows

      // Derive columns from the first row, or from response.data.columns if available
      if (data.columns && Array.isArray(data.columns) && data.columns.length > 0) {
        resultColumns.value = data.columns.map((c) => typeof c === 'string' ? c : c.name)
      } else if (rows.length > 0) {
        resultColumns.value = Object.keys(rows[0])
      } else {
        resultColumns.value = []
      }
    } catch (e: unknown) {
      executionTime.value = Math.round(performance.now() - startTime)
      const msg = e instanceof Error ? e.message : 'Query execution failed'
      errorMessage.value = msg
    } finally {
      isExecuting.value = false
    }
  }

  function clearSQL() {
    sqlQuery.value = ''
    errorMessage.value = ''
    resultColumns.value = []
    resultRows.value = []
    executionTime.value = null
  }

  function formatCellValue(value: unknown): string {
    if (value === null || value === undefined) return ''
    if (typeof value === 'number') {
      if (Number.isInteger(value)) return value.toLocaleString()
      return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    }
    return String(value)
  }
</script>
