<template>
  <div class="flex items-center border-b border-gray-200 dark:border-white/10">
    <!-- Tabs -->
    <div
      v-for="tab in tabs"
      :key="tab.id"
      role="tab"
      tabindex="0"
      @click="emit('select', tab.id)"
      @keydown.enter="emit('select', tab.id)"
      class="group relative flex items-center gap-2 border-b-2 px-4 py-3 text-sm font-medium cursor-pointer whitespace-nowrap"
      :class="[
        tab.id === activeTabId
          ? 'border-indigo-500 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
          : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200'
      ]"
    >
      <span class="truncate max-w-[200px]">{{ tab.title }}</span>
      <button
        @click.stop="emit('remove', tab.id)"
        class="flex-shrink-0 p-0.5 rounded opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-all"
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

    <!-- Spacer -->
    <div class="flex-1" />

    <!-- Reload recipes button -->
    <button
      @click="emit('reload')"
      :disabled="reloading"
      class="flex items-center justify-center p-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      title="Reload recipes"
    >
      <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': reloading }" />
    </button>

    <!-- Manage recipes (gear icon) -->
    <button
      @click="router.push({ path: '/settings', query: { tab: 'dashboards' } })"
      class="flex items-center justify-center p-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800/50 transition-colors"
      title="Manage recipes"
    >
      <Cog6ToothIcon class="h-4 w-4" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { PlusIcon, XMarkIcon, ArrowPathIcon, Cog6ToothIcon } from '@heroicons/vue/20/solid'
import type { DashboardTab } from '@/composables/useDashboardTabs'

const router = useRouter()

interface Props {
  tabs: readonly DashboardTab[]
  activeTabId: string | null
  reloading?: boolean
}

withDefaults(defineProps<Props>(), { reloading: false })

const emit = defineEmits<{
  (e: 'select', dashboardId: string): void
  (e: 'remove', dashboardId: string): void
  (e: 'add'): void
  (e: 'reload'): void
}>()
</script>
