<template>
  <button
    type="button"
    @click.stop="emit('documentClick', transaction.id)"
    :title="count > 0 ? `${count} document${count === 1 ? '' : 's'}` : 'Attach a document'"
    class="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-500 dark:hover:text-indigo-400 dark:hover:bg-indigo-900/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
    :class="count > 0 ? 'text-indigo-600 dark:text-indigo-400' : ''"
  >
    <PaperClipIcon class="h-4 w-4" />
    <span v-if="count > 0" class="text-xs font-semibold tabular-nums">{{ count }}</span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { PaperClipIcon } from '@heroicons/vue/20/solid'
import type { TransactionViewModel } from '@/types/transactions'
import { documentCount } from '@/utils/documentMeta'

// Extra props (importContext, ledgerContext) are passed to every `component`
// column cell; we ignore them and keep them off the DOM.
defineOptions({ inheritAttrs: false })

const props = defineProps<{ transaction: TransactionViewModel }>()
const emit = defineEmits<{ (e: 'documentClick', id: string): void }>()

const count = computed(() => documentCount(props.transaction.meta))
</script>
