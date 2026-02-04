<template>
  <div class="space-y-4">
    <!-- Date Range Row -->
    <div class="flex flex-wrap items-center gap-3">
      <span class="text-sm font-medium text-gray-700 dark:text-gray-300">Period:</span>

      <!-- Preset Buttons -->
      <div class="flex gap-1">
        <button
          v-for="preset in datePresets"
          :key="preset.label"
          @click="applyPreset(preset)"
          :class="[
            'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
            isPresetActive(preset)
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600'
          ]"
        >
          {{ preset.label }}
        </button>
      </div>

      <!-- Custom Date Inputs -->
      <div class="flex items-center gap-2 ml-2">
        <input
          v-model="localDateFilter.startDate"
          type="date"
          class="px-2 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
          @change="onDateChange"
        />
        <span class="text-gray-500 dark:text-gray-400">to</span>
        <input
          v-model="localDateFilter.endDate"
          type="date"
          class="px-2 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
          @change="onDateChange"
        />
      </div>
    </div>

    <!-- Filter Row -->
    <div class="flex flex-wrap items-center gap-4">
      <!-- Search Input -->
      <div class="flex-1 min-w-[200px]">
        <label for="search" class="sr-only">Search accounts</label>
        <div class="relative">
          <MagnifyingGlassIcon class="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400" />
          <input
            id="search"
            v-model="searchInput"
            type="text"
            placeholder="Search accounts..."
            class="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-md text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white dark:placeholder-gray-400"
          />
        </div>
      </div>

      <!-- Type Filter -->
      <div class="min-w-[150px]">
        <label for="type-filter" class="sr-only">Filter by type</label>
        <select
          id="type-filter"
          v-model="localFilters.type"
          class="block w-full py-2 px-3 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        >
          <option value="All">All Types</option>
          <option value="Assets">Assets</option>
          <option value="Liabilities">Liabilities</option>
          <option value="Equity">Equity</option>
          <option value="Income">Income</option>
          <option value="Expenses">Expenses</option>
        </select>
      </div>

      <!-- Status Filter -->
      <div class="min-w-[130px]">
        <label for="status-filter" class="sr-only">Filter by status</label>
        <select
          id="status-filter"
          v-model="localFilters.status"
          class="block w-full py-2 px-3 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
        >
          <option value="All">All Status</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </select>
      </div>

      <!-- New Account Button -->
      <button
        @click="emit('create')"
        class="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-gray-800"
      >
        <PlusIcon class="h-5 w-5" />
        New Account
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, reactive, computed } from 'vue'
import { MagnifyingGlassIcon, PlusIcon } from '@heroicons/vue/24/outline'
import type { AccountFilters } from '@/types/accounts'

interface DateFilter {
  startDate: string | null
  endDate: string | null
}

interface DatePreset {
  label: string
  getRange: () => { startDate: string | null; endDate: string | null }
}

interface Props {
  filters: AccountFilters
  dateFilter: DateFilter
}

interface Emits {
  (e: 'update:filters', filters: AccountFilters): void
  (e: 'update:dateFilter', dateFilter: DateFilter): void
  (e: 'create'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

// Local reactive copy of filters
const localFilters = reactive<AccountFilters>({
  search: props.filters.search,
  type: props.filters.type,
  status: props.filters.status
})

// Local date filter state
const localDateFilter = reactive<DateFilter>({
  startDate: props.dateFilter.startDate,
  endDate: props.dateFilter.endDate
})

// Separate ref for debounced search
const searchInput = ref(props.filters.search)

// Date helper functions
function getToday(): string {
  return new Date().toISOString().split('T')[0]
}

function getFirstDayOfMonth(): string {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().split('T')[0]
}

function getFirstDayOfQuarter(): string {
  const now = new Date()
  const quarter = Math.floor(now.getMonth() / 3)
  return new Date(now.getFullYear(), quarter * 3, 1).toISOString().split('T')[0]
}

function getFirstDayOfYear(): string {
  const now = new Date()
  return new Date(now.getFullYear(), 0, 1).toISOString().split('T')[0]
}

// Date presets
const datePresets: DatePreset[] = [
  {
    label: 'All Time',
    getRange: () => ({ startDate: null, endDate: null })
  },
  {
    label: 'YTD',
    getRange: () => ({ startDate: getFirstDayOfYear(), endDate: getToday() })
  },
  {
    label: 'This Quarter',
    getRange: () => ({ startDate: getFirstDayOfQuarter(), endDate: getToday() })
  },
  {
    label: 'This Month',
    getRange: () => ({ startDate: getFirstDayOfMonth(), endDate: getToday() })
  }
]

function applyPreset(preset: DatePreset) {
  const range = preset.getRange()
  localDateFilter.startDate = range.startDate
  localDateFilter.endDate = range.endDate
  emit('update:dateFilter', { ...localDateFilter })
}

function isPresetActive(preset: DatePreset): boolean {
  const range = preset.getRange()
  return localDateFilter.startDate === range.startDate && localDateFilter.endDate === range.endDate
}

function onDateChange() {
  emit('update:dateFilter', { ...localDateFilter })
}

// Debounce search input
let searchTimeout: ReturnType<typeof setTimeout> | null = null
watch(searchInput, (newValue) => {
  if (searchTimeout) clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => {
    localFilters.search = newValue
  }, 300)
})

// Watch for filter changes and emit
watch(localFilters, (newFilters) => {
  emit('update:filters', { ...newFilters })
}, { deep: true })

// Sync props changes back to local state
watch(() => props.filters, (newFilters) => {
  localFilters.search = newFilters.search
  localFilters.type = newFilters.type
  localFilters.status = newFilters.status
  searchInput.value = newFilters.search
}, { deep: true })

watch(() => props.dateFilter, (newDateFilter) => {
  localDateFilter.startDate = newDateFilter.startDate
  localDateFilter.endDate = newDateFilter.endDate
}, { deep: true })
</script>
