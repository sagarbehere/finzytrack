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
          <div v-if="items.length" class="mt-4">
            <button
              type="button"
              @click="showItems = !showItems"
              class="inline-flex items-center gap-1 text-sm font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
            >
              <ChevronRightIcon class="size-4 transition-transform" :class="showItems ? 'rotate-90' : ''" />
              {{ showItems ? 'Hide details' : `See details (${items.length})` }}
            </button>
            <ul v-if="showItems" class="mt-2 max-h-48 overflow-y-auto rounded-md bg-gray-50 p-3 text-xs dark:bg-white/5">
              <li v-for="it in items" :key="it.path" class="flex items-baseline justify-between gap-3 py-0.5">
                <code class="text-gray-700 dark:text-gray-300">{{ it.path }}</code>
                <span v-if="it.note" class="shrink-0 text-gray-400 dark:text-gray-500">{{ it.note }}</span>
              </li>
            </ul>
          </div>

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
              <button
                type="button"
                @click="showSucceeded = !showSucceeded"
                class="mt-1 inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
              >
                <ChevronRightIcon class="size-3.5 transition-transform" :class="showSucceeded ? 'rotate-90' : ''" />
                {{ showSucceeded ? 'Hide' : 'See details' }}
              </button>
              <ul v-if="showSucceeded" class="mt-1 max-h-40 overflow-y-auto rounded-md bg-gray-50 p-3 text-xs dark:bg-white/5">
                <li v-for="it in succeeded" :key="it.path" class="flex items-baseline justify-between gap-3 py-0.5">
                  <code class="text-gray-700 dark:text-gray-300">{{ it.path }}</code>
                  <span v-if="it.note" class="shrink-0 text-gray-400 dark:text-gray-500">{{ it.note }}</span>
                </li>
              </ul>
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
              <button
                type="button"
                @click="showFailed = !showFailed"
                class="mt-1 inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
              >
                <ChevronRightIcon class="size-3.5 transition-transform" :class="showFailed ? 'rotate-90' : ''" />
                {{ showFailed ? 'Hide' : 'See details' }}
              </button>
              <ul v-if="showFailed" class="mt-1 max-h-40 overflow-y-auto rounded-md bg-red-50 p-3 text-xs dark:bg-red-500/10">
                <li v-for="f in failed" :key="f.path" class="py-0.5">
                  <code class="text-gray-700 dark:text-gray-300">{{ f.path }}</code>
                  <span class="text-red-600 dark:text-red-400"> — {{ f.reason }}</span>
                </li>
              </ul>
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
import { ChevronRightIcon, CheckCircleIcon, ExclamationCircleIcon } from '@heroicons/vue/20/solid'
import { useStartupTasks } from '@/composables/useStartupTasks'
import { renderMarkdown } from '@/utils/markdown'
import type { StartupTaskInfo } from '@/services/generated-api'

// Normalized shapes the modal renders for any task (see startup_tasks/base.py).
interface AffectedItem {
  path: string
  note?: string
}
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
const showItems = ref(false)

// Post-consent result.
const applyResult = ref<Record<string, unknown> | null>(null)
const outcome = computed(
  () => applyResult.value?.outcome as { succeeded?: AffectedItem[]; failed?: FailedItem[] } | undefined,
)
const succeeded = computed<AffectedItem[]>(() => outcome.value?.succeeded ?? [])
const failed = computed<FailedItem[]>(() => outcome.value?.failed ?? [])
const resultSummary = computed(() => (applyResult.value?.summary as string | undefined) ?? 'Done.')
const showSucceeded = ref(false)
const showFailed = ref(false)

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
