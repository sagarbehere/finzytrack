<template>
  <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
    <!-- Header -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-start justify-between">
      <div>
        <h3 class="text-lg font-medium text-gray-900 dark:text-white">{{ title }}</h3>
        <p v-if="description" class="mt-1 text-sm text-gray-600 dark:text-gray-400">{{ description }}</p>
      </div>
      <span
        v-if="requiresRestart"
        class="ml-4 shrink-0 flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400"
      >
        <svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
          <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
        </svg>
        Requires restart
      </span>
    </div>

    <!-- Fields -->
    <div class="p-6 space-y-5">
      <slot />
    </div>

    <!-- Footer -->
    <div class="px-6 py-4 border-t border-gray-200 dark:border-gray-700 flex items-center gap-3">
      <button
        @click="$emit('save')"
        :disabled="!isDirty || isSaving"
        class="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {{ isSaving ? 'Saving...' : 'Save' }}
      </button>
      <button
        @click="$emit('reset')"
        :disabled="!isDirty || isSaving"
        class="px-4 py-2 text-sm font-medium rounded-lg bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        Reset
      </button>
      <p v-if="error" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  isDirty: boolean
  isSaving: boolean
  error?: string
  requiresRestart?: boolean
}>()

defineEmits<{
  save: []
  reset: []
}>()
</script>
