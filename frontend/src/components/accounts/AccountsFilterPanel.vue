<template>
  <div class="space-y-4">
    <!-- Date Range Row -->
    <div class="flex flex-wrap items-center gap-3">
      <span class="text-sm font-medium text-gray-700 dark:text-gray-300">Period:</span>

      <!-- Quick Access Buttons -->
      <div class="flex gap-1">
        <button
          v-for="preset in quickPresets"
          :key="preset.label"
          @click="applyPreset(preset)"
          :class="[
            'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
            isPresetActive(preset.label)
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600'
          ]"
        >
          {{ preset.label }}
        </button>

        <!-- More Dropdown -->
        <Menu as="div" class="relative" v-slot="{ close }">
          <MenuButton
            :class="[
              'px-3 py-1.5 text-xs font-medium rounded-md transition-colors inline-flex items-center gap-1',
              isDropdownPresetActive
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600'
            ]"
          >
            {{ activeDropdownLabel || 'More' }}
            <ChevronDownIcon class="h-3 w-3" />
          </MenuButton>

          <transition
            enter-active-class="transition duration-100 ease-out"
            enter-from-class="transform scale-95 opacity-0"
            enter-to-class="transform scale-100 opacity-100"
            leave-active-class="transition duration-75 ease-in"
            leave-from-class="transform scale-100 opacity-100"
            leave-to-class="transform scale-95 opacity-0"
          >
            <MenuItems class="absolute left-0 mt-1 w-52 origin-top-left rounded-md bg-white dark:bg-gray-800 shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none z-10">
              <!-- Previous Section -->
              <div class="px-3 py-1.5 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Previous
              </div>
              <MenuItem v-for="preset in dropdownPresets.previous" :key="preset.label" v-slot="{ active }">
                <button
                  @click="applyPreset(preset)"
                  :class="[
                    'block w-full text-left px-3 py-1.5 text-sm',
                    active ? 'bg-gray-100 dark:bg-gray-700' : '',
                    isPresetActive(preset.label) ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-700 dark:text-gray-300'
                  ]"
                >
                  {{ preset.label }}
                </button>
              </MenuItem>

              <!-- Rolling Section -->
              <div class="border-t border-gray-200 dark:border-gray-700 mt-1 pt-1">
                <div class="px-3 py-1.5 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Rolling
                </div>
              </div>
              <MenuItem v-for="preset in dropdownPresets.rolling" :key="preset.label" v-slot="{ active }">
                <button
                  @click="applyPreset(preset)"
                  :class="[
                    'block w-full text-left px-3 py-1.5 text-sm',
                    active ? 'bg-gray-100 dark:bg-gray-700' : '',
                    isPresetActive(preset.label) ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-700 dark:text-gray-300'
                  ]"
                >
                  {{ preset.label }}
                </button>
              </MenuItem>

              <!-- Custom Rolling Input -->
              <div class="border-t border-gray-200 dark:border-gray-700 mt-1 pt-2 px-3 pb-2">
                <div class="flex items-center gap-1.5">
                  <span class="text-sm text-gray-700 dark:text-gray-300">Last</span>
                  <input
                    v-model.number="customRollingDays"
                    type="number"
                    min="1"
                    max="9999"
                    class="w-14 px-1.5 py-1 text-sm border border-gray-300 rounded dark:bg-gray-700 dark:border-gray-600 dark:text-white text-center"
                    @click.stop
                    @keydown.enter="applyCustomRolling(); close()"
                  />
                  <span class="text-sm text-gray-700 dark:text-gray-300">days</span>
                  <button
                    @click="applyCustomRolling(); close()"
                    class="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700"
                  >
                    Go
                  </button>
                </div>
              </div>
            </MenuItems>
          </transition>
        </Menu>
      </div>

      <!-- Custom Date Inputs -->
      <div class="flex items-center gap-2 ml-2">
        <span class="text-sm text-gray-500 dark:text-gray-400">From:</span>
        <input
          v-model="localDateFilter.startDate"
          type="date"
          class="px-2 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
          @change="onDateChange"
        />
        <span class="text-sm text-gray-500 dark:text-gray-400">To:</span>
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
import { Menu, MenuButton, MenuItems, MenuItem } from '@headlessui/vue'
import { MagnifyingGlassIcon, PlusIcon, ChevronDownIcon } from '@heroicons/vue/24/outline'
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

// Track which preset is active (to avoid ambiguity when date ranges overlap)
const activePreset = ref<string | null>('All Time')

// Custom rolling days input
const customRollingDays = ref<number>(60)

// Separate ref for debounced search
const searchInput = ref(props.filters.search)

// =====================
// Date Helper Functions
// =====================

// Format a Date object as YYYY-MM-DD in local timezone (avoids UTC conversion issues)
function formatLocalDate(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function getToday(): string {
  return formatLocalDate(new Date())
}

function getFirstDayOfMonth(): string {
  const now = new Date()
  return formatLocalDate(new Date(now.getFullYear(), now.getMonth(), 1))
}

function getFirstDayOfYear(): string {
  return `${new Date().getFullYear()}-01-01`
}

function getLastMonth(): { startDate: string; endDate: string } {
  const now = new Date()
  const firstDay = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  const lastDay = new Date(now.getFullYear(), now.getMonth(), 0)
  return {
    startDate: formatLocalDate(firstDay),
    endDate: formatLocalDate(lastDay)
  }
}

function getLastQuarter(): { startDate: string; endDate: string } {
  const now = new Date()
  const currentQuarter = Math.floor(now.getMonth() / 3)
  let lastQuarterStart: Date
  let lastQuarterEnd: Date

  if (currentQuarter === 0) {
    // Currently in Q1, last quarter is Q4 of previous year
    lastQuarterStart = new Date(now.getFullYear() - 1, 9, 1)
    lastQuarterEnd = new Date(now.getFullYear() - 1, 11, 31)
  } else {
    lastQuarterStart = new Date(now.getFullYear(), (currentQuarter - 1) * 3, 1)
    lastQuarterEnd = new Date(now.getFullYear(), currentQuarter * 3, 0)
  }

  return {
    startDate: formatLocalDate(lastQuarterStart),
    endDate: formatLocalDate(lastQuarterEnd)
  }
}

function getLastYear(): { startDate: string; endDate: string } {
  const now = new Date()
  const year = now.getFullYear() - 1
  return {
    startDate: `${year}-01-01`,
    endDate: `${year}-12-31`
  }
}

function getLastNDays(n: number): { startDate: string; endDate: string } {
  const now = new Date()
  const past = new Date(now)
  past.setDate(past.getDate() - n)
  return {
    startDate: formatLocalDate(past),
    endDate: formatLocalDate(now)
  }
}

// =====================
// Preset Definitions
// =====================

// Quick access presets (shown as buttons)
const quickPresets: DatePreset[] = [
  { label: 'All Time', getRange: () => ({ startDate: null, endDate: null }) },
  { label: 'YTD', getRange: () => ({ startDate: getFirstDayOfYear(), endDate: getToday() }) },
  { label: 'This Month', getRange: () => ({ startDate: getFirstDayOfMonth(), endDate: getToday() }) },
]

// Dropdown presets (grouped)
const dropdownPresets = {
  previous: [
    { label: 'Last Month', getRange: () => getLastMonth() },
    { label: 'Last Quarter', getRange: () => getLastQuarter() },
    { label: 'Last Year', getRange: () => getLastYear() },
  ] as DatePreset[],
  rolling: [
    { label: 'Last 30 Days', getRange: () => getLastNDays(30) },
    { label: 'Last 90 Days', getRange: () => getLastNDays(90) },
  ] as DatePreset[]
}

// All dropdown preset labels for checking active state
const allDropdownLabels = [
  ...dropdownPresets.previous.map(p => p.label),
  ...dropdownPresets.rolling.map(p => p.label),
]

// =====================
// Computed Properties
// =====================

// Check if any dropdown preset is active
const isDropdownPresetActive = computed(() => {
  const current = activePreset.value
  if (!current) return false

  // Check if it's a predefined dropdown preset
  if (allDropdownLabels.includes(current)) return true

  // Check if it's a custom rolling period (e.g., "Last 45 Days")
  if (current.startsWith('Last ') && current.endsWith(' Days')) {
    // Make sure it's not one of the quick presets
    const quickLabels = quickPresets.map(p => p.label)
    return !quickLabels.includes(current)
  }

  return false
})

// Get active dropdown label for display on the button
const activeDropdownLabel = computed(() => {
  if (isDropdownPresetActive.value) {
    return activePreset.value
  }
  return null
})

// =====================
// Actions
// =====================

function applyPreset(preset: DatePreset) {
  const range = preset.getRange()
  localDateFilter.startDate = range.startDate
  localDateFilter.endDate = range.endDate
  activePreset.value = preset.label
  emit('update:dateFilter', { ...localDateFilter })
}

function applyCustomRolling() {
  if (customRollingDays.value < 1) return

  const range = getLastNDays(customRollingDays.value)
  localDateFilter.startDate = range.startDate
  localDateFilter.endDate = range.endDate
  activePreset.value = `Last ${customRollingDays.value} Days`
  emit('update:dateFilter', { ...localDateFilter })
}

function isPresetActive(label: string): boolean {
  return activePreset.value === label
}

function onDateChange() {
  // Clear preset selection when dates are manually changed
  activePreset.value = null
  emit('update:dateFilter', { ...localDateFilter })
}

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

watch(() => props.dateFilter, (newDateFilter) => {
  localDateFilter.startDate = newDateFilter.startDate
  localDateFilter.endDate = newDateFilter.endDate
}, { deep: true })
</script>
