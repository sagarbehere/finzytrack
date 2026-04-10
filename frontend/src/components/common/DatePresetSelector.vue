<template>
  <div class="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
    <span class="text-sm font-medium text-gray-700 dark:text-gray-300">Period:</span>

    <!-- Quick Access Buttons -->
    <div class="flex flex-wrap gap-1">
      <button
        v-for="preset in quickPresets"
        :key="preset.label"
        @click="applyPreset(preset)"
        :class="[
          'px-3 py-1.5 text-sm font-medium rounded-md transition-colors',
          isPresetActive(preset.label)
            ? 'bg-indigo-600 text-white'
            : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-white/10 dark:text-gray-300 dark:hover:bg-white/20'
        ]"
      >
        {{ preset.label }}
      </button>

      <!-- More Dropdown -->
      <Menu as="div" class="relative" v-slot="{ close }">
        <MenuButton
          :class="[
            'px-3 py-1.5 text-sm font-medium rounded-md transition-colors inline-flex items-center gap-1',
            isDropdownPresetActive
              ? 'bg-indigo-600 text-white'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-white/10 dark:text-gray-300 dark:hover:bg-white/20'
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
          <MenuItems class="absolute right-0 sm:left-0 sm:right-auto mt-1 w-52 origin-top-right sm:origin-top-left rounded-md bg-white dark:bg-gray-800 shadow-lg ring-1 ring-black/5 focus:outline-none z-10">
            <!-- Previous Section -->
            <div class="px-3 py-1.5 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
              Previous
            </div>
            <MenuItem v-for="preset in dropdownPresets.previous" :key="preset.label" v-slot="{ active }">
              <button
                @click="applyPreset(preset)"
                :class="[
                  'block w-full text-left px-3 py-1.5 text-sm',
                  active ? 'bg-gray-100 dark:bg-white/5' : '',
                  isPresetActive(preset.label) ? 'text-indigo-600 dark:text-indigo-400 font-medium' : 'text-gray-700 dark:text-gray-300'
                ]"
              >
                {{ preset.label }}
              </button>
            </MenuItem>

            <!-- Rolling Section -->
            <div class="border-t border-gray-200 dark:border-white/10 mt-1 pt-1">
              <div class="px-3 py-1.5 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Rolling
              </div>
            </div>
            <MenuItem v-for="preset in dropdownPresets.rolling" :key="preset.label" v-slot="{ active }">
              <button
                @click="applyPreset(preset)"
                :class="[
                  'block w-full text-left px-3 py-1.5 text-sm',
                  active ? 'bg-gray-100 dark:bg-white/5' : '',
                  isPresetActive(preset.label) ? 'text-indigo-600 dark:text-indigo-400 font-medium' : 'text-gray-700 dark:text-gray-300'
                ]"
              >
                {{ preset.label }}
              </button>
            </MenuItem>

            <!-- Custom Rolling Input -->
            <div class="border-t border-gray-200 dark:border-white/10 mt-1 pt-2 px-3 pb-2">
              <div class="flex items-center gap-1.5">
                <span class="text-sm text-gray-700 dark:text-gray-300">Last</span>
                <input
                  v-model.number="customRollingDays"
                  type="number"
                  min="1"
                  max="9999"
                  class="w-14 rounded-md bg-white px-1.5 py-1 text-sm text-gray-900 text-center outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                  @click.stop
                  @keydown.enter="applyCustomRolling(); close()"
                />
                <span class="text-sm text-gray-700 dark:text-gray-300">days</span>
                <button
                  @click="applyCustomRolling(); close()"
                  class="rounded-md bg-indigo-600 px-2 py-1 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400"
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
    <div class="flex flex-col gap-2 rounded-md outline-1 -outline-offset-1 outline-gray-200 dark:outline-white/10 px-2 py-1.5 sm:ml-2 sm:flex-row sm:items-center sm:py-0.5">
      <div class="flex items-center gap-2">
        <span class="text-sm text-gray-500 dark:text-gray-400 min-w-[3rem]">From:</span>
        <input
          :value="localStartDate"
          type="date"
          class="flex-1 px-1.5 py-1 text-sm bg-transparent border-none focus:outline-none focus:ring-0 dark:text-white"
          @change="onDateChange($event, 'startDate')"
          @keydown.enter="applyDateInputs"
        />
      </div>
      <div class="flex items-center gap-2">
        <span class="text-sm text-gray-500 dark:text-gray-400 min-w-[3rem]">To:</span>
        <input
          :value="localEndDate"
          type="date"
          class="flex-1 px-1.5 py-1 text-sm bg-transparent border-none focus:outline-none focus:ring-0 dark:text-white"
          @change="onDateChange($event, 'endDate')"
          @keydown.enter="applyDateInputs"
        />
      </div>
      <button
        @click="applyDateInputs"
        class="rounded-md bg-indigo-600 px-2 py-1 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 sm:self-auto self-end"
      >
        Go
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { Menu, MenuButton, MenuItems, MenuItem } from '@headlessui/vue'
import { ChevronDownIcon } from '@heroicons/vue/24/outline'

interface DatePreset {
  label: string
  getRange: () => { startDate: string | null; endDate: string | null }
}

interface Props {
  startDate?: string | null
  endDate?: string | null
  activePreset?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  startDate: null,
  endDate: null,
  activePreset: null,
})

const emit = defineEmits<{
  (e: 'update:activePreset', preset: string | null): void
  (e: 'change', range: { startDate: string | null; endDate: string | null }): void
}>()

// Local state
const localStartDate = ref<string | null>(props.startDate)
const localEndDate = ref<string | null>(props.endDate)
const localActivePreset = ref<string | null>(props.activePreset)
const customRollingDays = ref<number>(60)

// =====================
// Date Helper Functions
// =====================

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

const quickPresets: DatePreset[] = [
  { label: 'All Time', getRange: () => ({ startDate: null, endDate: null }) },
  { label: 'YTD', getRange: () => ({ startDate: getFirstDayOfYear(), endDate: getToday() }) },
  { label: 'This Month', getRange: () => ({ startDate: getFirstDayOfMonth(), endDate: getToday() }) },
]

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

const allDropdownLabels = [
  ...dropdownPresets.previous.map(p => p.label),
  ...dropdownPresets.rolling.map(p => p.label),
]

// =====================
// Computed Properties
// =====================

const isDropdownPresetActive = computed(() => {
  const current = localActivePreset.value
  if (!current) return false

  if (allDropdownLabels.includes(current)) return true

  if (current.startsWith('Last ') && current.endsWith(' Days')) {
    const quickLabels = quickPresets.map(p => p.label)
    return !quickLabels.includes(current)
  }

  return false
})

const activeDropdownLabel = computed(() => {
  if (isDropdownPresetActive.value) {
    return localActivePreset.value
  }
  return null
})

// =====================
// Actions
// =====================

function applyPreset(preset: DatePreset) {
  const range = preset.getRange()
  localStartDate.value = range.startDate
  localEndDate.value = range.endDate
  localActivePreset.value = preset.label
  emit('update:activePreset', preset.label)
  emit('change', { startDate: range.startDate, endDate: range.endDate })
}

function applyCustomRolling() {
  if (customRollingDays.value < 1) return

  const range = getLastNDays(customRollingDays.value)
  localStartDate.value = range.startDate
  localEndDate.value = range.endDate
  const presetLabel = `Last ${customRollingDays.value} Days`
  localActivePreset.value = presetLabel
  emit('update:activePreset', presetLabel)
  emit('change', { startDate: range.startDate, endDate: range.endDate })
}

function isPresetActive(label: string): boolean {
  return localActivePreset.value === label
}

function onDateChange(event: Event, field: 'startDate' | 'endDate') {
  const value = (event.target as HTMLInputElement).value || null
  if (field === 'startDate') {
    localStartDate.value = value
  } else {
    localEndDate.value = value
  }
  localActivePreset.value = null
  emit('update:activePreset', null)
}

function applyDateInputs() {
  emit('change', { startDate: localStartDate.value, endDate: localEndDate.value })
}

// =====================
// Watchers
// =====================

watch(() => props.startDate, (val) => {
  localStartDate.value = val
})

watch(() => props.endDate, (val) => {
  localEndDate.value = val
})

watch(() => props.activePreset, (val) => {
  localActivePreset.value = val
})
</script>
