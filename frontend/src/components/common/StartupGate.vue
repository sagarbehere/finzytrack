<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/60 p-4 backdrop-blur-sm">
    <div class="w-full max-w-lg overflow-hidden rounded-lg bg-white shadow-xl ring-1 ring-gray-200 dark:bg-gray-800 dark:ring-white/10">
      <div class="px-6 py-5">
        <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ task.title }}</h2>
        <!-- summary is author-controlled markdown, HTML-escaped by renderMarkdown -->
        <div class="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-300" v-html="summaryHtml"></div>

        <p v-if="applyError" class="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400">
          {{ applyError }}
        </p>
      </div>

      <div class="flex items-center justify-between gap-3 border-t border-gray-200 bg-gray-50 px-6 py-4 dark:border-white/10 dark:bg-white/5">
        <a
          v-if="docsUrl"
          :href="docsUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="text-sm font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
        >Learn more</a>
        <span v-else></span>

        <button
          type="button"
          :disabled="isApplying"
          @click="onUpgrade"
          class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-indigo-500 dark:hover:bg-indigo-400"
        >
          {{ isApplying ? 'Upgrading…' : 'Upgrade & continue' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useStartupTasks } from '@/composables/useStartupTasks'
import { renderMarkdown } from '@/utils/markdown'
import type { StartupTaskInfo } from '@/services/generated-api'

const props = defineProps<{ task: StartupTaskInfo }>()
const emit = defineEmits<{ applied: [] }>()

const { isApplying, applyError, applyStartupTask, docsUrlFor } = useStartupTasks()

const docsUrl = computed(() => docsUrlFor(props.task))
const summaryHtml = computed(() => renderMarkdown(props.task.summary))

async function onUpgrade() {
  const ok = await applyStartupTask(props.task.id)
  if (ok) emit('applied')
}
</script>
