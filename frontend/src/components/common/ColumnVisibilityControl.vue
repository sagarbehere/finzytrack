<template>
  <Menu as="div" class="relative inline-block text-left">
    <div>
      <MenuButton
        class="inline-flex w-full justify-center gap-x-1.5 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 dark:bg-gray-800 dark:text-white dark:ring-gray-600 dark:hover:bg-gray-700"
        title="Show/hide columns"
      >
        <AdjustmentsHorizontalIcon class="h-5 w-5" aria-hidden="true" />
        <span class="sr-only">Show/hide columns</span>
        <ChevronDownIcon class="-mr-1 h-5 w-5 text-gray-400" aria-hidden="true" />
      </MenuButton>
    </div>

    <transition
      enter-active-class="transition ease-out duration-100"
      enter-from-class="transform opacity-0 scale-95"
      enter-to-class="transform opacity-100 scale-100"
      leave-active-class="transition ease-in duration-75"
      leave-from-class="transform opacity-100 scale-100"
      leave-to-class="transform opacity-0 scale-95"
    >
      <MenuItems
        class="absolute left-0 z-50 mt-2 w-[32rem] origin-top-left divide-y divide-gray-100 rounded-md bg-white shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none dark:bg-gray-800 dark:divide-gray-700 dark:ring-gray-600"
      >
        <div class="px-4 py-3">
          <p class="text-sm font-medium text-gray-900 dark:text-white">Show Columns</p>
          <p class="text-sm text-gray-500 dark:text-gray-400">
            Choose which columns to display
          </p>
        </div>

        <div class="py-1">
          <div class="grid grid-cols-2 gap-0">
            <MenuItem
              v-for="column in allColumns"
              :key="column.id"
              v-slot="{ active }"
              as="div"
            >
            <button
              @click="toggleColumnVisibility(column.id)"
              :class="[
                active ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white' : 'text-gray-700 dark:text-gray-300',
                'group flex w-full items-center px-4 py-2 text-sm'
              ]"
              :disabled="column.id === 'status' || column.id === 'actions' || column.disabled"
              :title="column.disabled ? column.disabledReason : undefined"
            >
              <div class="flex items-center justify-between w-full">
                <span :class="{ 'opacity-50': column.id === 'status' || column.id === 'actions' || column.disabled }">
                  {{ column.label }}
                </span>
                <div class="flex items-center">
                  <CheckIcon
                    v-if="columnVisibility[column.id]"
                    class="h-4 w-4 text-green-600 dark:text-green-400"
                    aria-hidden="true"
                  />
                  <span
                    v-if="column.id === 'status' || column.id === 'actions'"
                    class="text-xs text-gray-400 ml-2"
                  >
                    Required
                  </span>
                  <span
                    v-else-if="column.disabled"
                    class="text-xs text-gray-400 ml-2"
                  >
                    {{ column.disabledReason || 'Coming Soon' }}
                  </span>
                </div>
              </div>
            </button>
          </MenuItem>
          </div>
        </div>

        <div class="py-1">
          <MenuItem v-slot="{ active }">
            <button
              @click="resetToDefaults"
              :class="[
                active ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white' : 'text-gray-700 dark:text-gray-300',
                'group flex w-full items-center px-4 py-2 text-sm'
              ]"
            >
              <ArrowPathIcon class="mr-3 h-4 w-4" aria-hidden="true" />
              Reset to defaults
            </button>
          </MenuItem>
        </div>
      </MenuItems>
    </transition>
  </Menu>
</template>

<script setup lang="ts">
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/vue'
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
}

defineProps<Props>()
</script>