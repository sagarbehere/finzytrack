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
            class="block w-full rounded-md bg-white py-1.5 pr-3 pl-10 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
          />
        </div>
      </div>

      <!-- Type Filter -->
      <Listbox as="div" v-model="localFilters.type" class="min-w-[150px]">
        <ListboxLabel class="sr-only">Filter by type</ListboxLabel>
        <div class="relative">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ typeOptions.find(o => o.value === localFilters.type)?.label || 'All Types' }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in typeOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
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

      <!-- Status Filter -->
      <Listbox as="div" v-model="localFilters.status" class="min-w-[130px]">
        <ListboxLabel class="sr-only">Filter by status</ListboxLabel>
        <div class="relative">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ statusOptions.find(o => o.value === localFilters.status)?.label || 'All Status' }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in statusOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
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

      <!-- New Account Button -->
      <button
        @click="emit('create')"
        class="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
      >
        <PlusIcon class="h-5 w-5" />
        New Account
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, reactive } from 'vue'
import { Listbox, ListboxButton, ListboxLabel, ListboxOption, ListboxOptions } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
import { CheckIcon } from '@heroicons/vue/20/solid'
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

const typeOptions = [
  { value: 'All', label: 'All Types' },
  { value: 'Assets', label: 'Assets' },
  { value: 'Liabilities', label: 'Liabilities' },
  { value: 'Equity', label: 'Equity' },
  { value: 'Income', label: 'Income' },
  { value: 'Expenses', label: 'Expenses' },
]

const statusOptions = [
  { value: 'All', label: 'All Status' },
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
]

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
