<template>
  <div class="transaction-filter-panel">
    <!-- Date Preset Selector -->
    <DatePresetSelector
      :start-date="filters.dateFrom || null"
      :end-date="filters.dateTo || null"
      :active-preset="activePreset"
      auto-apply
      @update:active-preset="activePreset = $event"
      @change="handleDatePresetChange"
    />

    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
      <!-- Search (payee or narration) -->
      <div class="md:col-span-2 lg:col-span-3">
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Search
        </label>
        <input
          v-model="filters.search"
          type="text"
          placeholder="Search payee or narration..."
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Amount Greater Than -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Amount Greater Than
        </label>
        <input
          v-model.number="filters.amountGreaterThan"
          type="number"
          step="0.01"
          placeholder="e.g., 100.00"
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Amount Less Than -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Amount Less Than
        </label>
        <input
          v-model.number="filters.amountLessThan"
          type="number"
          step="0.01"
          placeholder="e.g., 500.00"
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Payee Contains -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Payee Contains
        </label>
        <input
          v-model="filters.payeeContains"
          type="text"
          placeholder="Search payee..."
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Narration Contains -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Narration Contains
        </label>
        <input
          v-model="filters.narrationContains"
          type="text"
          placeholder="Search narration..."
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Account Contains -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Account Contains
        </label>
        <input
          v-model="filters.accountContains"
          type="text"
          placeholder="Search account..."
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Tags Contains -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Tags Contains
        </label>
        <input
          v-model="filters.tagsContain"
          type="text"
          placeholder="Search tags..."
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Links Contains -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Links Contains
        </label>
        <input
          v-model="filters.linksContain"
          type="text"
          placeholder="Search links..."
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Currency Filter -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Currency
        </label>
        <input
          v-model="filters.currency"
          type="text"
          placeholder="e.g., USD, EUR"
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Flag Filter -->
      <Listbox as="div" v-model="filters.flag">
        <ListboxLabel class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Flag
        </ListboxLabel>
        <div class="relative mt-1">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ flagOptions.find(o => o.value === filters.flag)?.label || 'Any Flag' }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in flagOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
                <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                  <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ opt.label }}</span>
                  <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                    <CheckIcon class="size-5" aria-hidden="true" />
                  </span>
                </li>
              </ListboxOption>
            </ListboxOptions>
          </transition>
        </div>
      </Listbox>

      <!-- Account Type Filter -->
      <Listbox as="div" v-model="filters.accountType">
        <ListboxLabel class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Account Type
        </ListboxLabel>
        <div class="relative mt-1">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ accountTypeOptions.find(o => o.value === filters.accountType)?.label || 'All Types' }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in accountTypeOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
                <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                  <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ opt.label }}</span>
                  <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                    <CheckIcon class="size-5" aria-hidden="true" />
                  </span>
                </li>
              </ListboxOption>
            </ListboxOptions>
          </transition>
        </div>
      </Listbox>

      <!-- Year Filter -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Year
        </label>
        <input
          v-model.number="filters.year"
          type="number"
          min="2000"
          max="2100"
          placeholder="e.g., 2024"
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>

      <!-- Quarter Filter -->
      <Listbox as="div" :model-value="filters.quarter" @update:model-value="filters.quarter = $event">
        <ListboxLabel class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Quarter
        </ListboxLabel>
        <div class="relative mt-1">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ quarterOptions.find(o => o.value === filters.quarter)?.label || 'All Quarters' }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in quarterOptions" :key="String(opt.value)" :value="opt.value" as="template" v-slot="{ active, selected }">
                <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                  <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ opt.label }}</span>
                  <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                    <CheckIcon class="size-5" aria-hidden="true" />
                  </span>
                </li>
              </ListboxOption>
            </ListboxOptions>
          </transition>
        </div>
      </Listbox>

      <!-- Max Results -->
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
          Max Results
        </label>
        <input
          v-model.number="limit"
          type="number"
          min="1"
          max="50000"
          step="1"
          placeholder="e.g., 1000"
          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>
    </div>

    <!-- Action Buttons -->
    <div class="mt-4 flex justify-end gap-3">
      <button
        @click="handleClear"
        class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
      >
        Clear Filters
      </button>
      <button
        @click="handleApply"
        :disabled="loading"
        class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
      >
        {{ loading ? 'Querying...' : 'Apply Filters' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { Listbox, ListboxButton, ListboxLabel, ListboxOption, ListboxOptions } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
import { CheckIcon } from '@heroicons/vue/20/solid'
import DatePresetSelector from '@/components/common/DatePresetSelector.vue'
import type { TransactionFilters } from '@/types/filters'

const flagOptions = [
  { value: '', label: 'Any Flag' },
  { value: '*', label: '* (Cleared)' },
  { value: '!', label: '! (Pending)' },
]

const accountTypeOptions = [
  { value: '', label: 'All Types' },
  { value: 'Assets', label: 'Assets' },
  { value: 'Liabilities', label: 'Liabilities' },
  { value: 'Expenses', label: 'Expenses' },
  { value: 'Income', label: 'Income' },
  { value: 'Equity', label: 'Equity' },
]

const quarterOptions: { value: number | undefined; label: string }[] = [
  { value: undefined, label: 'All Quarters' },
  { value: 1, label: 'Q1 (Jan-Mar)' },
  { value: 2, label: 'Q2 (Apr-Jun)' },
  { value: 3, label: 'Q3 (Jul-Sep)' },
  { value: 4, label: 'Q4 (Oct-Dec)' },
]

interface Props {
  loading?: boolean
  initialFilters?: TransactionFilters
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'filter-changed', filters: TransactionFilters, limit: number): void
}>()

// Result limit (default 1000, min 1, max 50000)
const limit = ref<number>(1000)

// Track active date preset
const activePreset = ref<string | null>(props.initialFilters ? null : 'Last 90 Days')

// Default filter values
function getDefaultFilters(): TransactionFilters {
  return {
    dateFrom: getDate90DaysAgo(),
    dateTo: getTodayDate(),
    amountGreaterThan: undefined,
    amountLessThan: undefined,
    search: '',
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
}

// Initialize filters - will be populated on mount with initialFilters if provided
const filters = ref<TransactionFilters>(getDefaultFilters())

function handleDatePresetChange(range: { startDate: string | null; endDate: string | null }) {
  filters.value.dateFrom = range.startDate ?? ''
  filters.value.dateTo = range.endDate ?? ''
}

function handleApply() {
  // Validate and clamp limit to reasonable bounds
  let validatedLimit = limit.value
  if (isNaN(validatedLimit) || validatedLimit < 1) {
    validatedLimit = 1000
    limit.value = validatedLimit
  } else if (validatedLimit > 50000) {
    validatedLimit = 50000
    limit.value = validatedLimit
  }

  emit('filter-changed', { ...filters.value }, validatedLimit)
}

function handleClear() {
  filters.value = {
    dateFrom: '',
    dateTo: '',
    amountGreaterThan: undefined,
    amountLessThan: undefined,
    search: '',
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
  activePreset.value = null
}

function applyInitialFilters(newFilters: TransactionFilters) {
  const defaults = getDefaultFilters()
  filters.value = {
    ...defaults,
    ...newFilters,
    dateFrom: newFilters.dateFrom || '',
    dateTo: newFilters.dateTo || defaults.dateTo,
  }
  activePreset.value = null
  handleApply()
}

// Apply initial filters on mount if provided
onMounted(() => {
  if (props.initialFilters && Object.keys(props.initialFilters).length > 0) {
    applyInitialFilters(props.initialFilters)
  }
})

// Re-apply when initialFilters changes (same-route navigation, e.g. global search while on Transactions)
watch(() => props.initialFilters, (newFilters) => {
  if (newFilters && Object.keys(newFilters).length > 0) {
    applyInitialFilters(newFilters)
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
