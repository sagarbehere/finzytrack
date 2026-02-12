<template>
  <div class="space-y-4">
    <!-- Date Range Row -->
    <DatePresetSelector
      :start-date="dateFilter.startDate"
      :end-date="dateFilter.endDate"
      :active-preset="activePreset"
      @update:active-preset="emit('update:activePreset', $event)"
      @change="emit('update:dateFilter', $event)"
    />

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
            type="search"
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
import { ref, watch, reactive } from 'vue'
import { MagnifyingGlassIcon, PlusIcon } from '@heroicons/vue/24/outline'
import DatePresetSelector from '@/components/common/DatePresetSelector.vue'
import type { AccountFilters } from '@/types/accounts'

interface DateFilter {
  startDate: string | null
  endDate: string | null
}

interface Props {
  filters: AccountFilters
  dateFilter: DateFilter
  activePreset: string | null
}

interface Emits {
  (e: 'update:filters', filters: AccountFilters): void
  (e: 'update:dateFilter', dateFilter: DateFilter): void
  (e: 'update:activePreset', preset: string | null): void
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

// Separate ref for debounced search
const searchInput = ref(props.filters.search)

// =====================
// Watchers
// =====================

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
</script>
