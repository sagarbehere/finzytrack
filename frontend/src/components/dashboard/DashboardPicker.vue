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
        <div class="fixed inset-0 bg-gray-500/75 transition-opacity dark:bg-gray-900/50" />
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
            <DialogPanel class="relative transform overflow-hidden rounded-lg bg-white text-left shadow-xl transition-all sm:my-8 sm:w-full sm:max-w-md dark:bg-gray-800 dark:outline dark:-outline-offset-1 dark:outline-white/10">
              <div class="px-4 pb-4 pt-5 sm:p-6">
                <!-- Header -->
                <div class="flex items-center justify-between mb-4">
                  <DialogTitle as="h3" class="text-lg font-semibold text-gray-900 dark:text-white">
                    Add Dashboard
                  </DialogTitle>
                  <button
                    @click="emit('close')"
                    class="rounded-md text-gray-400 hover:text-gray-500 dark:hover:text-white"
                  >
                    <XMarkIcon class="h-5 w-5" />
                  </button>
                </div>

                <!-- Dashboard list -->
                <div v-if="availableDashboards.length > 0" class="space-y-2">
                  <div
                    v-for="dashboard in availableDashboards"
                    :key="dashboard.id"
                    class="flex items-stretch rounded-lg border transition-colors"
                    :class="[
                      selectedIds.includes(dashboard.id)
                        ? 'bg-gray-50 dark:bg-white/5 border-gray-200 dark:border-white/10 opacity-60'
                        : 'bg-white dark:bg-gray-800/50 border-gray-200 dark:border-white/10 hover:border-indigo-500 dark:hover:border-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/20'
                    ]"
                  >
                    <!-- Clickable area for selection -->
                    <button
                      class="flex-1 text-left px-4 py-3 min-w-0"
                      :disabled="selectedIds.includes(dashboard.id)"
                      :class="selectedIds.includes(dashboard.id) ? 'cursor-not-allowed' : 'cursor-pointer'"
                      @click="handleSelect(dashboard.id)"
                    >
                      <div class="flex items-center justify-between">
                        <div class="font-medium text-gray-900 dark:text-white truncate">
                          {{ dashboard.title }}
                        </div>
                        <span
                          v-if="selectedIds.includes(dashboard.id)"
                          class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
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
                    <!-- Delete button (only for user recipes) -->
                    <button
                      v-if="dashboard.canDelete"
                      class="flex-none flex items-center px-2 text-gray-300 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 transition-colors"
                      title="Delete this dashboard"
                      @click.stop="handleDelete(dashboard)"
                    >
                      <TrashIcon class="h-4 w-4" />
                    </button>
                  </div>
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
                  class="w-full sm:w-auto inline-flex justify-center rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
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
import { TrashIcon } from '@heroicons/vue/24/outline'

export interface AvailableDashboard {
  id: string
  title: string
  description?: string
  canDelete?: boolean
  manifestPath?: string
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
  (e: 'delete', dashboard: AvailableDashboard): void
}>()

function handleSelect(dashboardId: string) {
  emit('select', dashboardId)
}

function handleDelete(dashboard: AvailableDashboard) {
  if (confirm(`Delete "${dashboard.title}"? A timestamped backup is kept in the backup directory; you can restore it from there if needed.`)) {
    emit('delete', dashboard)
  }
}
</script>
