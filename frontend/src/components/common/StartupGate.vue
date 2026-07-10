<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/60 p-4 backdrop-blur-sm">
    <div class="w-full max-w-lg overflow-hidden rounded-lg bg-white shadow-xl ring-1 ring-gray-200 dark:bg-gray-800 dark:ring-white/10">
      <!-- ── Confirm ─────────────────────────────────────────────── -->
      <template v-if="!applyResult">
        <div class="px-6 py-5">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ task.title }}</h2>
          <!-- summary is author-controlled markdown, HTML-escaped by renderMarkdown -->
          <div class="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-300" v-html="summaryHtml"></div>

          <!-- Which files are affected (behind "See details"). -->
          <AffectedFilesList v-if="items.length" class="mt-4" :items="items" :base-dir="itemsBaseDir" />

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
      </template>

      <!-- ── Result ──────────────────────────────────────────────── -->
      <template v-else>
        <div class="px-6 py-5">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">
            {{ failed.length ? 'Upgrade finished with issues' : 'Upgrade complete' }}
          </h2>

          <div class="mt-4 space-y-3">
            <!-- Succeeded -->
            <div v-if="succeeded.length">
              <p class="flex items-center gap-2 text-sm font-medium text-green-700 dark:text-green-400">
                <CheckCircleIcon class="size-5" />
                {{ succeeded.length }} upgraded
              </p>
              <AffectedFilesList
                class="mt-1"
                :items="succeeded"
                :base-dir="resultBaseDir"
                :with-count="false"
                hide-label="Hide"
                small
              />
            </div>

            <!-- Failed -->
            <div v-if="failed.length">
              <p class="flex items-center gap-2 text-sm font-medium text-red-700 dark:text-red-400">
                <ExclamationCircleIcon class="size-5" />
                {{ failed.length }} couldn't be upgraded
              </p>
              <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                These won't load until fixed. Edit or remove the affected file(s), or restore the
                original from its timestamped <code>.backup</code>.
              </p>
              <AffectedFilesList
                class="mt-1"
                :items="failedItems"
                :base-dir="resultBaseDir"
                :with-count="false"
                hide-label="Hide"
                tone="danger"
                small
              />
            </div>

            <!-- Fallback if a task returned no normalized outcome. -->
            <p v-if="!succeeded.length && !failed.length" class="text-sm text-gray-600 dark:text-gray-300">
              {{ resultSummary }}
            </p>
          </div>
        </div>

        <div class="flex items-center justify-end border-t border-gray-200 bg-gray-50 px-6 py-4 dark:border-white/10 dark:bg-white/5">
          <button
            type="button"
            @click="onContinue"
            class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:hover:bg-indigo-400"
          >
            Continue
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { CheckCircleIcon, ExclamationCircleIcon } from '@heroicons/vue/20/solid'
import { useStartupTasks } from '@/composables/useStartupTasks'
import { renderMarkdown } from '@/utils/markdown'
import AffectedFilesList from '@/components/common/AffectedFilesList.vue'
import type { AffectedItem } from '@/components/common/AffectedFilesList.vue'
import type { StartupTaskInfo } from '@/services/generated-api'

// Normalized shapes the modal renders for any task (see startup_tasks/base.py).
interface FailedItem {
  path: string
  reason: string
}

const props = defineProps<{ task: StartupTaskInfo }>()
const emit = defineEmits<{ applied: [] }>()

const { isApplying, applyError, applyStartupTask, checkStartupTasks, docsUrlFor } = useStartupTasks()

const docsUrl = computed(() => docsUrlFor(props.task))
const summaryHtml = computed(() => renderMarkdown(props.task.summary))

// Pre-consent: the affected files behind "See details".
const items = computed<AffectedItem[]>(
  () => (props.task.details?.items as AffectedItem[] | undefined) ?? [],
)
// Absolute directory the (config/recipes-relative) paths live under, shown once.
const itemsBaseDir = computed(() => props.task.details?.baseDir as string | undefined)

// Post-consent result.
const applyResult = ref<Record<string, unknown> | null>(null)
const outcome = computed(
  () => applyResult.value?.outcome as { succeeded?: AffectedItem[]; failed?: FailedItem[] } | undefined,
)
const succeeded = computed<AffectedItem[]>(() => outcome.value?.succeeded ?? [])
const failed = computed<FailedItem[]>(() => outcome.value?.failed ?? [])
// The shared list renders a note per row; a failure's note is its reason.
const failedItems = computed<AffectedItem[]>(() => failed.value.map((f) => ({ path: f.path, note: f.reason })))
const resultBaseDir = computed(() => applyResult.value?.baseDir as string | undefined)
const resultSummary = computed(() => (applyResult.value?.summary as string | undefined) ?? 'Done.')

async function onUpgrade() {
  const result = await applyStartupTask(props.task.id)
  if (result) applyResult.value = result // switch to the result view
}

async function onContinue() {
  // Re-detect now (a clean apply self-retires and the gate clears); a partial
  // failure downgrades to a non-blocking notice, so the gate also clears.
  await checkStartupTasks()
  emit('applied')
}
</script>
