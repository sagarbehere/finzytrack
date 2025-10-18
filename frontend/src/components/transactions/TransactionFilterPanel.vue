<template>
  <div class="transaction-filter-panel">
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <!-- Date From -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Date From
        </label>
        <input
          v-model="filters.dateFrom"
          type="date"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Date To -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Date To
        </label>
        <input
          v-model="filters.dateTo"
          type="date"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Database Type -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Database
        </label>
        <select
          v-model="dbType"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        >
          <option value="sqlite">SQLite</option>
          <option value="duckdb">DuckDB</option>
        </select>
      </div>

      <!-- Amount Greater Than -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Amount Greater Than
        </label>
        <input
          v-model.number="filters.amountGreaterThan"
          type="number"
          step="0.01"
          placeholder="e.g., 100.00"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Amount Less Than -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Amount Less Than
        </label>
        <input
          v-model.number="filters.amountLessThan"
          type="number"
          step="0.01"
          placeholder="e.g., 500.00"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Payee Contains -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Payee Contains
        </label>
        <input
          v-model="filters.payeeContains"
          type="text"
          placeholder="Search payee..."
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Narration Contains -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Narration Contains
        </label>
        <input
          v-model="filters.narrationContains"
          type="text"
          placeholder="Search narration..."
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Account Contains -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Account Contains
        </label>
        <input
          v-model="filters.accountContains"
          type="text"
          placeholder="Search account..."
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Tags Contains -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Tags Contains
        </label>
        <input
          v-model="filters.tagsContain"
          type="text"
          placeholder="Search tags..."
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Links Contains -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Links Contains
        </label>
        <input
          v-model="filters.linksContain"
          type="text"
          placeholder="Search links..."
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Currency Filter -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Currency
        </label>
        <input
          v-model="filters.currency"
          type="text"
          placeholder="e.g., USD, EUR"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Flag Filter -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Flag
        </label>
        <select
          v-model="filters.flag"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        >
          <option value="">Any Flag</option>
          <option value="*">* (Cleared)</option>
          <option value="!">! (Pending)</option>
        </select>
      </div>

      <!-- Account Type Filter -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Account Type
        </label>
        <select
          v-model="filters.accountType"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        >
          <option value="">All Types</option>
          <option value="Assets">Assets</option>
          <option value="Liabilities">Liabilities</option>
          <option value="Expenses">Expenses</option>
          <option value="Income">Income</option>
          <option value="Equity">Equity</option>
        </select>
      </div>

      <!-- Year Filter -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Year
        </label>
        <input
          v-model.number="filters.year"
          type="number"
          min="2000"
          max="2100"
          placeholder="e.g., 2024"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>

      <!-- Quarter Filter -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Quarter
        </label>
        <select
          v-model.number="filters.quarter"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        >
          <option :value="undefined">All Quarters</option>
          <option :value="1">Q1 (Jan-Mar)</option>
          <option :value="2">Q2 (Apr-Jun)</option>
          <option :value="3">Q3 (Jul-Sep)</option>
          <option :value="4">Q4 (Oct-Dec)</option>
        </select>
      </div>

      <!-- Max Results -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Max Results
        </label>
        <input
          v-model.number="limit"
          type="number"
          min="1"
          max="50000"
          step="1"
          placeholder="e.g., 1000"
          class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        />
      </div>
    </div>

    <!-- Action Buttons -->
    <div class="mt-4 flex justify-end gap-3">
      <button
        @click="handleClear"
        class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 dark:bg-gray-700 dark:text-gray-200 dark:border-gray-600 dark:hover:bg-gray-600"
      >
        Clear Filters
      </button>
      <button
        @click="handleApply"
        :disabled="loading"
        class="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ loading ? 'Querying...' : 'Apply Filters' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { TransactionFilters } from '@/types/filters'

interface Props {
  loading?: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'filter-changed', filters: TransactionFilters, dbType: 'duckdb' | 'sqlite', limit: number): void
}>()

// Database type selection (SQLite as default)
const dbType = ref<'duckdb' | 'sqlite'>('sqlite')

// Result limit (default 1000, min 1, max 50000)
const limit = ref<number>(1000)

// Initialize with last 90 days
const filters = ref<TransactionFilters>({
  dateFrom: getDate90DaysAgo(),
  dateTo: getTodayDate(),
  amountGreaterThan: undefined,
  amountLessThan: undefined,
  payeeContains: '',
  narrationContains: '',
  accountContains: '',
  tagsContain: '',
  linksContain: '',
  currency: '',
  flag: '',
  accountType: '',
  year: undefined,
  quarter: undefined,
})

function handleApply() {
  // Validate and clamp limit to reasonable bounds
  let validatedLimit = limit.value
  if (isNaN(validatedLimit) || validatedLimit < 1) {
    validatedLimit = 1000 // Default to 1000 if invalid
    limit.value = validatedLimit
  } else if (validatedLimit > 50000) {
    validatedLimit = 50000 // Cap at 50000 to prevent performance issues
    limit.value = validatedLimit
  }

  emit('filter-changed', { ...filters.value }, dbType.value, validatedLimit)
}

function handleClear() {
  filters.value = {
    dateFrom: '',
    dateTo: '',
    amountGreaterThan: undefined,
    amountLessThan: undefined,
    payeeContains: '',
    narrationContains: '',
    accountContains: '',
    tagsContain: '',
    linksContain: '',
    currency: '',
    flag: '',
    accountType: '',
    year: undefined,
    quarter: undefined,
  }
  // Don't auto-apply - let user click "Apply Filters" when ready
}

// Auto-apply on date changes
watch(() => [filters.value.dateFrom, filters.value.dateTo], () => {
  // Only auto-apply if both dates are set (not empty)
  // This prevents auto-apply when dates are cleared or only one is set
  if (filters.value.dateFrom && filters.value.dateTo) {
    handleApply()
  }
})

function getDate90DaysAgo(): string {
  const date = new Date()
  date.setDate(date.getDate() - 90)
  return date.toISOString().split('T')[0]
}

function getTodayDate(): string {
  return new Date().toISOString().split('T')[0]
}
</script>
