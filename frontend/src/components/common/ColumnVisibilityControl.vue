<template>
  <Popover class="relative inline-block text-left">
    <PopoverButton
      class="inline-flex w-full justify-center gap-x-1.5 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-gray-800 dark:text-white dark:ring-white/10 dark:hover:bg-gray-700"
      title="Show/hide columns"
    >
      <AdjustmentsHorizontalIcon class="h-5 w-5" aria-hidden="true" />
      <span class="sr-only">Show/hide columns</span>
      <ChevronDownIcon class="-mr-1 h-5 w-5 text-gray-400" aria-hidden="true" />
    </PopoverButton>

    <transition
      enter-active-class="transition ease-out duration-100"
      enter-from-class="transform opacity-0 scale-95"
      enter-to-class="transform opacity-100 scale-100"
      leave-active-class="transition ease-in duration-75"
      leave-from-class="transform opacity-100 scale-100"
      leave-to-class="transform opacity-0 scale-95"
    >
      <!-- Popover (not Menu) so it stays open while toggling multiple columns;
           it closes only on the button, Esc, or an outside click. -->
      <PopoverPanel
        :class="[
          'absolute z-50 mt-2 w-[32rem] divide-y divide-gray-100 rounded-md bg-white shadow-lg ring-1 ring-black/5 focus:outline-none dark:bg-gray-800 dark:divide-gray-700 dark:ring-white/10',
          align === 'right' ? 'right-0 origin-top-right' : 'left-0 origin-top-left'
        ]"
      >
        <div class="px-4 py-3">
          <p class="text-sm font-medium text-gray-900 dark:text-white">Show Columns</p>
          <p class="text-sm text-gray-500 dark:text-gray-400">
            Choose which columns to display
          </p>
        </div>

        <div class="py-1">
          <div class="grid grid-cols-2 gap-0">
            <button
              v-for="column in selectableColumns"
              :key="column.id"
              type="button"
              @click="toggleColumnVisibility(column.id)"
              class="group flex w-full items-center justify-between px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-white/5 dark:hover:text-white"
            >
              <span>{{ column.label }}</span>
              <CheckIcon
                v-if="columnVisibility[column.id]"
                class="h-4 w-4 text-green-600 dark:text-green-400"
                aria-hidden="true"
              />
            </button>
          </div>
        </div>

        <div class="py-1">
          <button
            type="button"
            @click="resetToDefaults"
            class="group flex w-full items-center px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-white/5 dark:hover:text-white"
          >
            <ArrowPathIcon class="mr-3 h-4 w-4" aria-hidden="true" />
            Reset to defaults
          </button>
        </div>
      </PopoverPanel>
    </transition>
  </Popover>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Popover, PopoverButton, PopoverPanel } from '@headlessui/vue'
import {
  AdjustmentsHorizontalIcon,
  ChevronDownIcon,
  CheckIcon,
  ArrowPathIcon,
} from '@heroicons/vue/20/solid'

interface Props {
  columnVisibility: Record<string, boolean>
  allColumns: Array<{
    id: string
    label: string
    disabled?: boolean
    disabledReason?: string
  }>
  toggleColumnVisibility: (columnId: string) => void
  resetToDefaults: () => void
  align?: 'left' | 'right'
}

const props = withDefaults(defineProps<Props>(), {
  align: 'left'
})

// Always-present columns the user can't toggle (they carry the row's identity /
// actions), and disabled ones (e.g. Balance "Coming Soon"), are not shown —
// listing non-selectable entries adds noise without letting the user do anything.
const REQUIRED_COLUMN_IDS = new Set(['status', 'actions'])
const selectableColumns = computed(() =>
  props.allColumns.filter(c => !REQUIRED_COLUMN_IDS.has(c.id) && !c.disabled)
)
</script>
