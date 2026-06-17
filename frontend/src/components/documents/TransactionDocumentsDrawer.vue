<template>
  <TransitionRoot appear :show="open" as="template">
    <Dialog as="div" @close="emit('update:open', false)" class="relative z-40">
      <TransitionChild
        as="template"
        enter="duration-300 ease-out" enter-from="opacity-0" enter-to="opacity-100"
        leave="duration-200 ease-in" leave-from="opacity-100" leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/25 dark:bg-black/50" />
      </TransitionChild>

      <div class="fixed inset-0 overflow-hidden">
        <div class="absolute inset-0 overflow-hidden">
          <div class="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
            <TransitionChild
              as="template"
              enter="transform transition ease-in-out duration-300" enter-from="translate-x-full" enter-to="translate-x-0"
              leave="transform transition ease-in-out duration-200" leave-from="translate-x-0" leave-to="translate-x-full"
            >
              <DialogPanel class="pointer-events-auto w-screen max-w-md">
                <div class="flex h-full flex-col bg-white dark:bg-gray-800 shadow-xl">

                  <!-- Header -->
                  <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10 flex-shrink-0">
                    <div class="flex items-start justify-between gap-4">
                      <div class="flex-1 min-w-0">
                        <DialogTitle class="text-base font-semibold text-gray-900 dark:text-white leading-tight">
                          Documents
                        </DialogTitle>
                        <p v-if="transaction" class="mt-1 text-sm text-gray-500 dark:text-gray-400 truncate">
                          {{ transaction.date }} · {{ transaction.payee || transaction.narration || 'Transaction' }}
                        </p>
                      </div>
                      <button
                        @click="emit('update:open', false)"
                        class="flex-shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 mt-0.5"
                      >
                        <XMarkIcon class="h-5 w-5" />
                      </button>
                    </div>
                  </div>

                  <!-- Content -->
                  <div v-if="transaction" class="flex-1 overflow-y-auto px-6 py-5 space-y-5">
                    <!-- Attached documents -->
                    <ul v-if="docs.length > 0" class="divide-y divide-gray-100 dark:divide-white/5">
                      <li
                        v-for="doc in docs"
                        :key="doc.key"
                        class="flex items-center justify-between gap-3 py-2"
                      >
                        <button
                          type="button"
                          @click="openDocument(doc.path)"
                          class="flex items-center gap-2 min-w-0 text-left text-sm text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
                        >
                          <PaperClipIcon class="h-4 w-4 flex-shrink-0" />
                          <span class="truncate">{{ basename(doc.path) }}</span>
                        </button>
                        <button
                          type="button"
                          @click="onRemove(doc.key)"
                          class="flex-shrink-0 text-gray-400 hover:text-red-600 dark:hover:text-red-400"
                          title="Remove document"
                        >
                          <XMarkIcon class="h-4 w-4" />
                        </button>
                      </li>
                    </ul>
                    <p v-else class="text-sm text-gray-400 dark:text-gray-500 italic">
                      No documents attached yet.
                    </p>

                    <!-- Upload zone -->
                    <DocumentUploadZone :uploading="isUploading" @files-selected="onFiles" />
                  </div>

                </div>
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { PaperClipIcon } from '@heroicons/vue/20/solid'
import DocumentUploadZone from '@/components/documents/DocumentUploadZone.vue'
import { useDocuments } from '@/composables/useDocuments'
import { listDocuments, addDocument, removeDocument } from '@/utils/documentMeta'
import type { TransactionViewModel } from '@/types/transactions'

const props = defineProps<{
  open: boolean
  transaction: TransactionViewModel | null
}>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  /** New meta for the transaction; the parent decides whether to persist or stage. */
  (e: 'changed', meta: Record<string, string>): void
}>()

const { uploadDocument, openDocument, isUploading } = useDocuments()

const docs = computed(() => listDocuments(props.transaction?.meta))

function basename(path: string): string {
  return path.split('/').pop() || path
}

async function onFiles(files: File[]) {
  if (!props.transaction) return
  let meta = { ...props.transaction.meta }
  for (const file of files) {
    const stored = await uploadDocument(file, {
      date: props.transaction.date,
      narration: props.transaction.payee || props.transaction.narration,
    })
    meta = addDocument(meta, stored.path)
  }
  emit('changed', meta)
}

function onRemove(key: string) {
  if (!props.transaction) return
  emit('changed', removeDocument(props.transaction.meta, key))
}
</script>
