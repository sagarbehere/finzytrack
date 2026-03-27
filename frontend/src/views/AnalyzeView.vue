<template>
  <div>
    <div class="mb-6 flex items-start justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Analyze</h1>
        <p class="mt-1 text-gray-600 dark:text-gray-400">
          Query your financial data using natural language or {{ queryLanguage === 'sqlite' ? 'SQL' : 'BQL' }}
        </p>
      </div>

      <!-- Language Toggle -->
      <div class="flex rounded-md shadow-sm" role="group">
        <button
          @click="queryLanguage = 'sqlite'"
          :class="[
            'px-4 py-2 text-sm font-medium border focus:outline-none focus:z-10',
            'rounded-l-md',
            queryLanguage === 'sqlite'
              ? 'bg-indigo-600 text-white border-indigo-600'
              : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-300 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/5',
          ]"
        >
          SQLite
        </button>
        <button
          @click="queryLanguage = 'beanquery'"
          :class="[
            'px-4 py-2 text-sm font-medium border focus:outline-none focus:z-10',
            '-ml-px rounded-r-md',
            queryLanguage === 'beanquery'
              ? 'bg-indigo-600 text-white border-indigo-600'
              : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-300 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/5',
          ]"
        >
          BQL
        </button>
      </div>
    </div>

    <!-- Natural Language Input -->
    <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 p-4 mb-4">
      <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
        Ask a question in plain English
      </label>
      <p class="text-xs text-gray-400 dark:text-gray-500 mb-1">
        AI can make mistakes — review output carefully.
        <a href="https://finzytrack.app/docs/ai-data-sharing" target="_blank" rel="noopener noreferrer" class="text-indigo-500 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 underline underline-offset-2">Data shared with AI</a>
      </p>
      <p v-if="!config?.ai?.llm?.api_url" class="text-xs text-amber-600 dark:text-amber-400 mb-2">
        LLM not configured. Set <code class="font-mono">ai.llm.api_url</code> in <code class="font-mono">{{ config?.config_file_path ?? 'config.yaml' }}</code> to enable query generation.
      </p>
      <textarea
        v-model="nlQuery"
        placeholder="e.g. Show me my top 10 expense categories this year"
        rows="2"
        class="w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
        @keydown.meta.enter="handleGenerate"
        @keydown.ctrl.enter="handleGenerate"
      />
      <div class="mt-2 flex items-center gap-2">
        <button
          @click="handleGenerate"
          :disabled="!nlQuery.trim() || isGenerating || !config?.ai?.llm?.api_url"
          class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-sm flex items-center gap-2"
        >
          <svg v-if="isGenerating" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{{ isGenerating ? 'Generating...' : generateButtonLabel }}</span>
        </button>
        <button
          @click="nlQuery = ''"
          :disabled="!nlQuery.trim()"
          class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 disabled:opacity-50 disabled:cursor-not-allowed text-sm"
        >
          Clear
        </button>
      </div>
    </div>

    <!-- Query Editor -->
    <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 p-4 mb-4">
      <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
        {{ queryLanguage === 'sqlite' ? 'SQL Query' : 'BQL Query' }}
      </label>
      <textarea
        v-model="queryText"
        :placeholder="queryPlaceholder"
        rows="6"
        spellcheck="false"
        class="w-full rounded-md bg-white px-3 py-1.5 font-mono text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        @keydown.meta.enter="handleExecute"
        @keydown.ctrl.enter="handleExecute"
      />
      <div class="mt-2 flex items-center gap-2">
        <button
          @click="handleExecute"
          :disabled="!queryText.trim() || isExecuting"
          class="flex items-center gap-2 rounded-md bg-green-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-green-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-green-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-green-500 dark:shadow-none dark:hover:bg-green-400"
        >
          <svg v-if="isExecuting" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{{ isExecuting ? 'Executing...' : 'Execute' }}</span>
        </button>
        <button
          @click="clearQuery"
          class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 text-sm"
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
    <div v-if="resultColumns.length > 0" class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 overflow-hidden">
      <!-- Tab Bar -->
      <div class="border-b border-gray-200 dark:border-white/10">
        <nav class="flex -mb-px">
          <button
            @click="activeTab = 'table'"
            :class="[
              'px-4 py-3 text-sm font-medium border-b-2 focus:outline-none',
              activeTab === 'table'
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
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
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
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
                    ? 'bg-indigo-600 text-white'
                    : 'bg-gray-100 dark:bg-white/10 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-white/20',
                ]"
              >
                {{ ct }}
              </button>
            </div>
          </div>

          <!-- X / Category Column -->
          <Listbox as="div" v-model="chartXColumn" class="min-w-[140px]">
            <ListboxLabel class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              {{ chartType === 'pie' ? 'Name' : 'X Axis' }}
            </ListboxLabel>
            <div class="relative">
              <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
                <span class="col-start-1 row-start-1 truncate pr-6">{{ chartXColumn || '-- select --' }}</span>
                <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
              </ListboxButton>
              <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
                <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
                  <ListboxOption :value="''" as="template" v-slot="{ active, selected }">
                    <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                      <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">-- select --</span>
                      <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                        <CheckIcon class="size-5" aria-hidden="true" />
                      </span>
                    </li>
                  </ListboxOption>
                  <ListboxOption v-for="col in resultColumns" :key="col" :value="col" as="template" v-slot="{ active, selected }">
                    <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                      <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ col }}</span>
                      <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                        <CheckIcon class="size-5" aria-hidden="true" />
                      </span>
                    </li>
                  </ListboxOption>
                </ListboxOptions>
              </transition>
            </div>
          </Listbox>

          <!-- Y / Value Columns -->
          <div class="min-w-[200px]">
            <template v-if="chartType === 'pie'">
              <Listbox as="div" v-model="chartYColumnSingle">
                <ListboxLabel class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Value</ListboxLabel>
                <div class="relative">
                  <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
                    <span class="col-start-1 row-start-1 truncate pr-6">{{ chartYColumnSingle || '-- select --' }}</span>
                    <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
                  </ListboxButton>
                  <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
                    <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
                      <ListboxOption :value="''" as="template" v-slot="{ active, selected }">
                        <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                          <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">-- select --</span>
                          <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                            <CheckIcon class="size-5" aria-hidden="true" />
                          </span>
                        </li>
                      </ListboxOption>
                      <ListboxOption v-for="col in numericColumns" :key="col" :value="col" as="template" v-slot="{ active, selected }">
                        <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                          <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ col }}</span>
                          <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                            <CheckIcon class="size-5" aria-hidden="true" />
                          </span>
                        </li>
                      </ListboxOption>
                    </ListboxOptions>
                  </transition>
                </div>
              </Listbox>
            </template>
            <template v-else>
              <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                Y Axis
                <span class="font-normal">(multi-select)</span>
              </label>
              <select
                v-model="chartYColumns"
                multiple
                :size="Math.min(numericColumns.length, 4)"
                class="w-full rounded-md bg-white px-3 py-1 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
              >
                <option v-for="col in numericColumns" :key="col" :value="col">{{ col }}</option>
              </select>
            </template>
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
    <div v-else-if="!errorMessage && !isExecuting" class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 p-6">
      <div class="text-center py-8 text-gray-500 dark:text-gray-400">
        <p class="text-sm">
          Write {{ queryLanguage === 'sqlite' ? 'an SQL' : 'a BQL' }} query or ask a question above, then execute to see results here.
        </p>
        <p class="text-xs mt-2 text-gray-400 dark:text-gray-500">
          Tip: Press Cmd+Enter (or Ctrl+Enter) to execute quickly.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { ref, computed, watch } from 'vue'
  import { Listbox, ListboxButton, ListboxLabel, ListboxOption, ListboxOptions } from '@headlessui/vue'
  import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
  import { CheckIcon } from '@heroicons/vue/20/solid'
  import { generateQuery, type QueryLanguage } from '@/services/sqlAssistant'
  import { LedgerService, ApiError } from '@/services/generated-api'
  import type { QueryRequest } from '@/services/generated-api'
  import { useToast } from '@/composables/useNotifications'
  import { useConfig } from '@/composables/useConfig'
  import RecipeChart from '@/components/recipes/RecipeChart.vue'
  import { SUPPORTED_CHART_TYPES, type ChartType } from '@/types/recipes'
  import type { EChartsOption } from 'echarts'

  const toast = useToast()
  const { config } = useConfig()

  // --- Language mode ---
  const queryLanguage = ref<QueryLanguage>('sqlite')

  const generateButtonLabel = computed(() =>
    queryLanguage.value === 'sqlite' ? 'Generate SQL' : 'Generate BQL'
  )

  const queryPlaceholder = computed(() =>
    queryLanguage.value === 'sqlite'
      ? 'SELECT account, SUM(amount) as total FROM postings WHERE account_type = \'Expenses\' GROUP BY account ORDER BY total DESC LIMIT 10'
      : 'SELECT account, COST(SUM(position)) AS total WHERE account ~ \'Expenses\' GROUP BY 1 ORDER BY 2 DESC LIMIT 10'
  )

  // --- Query state ---
  const nlQuery = ref('')
  const queryText = ref('')
  const isGenerating = ref(false)
  const isExecuting = ref(false)
  const errorMessage = ref('')
  const resultColumns = ref<string[]>([])
  const resultRows = ref<Record<string, unknown>[]>([])
  const executionTime = ref<number | null>(null)

  // --- Results tab state ---
  const activeTab = ref<'table' | 'chart'>('table')

  // --- Chart config state ---
  const chartTypes = SUPPORTED_CHART_TYPES
  const chartType = ref<ChartType>('bar')
  const chartXColumn = ref('')
  const chartYColumns = ref<string[]>([])
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

  async function handleGenerate() {
    if (!nlQuery.value.trim() || isGenerating.value) return

    isGenerating.value = true
    errorMessage.value = ''
    try {
      const llm = config.value?.ai?.llm
      const llmConfig = llm?.api_url ? { apiUrl: llm.api_url, apiKey: llm.api_key || undefined, model: llm.model || undefined, temperature: llm.temperature, maxTokens: llm.max_tokens } : undefined
      const query = await generateQuery(nlQuery.value.trim(), queryLanguage.value, llmConfig)
      queryText.value = query
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to generate query'
      errorMessage.value = msg
      toast.error('Generation Failed', msg)
    } finally {
      isGenerating.value = false
    }
  }

  async function handleExecute() {
    if (!queryText.value.trim() || isExecuting.value) return

    isExecuting.value = true
    errorMessage.value = ''
    resultColumns.value = []
    resultRows.value = []
    executionTime.value = null

    const startTime = performance.now()

    try {
      const request: QueryRequest = { query: queryText.value.trim() }
      const response = await LedgerService.executeQuery(request, queryLanguage.value)

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

      if (data.columns && Array.isArray(data.columns) && data.columns.length > 0) {
        resultColumns.value = data.columns.map((c) => typeof c === 'string' ? c : c.name)
      } else if (rows.length > 0) {
        resultColumns.value = Object.keys(rows[0])
      } else {
        resultColumns.value = []
      }
    } catch (e: unknown) {
      executionTime.value = Math.round(performance.now() - startTime)
      errorMessage.value = extractErrorMessage(e)
    } finally {
      isExecuting.value = false
    }
  }

  function clearQuery() {
    queryText.value = ''
    errorMessage.value = ''
    resultColumns.value = []
    resultRows.value = []
    executionTime.value = null
  }

  /** Extract a detailed error message from ApiError or fall back to generic message */
  function extractErrorMessage(e: unknown): string {
    if (e instanceof ApiError && e.body?.error) {
      const detail = e.body.error.details?.error
      const message = e.body.error.message
      // Prefer the raw DB error detail, fall back to the structured message
      return detail || message || e.message
    }
    return e instanceof Error ? e.message : 'Query execution failed'
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
