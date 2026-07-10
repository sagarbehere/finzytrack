<template>
  <div>
    <button
      type="button"
      @click="show = !show"
      class="inline-flex items-center gap-1 text-sm font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
      :class="small ? 'text-xs' : ''"
    >
      <ChevronRightIcon class="size-4 transition-transform" :class="[show ? 'rotate-90' : '', small ? 'size-3.5' : '']" />
      {{ show ? hideLabel : `${label}${withCount ? ` (${items.length})` : ''}` }}
    </button>
    <ul
      v-if="show"
      class="mt-2 max-h-48 overflow-y-auto rounded-md p-3 text-xs"
      :class="tone === 'danger' ? 'bg-red-50 dark:bg-red-500/10' : 'bg-gray-50 dark:bg-white/5'"
    >
      <li
        v-if="baseDir"
        class="mb-1 border-b pb-1"
        :class="tone === 'danger'
          ? 'border-red-200 text-gray-500 dark:border-red-500/20 dark:text-gray-400'
          : 'border-gray-200 text-gray-400 dark:border-white/10 dark:text-gray-500'"
      >
        in <code>{{ baseDir }}</code>
      </li>
      <li
        v-for="it in items"
        :key="it.path"
        class="py-0.5"
        :class="tone === 'danger' ? '' : 'flex items-baseline justify-between gap-3'"
      >
        <code class="text-gray-700 dark:text-gray-300">{{ it.path }}</code>
        <span
          v-if="it.note"
          :class="tone === 'danger'
            ? 'text-red-600 dark:text-red-400'
            : 'shrink-0 text-gray-400 dark:text-gray-500'"
        >{{ tone === 'danger' ? ` — ${it.note}` : it.note }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ChevronRightIcon } from '@heroicons/vue/20/solid'

/**
 * One collapsible "See details" list of affected files — the itemization the
 * startup framework produces for any task (`{path, note?}` + an absolute
 * `baseDir` anchor). Shared by the gating modal (StartupGate) and the
 * seed-content notice, so the list is defined once. See
 * dev-docs/seed-content-refresh.md §9.5.
 */
export interface AffectedItem {
  path: string
  note?: string
}

withDefaults(
  defineProps<{
    items: AffectedItem[]
    baseDir?: string
    /** Button label when collapsed (a count is appended unless `withCount` is false). */
    label?: string
    hideLabel?: string
    withCount?: boolean
    /** `danger` styles the list for failures (red, inline "— reason"). */
    tone?: 'neutral' | 'danger'
    /** Smaller type, for use inside a compact result view. */
    small?: boolean
  }>(),
  {
    label: 'See details',
    hideLabel: 'Hide details',
    withCount: true,
    tone: 'neutral',
    small: false,
  },
)

const show = ref(false)
</script>
