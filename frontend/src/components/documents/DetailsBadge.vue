<template>
  <button
    type="button"
    @click.stop="emit('documentClick', transaction.id)"
    :title="tooltip"
    class="inline-flex cursor-pointer items-center gap-0.5 rounded px-1 py-0.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-500 dark:hover:text-indigo-400 dark:hover:bg-indigo-900/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
    :class="hasContent ? 'text-indigo-600 dark:text-indigo-400' : ''"
  >
    <!-- Details context (Transactions): a "panel-right" glyph signalling the
         side drawer. Import context: the familiar attachment paperclip. -->
    <svg
      v-if="detailsMode"
      viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
      class="h-4 w-4" aria-hidden="true"
    >
      <rect x="3" y="4.5" width="18" height="15" rx="2" />
      <line x1="14.5" y1="4.5" x2="14.5" y2="19.5" />
    </svg>
    <PaperClipIcon v-else class="h-4 w-4" />
    <span v-if="count > 0" class="text-xs font-semibold tabular-nums">{{ count }}</span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { PaperClipIcon } from '@heroicons/vue/20/solid'
import type { TransactionViewModel } from '@/types/transactions'
import { documentCount } from '@/utils/documentMeta'
import { editableMetaFields } from '@/utils/metadataEditor'

// Extra props (importContext, ledgerContext) are passed to every `component`
// column cell; we ignore them and keep them off the DOM.
defineOptions({ inheritAttrs: false })

const props = defineProps<{
  transaction: TransactionViewModel
  // When true (Transactions view) the drawer also edits metadata, so the tooltip
  // and active state reflect that. When false (Import) it is documents-only.
  detailsMode?: boolean
}>()
const emit = defineEmits<{ (e: 'documentClick', id: string): void }>()

const count = computed(() => documentCount(props.transaction.meta))
// In details mode, editable metadata also counts as "content" so a metadata-only
// row still shows an active (not empty-looking) paperclip.
const metaCount = computed(() => props.detailsMode ? editableMetaFields(props.transaction.meta).length : 0)
const hasContent = computed(() => count.value > 0 || metaCount.value > 0)

const tooltip = computed(() => {
  if (props.detailsMode) {
    const bits: string[] = []
    if (count.value > 0) bits.push(`${count.value} document${count.value === 1 ? '' : 's'}`)
    if (metaCount.value > 0) bits.push(`${metaCount.value} metadata field${metaCount.value === 1 ? '' : 's'}`)
    return bits.length ? `${bits.join(', ')} · edit details` : 'Edit details (documents and metadata)'
  }
  return count.value > 0 ? `${count.value} document${count.value === 1 ? '' : 's'}` : 'Attach a document'
})
</script>
