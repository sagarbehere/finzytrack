<template>
  <!-- Non-blocking notice: new/updated bundled demo content is available. Unlike
       StartupGate this never gates the app — it's a dismissible card. -->
  <div
    v-if="task || result"
    class="fixed bottom-4 right-4 z-40 w-full max-w-md overflow-hidden rounded-lg bg-white shadow-xl ring-1 ring-gray-200 dark:bg-gray-800 dark:ring-white/10"
  >
    <!-- ── Offer ───────────────────────────────────────────────── -->
    <template v-if="!result && task">
      <div class="px-5 py-4">
        <div class="flex items-start gap-3">
          <SparklesIcon class="mt-0.5 size-5 shrink-0 text-indigo-500" />
          <div class="min-w-0 flex-1">
            <h3 class="text-sm font-semibold text-gray-900 dark:text-white">{{ task.title }}</h3>
            <div class="mt-1 space-y-2 text-sm text-gray-600 dark:text-gray-300" v-html="summaryHtml"></div>
            <AffectedFilesList
              v-if="items.length"
              class="mt-3"
              :items="items"
              :base-dir="baseDir"
            />
          </div>
        </div>

        <p v-if="applyError" class="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-500/10 dark:text-red-400">
          {{ applyError }}
        </p>
      </div>

      <div class="flex items-center justify-between gap-3 border-t border-gray-200 bg-gray-50 px-5 py-3 dark:border-white/10 dark:bg-white/5">
        <a
          v-if="docsUrl"
          :href="docsUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
        >Learn more</a>
        <span v-else></span>

        <div class="flex items-center gap-2">
          <button
            type="button"
            :disabled="isApplying"
            @click="onDismiss"
            class="rounded-md bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-60 dark:bg-white/10 dark:text-white dark:inset-ring-white/10 dark:hover:bg-white/20"
          >
            Dismiss
          </button>
          <button
            type="button"
            :disabled="isApplying"
            @click="onApply"
            class="rounded-md bg-indigo-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-indigo-500 dark:hover:bg-indigo-400"
          >
            {{ isApplying ? 'Adding…' : 'Add now' }}
          </button>
        </div>
      </div>
    </template>

    <!-- ── Result ──────────────────────────────────────────────── -->
    <template v-else-if="result">
      <div class="px-5 py-4">
        <h3 class="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
          <CheckCircleIcon class="size-5 text-green-600 dark:text-green-400" />
          {{ resultSummary }}
        </h3>
        <div class="mt-3 space-y-2">
          <AffectedFilesList
            v-if="succeeded.length"
            :items="succeeded"
            :base-dir="resultBaseDir"
            :with-count="false"
            label="Show added / refreshed"
            hide-label="Hide"
            small
          />
          <AffectedFilesList
            v-if="skipped.length"
            :items="skipped"
            :base-dir="resultBaseDir"
            :with-count="false"
            :label="`${skipped.length} kept (your edits)`"
            hide-label="Hide"
            small
          />
          <AffectedFilesList
            v-if="failedItems.length"
            :items="failedItems"
            :base-dir="resultBaseDir"
            :with-count="false"
            :label="`${failedItems.length} failed`"
            hide-label="Hide"
            tone="danger"
            small
          />
        </div>
      </div>
      <div class="flex items-center justify-end border-t border-gray-200 bg-gray-50 px-5 py-3 dark:border-white/10 dark:bg-white/5">
        <button
          type="button"
          @click="onClose"
          class="rounded-md bg-indigo-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 dark:bg-indigo-500 dark:hover:bg-indigo-400"
        >
          Done
        </button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { SparklesIcon, CheckCircleIcon } from '@heroicons/vue/20/solid'
import { useStartupTasks } from '@/composables/useStartupTasks'
import { renderMarkdown } from '@/utils/markdown'
import AffectedFilesList from '@/components/common/AffectedFilesList.vue'
import type { AffectedItem } from '@/components/common/AffectedFilesList.vue'

const SEED_CONTENT_ID = 'seed-content'

const {
  infoTasks, isApplying, applyError,
  applyStartupTask, dismissStartupTask, checkStartupTasks, docsUrlFor,
} = useStartupTasks()

const task = computed(() => infoTasks.value.find((t) => t.id === SEED_CONTENT_ID) ?? null)
const docsUrl = computed(() => (task.value ? docsUrlFor(task.value) : null))
const summaryHtml = computed(() => (task.value ? renderMarkdown(task.value.summary) : ''))
const items = computed<AffectedItem[]>(() => (task.value?.details?.items as AffectedItem[] | undefined) ?? [])
const baseDir = computed(() => task.value?.details?.baseDir as string | undefined)

// Kept locally after apply so the result stays visible while we re-detect (which
// clears the task from infoTasks).
const result = ref<Record<string, unknown> | null>(null)
const outcome = computed(
  () => result.value?.outcome as { succeeded?: AffectedItem[]; failed?: { path: string; reason: string }[] } | undefined,
)
const succeeded = computed<AffectedItem[]>(() => outcome.value?.succeeded ?? [])
const skipped = computed<AffectedItem[]>(() => (result.value?.skipped as AffectedItem[] | undefined) ?? [])
const failedItems = computed<AffectedItem[]>(
  () => (outcome.value?.failed ?? []).map((f) => ({ path: f.path, note: f.reason })),
)
const resultBaseDir = computed(() => result.value?.baseDir as string | undefined)
const resultSummary = computed(() => (result.value?.summary as string | undefined) ?? 'Demo content added.')

async function onApply() {
  if (!task.value) return
  const r = await applyStartupTask(task.value.id)
  if (r) {
    result.value = r
    await checkStartupTasks() // task self-retires; result view stays (local copy)
  }
}

async function onDismiss() {
  if (!task.value) return
  await dismissStartupTask(task.value.id) // snoozes for this bundle
  await checkStartupTasks()
}

async function onClose() {
  result.value = null
}
</script>
