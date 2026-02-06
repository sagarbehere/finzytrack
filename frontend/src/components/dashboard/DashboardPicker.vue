<template>
  <TransitionRoot :show="isOpen" as="template">
    <Dialog as="div" class="relative z-50" @close="emit('close')">
      <!-- Backdrop -->
      <TransitionChild
        as="template"
        enter="ease-out duration-300"
        enter-from="opacity-0"
        enter-to="opacity-100"
        leave="ease-in duration-200"
        leave-from="opacity-100"
        leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-gray-500/75 dark:bg-gray-900/75 transition-opacity" />
      </TransitionChild>

      <div class="fixed inset-0 z-10 overflow-y-auto">
        <div class="flex min-h-full items-end justify-center p-4 text-center sm:items-center sm:p-0">
          <TransitionChild
            as="template"
            enter="ease-out duration-300"
            enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
            enter-to="opacity-100 translate-y-0 sm:scale-100"
            leave="ease-in duration-200"
            leave-from="opacity-100 translate-y-0 sm:scale-100"
            leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
          >
            <DialogPanel class="relative transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 text-left shadow-xl transition-all sm:my-8 sm:w-full sm:max-w-md">
              <div class="px-4 pb-4 pt-5 sm:p-6">
                <!-- Header -->
                <div class="flex items-center justify-between mb-4">
                  <DialogTitle as="h3" class="text-lg font-semibold text-gray-900 dark:text-white">
                    Add Dashboard
                  </DialogTitle>
                  <button
                    @click="emit('close')"
                    class="p-1 rounded-md text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                  >
                    <XMarkIcon class="h-5 w-5" />
                  </button>
                </div>

                <!-- Dashboard list -->
                <div v-if="availableDashboards.length > 0" class="space-y-2">
                  <button
                    v-for="dashboard in availableDashboards"
                    :key="dashboard.id"
                    @click="handleSelect(dashboard.id)"
                    :disabled="selectedIds.includes(dashboard.id)"
                    class="w-full text-left px-4 py-3 rounded-lg border transition-colors"
                    :class="[
                      selectedIds.includes(dashboard.id)
                        ? 'bg-gray-50 dark:bg-gray-700/50 border-gray-200 dark:border-gray-600 cursor-not-allowed opacity-60'
                        : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-600 hover:border-blue-500 dark:hover:border-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 cursor-pointer'
                    ]"
                  >
                    <div class="flex items-center justify-between">
                      <div class="font-medium text-gray-900 dark:text-white">
                        {{ dashboard.title }}
                      </div>
                      <span
                        v-if="selectedIds.includes(dashboard.id)"
                        class="text-xs font-medium px-2 py-0.5 rounded-full bg-gray-200 dark:bg-gray-600 text-gray-600 dark:text-gray-300"
                      >
                        Added
                      </span>
                    </div>
                    <p
                      v-if="dashboard.description"
                      class="mt-1 text-sm text-gray-500 dark:text-gray-400"
                    >
                      {{ dashboard.description }}
                    </p>
                  </button>
                </div>

                <!-- Empty state -->
                <div v-else class="text-center py-8 text-gray-500 dark:text-gray-400">
                  No dashboards available
                </div>
              </div>

              <!-- Footer -->
              <div class="bg-gray-50 dark:bg-gray-900/50 px-4 py-3 sm:px-6">
                <button
                  @click="emit('close')"
                  class="w-full sm:w-auto inline-flex justify-center rounded-md bg-white dark:bg-gray-700 px-3 py-2 text-sm font-semibold text-gray-900 dark:text-white shadow-sm ring-1 ring-inset ring-gray-300 dark:ring-gray-600 hover:bg-gray-50 dark:hover:bg-gray-600"
                >
                  Cancel
                </button>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogPanel, DialogTitle, TransitionRoot, TransitionChild } from '@headlessui/vue'
import { XMarkIcon } from '@heroicons/vue/20/solid'

export interface AvailableDashboard {
  id: string
  title: string
  description?: string
}

interface Props {
  isOpen: boolean
  availableDashboards: AvailableDashboard[]
  selectedIds: string[]
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'select', dashboardId: string): void
}>()

function handleSelect(dashboardId: string) {
  emit('select', dashboardId)
}
</script>
