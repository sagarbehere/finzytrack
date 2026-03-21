<template>
  <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
    <!-- Header -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
      <h3 class="text-lg font-medium text-gray-900 dark:text-white">{{ title }}</h3>
      <p v-if="description" class="mt-1 text-sm text-gray-600 dark:text-gray-400">{{ description }}</p>
    </div>

    <!-- Fields -->
    <div class="p-6 space-y-4">
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
}>()

defineEmits<{
  save: []
  reset: []
}>()
</script>
