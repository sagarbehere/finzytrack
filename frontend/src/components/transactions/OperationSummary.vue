<template>
  <div
    v-if="operations.length > 0"
    class="mb-4 rounded-lg bg-white px-4 py-3 shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:ring-white/10"
  >
    <p class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
      Staged bulk changes
    </p>
    <ul class="divide-y divide-gray-100 dark:divide-white/5">
      <li
        v-for="op in operationsReversed"
        :key="op.id"
        class="flex items-center justify-between gap-3 py-1.5 text-sm"
      >
        <span class="min-w-0 truncate text-gray-900 dark:text-white">
          {{ op.label }}
          <span class="ml-1 text-gray-500 dark:text-gray-400">
            · {{ op.affectedIds.length }} {{ op.affectedIds.length === 1 ? 'txn' : 'txns' }}
          </span>
        </span>
        <button
          class="shrink-0 text-sm font-medium text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300"
          @click="emit('undo', op.id)"
        >
          Undo
        </button>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AppliedOperation } from '@/composables/useTransactionStore'

const props = defineProps<{
  operations: AppliedOperation[]
}>()

const emit = defineEmits<{
  (e: 'undo', id: number): void
}>()

// Most-recent first. Undoing an operation reverts any later operations on the
// same transactions (priors are whole-transaction snapshots), so surfacing the
// newest at the top keeps undo intuitive.
const operationsReversed = computed(() => [...props.operations].reverse())
</script>
