<template>
  <div class="flex items-center border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
    <!-- Tabs -->
    <div
      v-for="tab in tabs"
      :key="tab.id"
      role="tab"
      tabindex="0"
      @click="emit('select', tab.id)"
      @keydown.enter="emit('select', tab.id)"
      class="group relative flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors cursor-pointer"
      :class="[
        tab.id === activeTabId
          ? 'text-gray-900 dark:text-white bg-white dark:bg-gray-800 border-b-2 border-blue-500 -mb-px'
          : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-800/50'
      ]"
    >
      <span class="truncate max-w-[200px]">{{ tab.title }}</span>
      <button
        @click.stop="emit('remove', tab.id)"
        class="flex-shrink-0 p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-all"
        :class="{ 'opacity-100': tab.id === activeTabId }"
        title="Close tab"
      >
        <XMarkIcon class="h-4 w-4" />
      </button>
    </div>

    <!-- Add button -->
    <button
      @click="emit('add')"
      class="flex items-center justify-center p-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800/50 transition-colors"
      title="Add dashboard"
    >
      <PlusIcon class="h-5 w-5" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { PlusIcon, XMarkIcon } from '@heroicons/vue/20/solid'
import type { DashboardTab } from '@/composables/useDashboardTabs'

interface Props {
  tabs: readonly DashboardTab[]
  activeTabId: string | null
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'select', dashboardId: string): void
  (e: 'remove', dashboardId: string): void
  (e: 'add'): void
}>()
</script>
