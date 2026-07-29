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
                          Details
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
                  <div v-if="transaction" class="flex-1 overflow-y-auto px-6 py-5 space-y-6">

                    <!-- Metadata -->
                    <section v-if="showMetadata">
                      <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                        Metadata
                      </h3>
                      <div v-if="editFields.length === 0" class="mb-2 text-sm text-gray-400 dark:text-gray-500 italic">
                        No metadata fields yet.
                      </div>
                      <ul class="space-y-2">
                        <li v-for="(field, i) in editFields" :key="i">
                          <div class="flex items-start gap-2">
                            <div class="flex-1 min-w-0">
                              <input
                                v-model="field.key"
                                type="text"
                                placeholder="key"
                                :class="inputClass"
                                @input="onFieldsChanged"
                              />
                            </div>
                            <div class="flex-1 min-w-0">
                              <input
                                v-model="field.value"
                                type="text"
                                placeholder="value"
                                :class="inputClass"
                                @input="onFieldsChanged"
                              />
                            </div>
                            <button
                              type="button"
                              @click="removeField(i)"
                              class="mt-1 flex-shrink-0 text-gray-400 hover:text-red-600 dark:hover:text-red-400"
                              title="Remove field"
                            >
                              <XMarkIcon class="h-4 w-4" />
                            </button>
                          </div>
                          <p v-if="fieldError(i)" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError(i) }}</p>
                          <p v-else-if="fieldStatus(i) === 'new'" class="mt-1 text-xs text-green-600 dark:text-green-400">new</p>
                          <p v-else-if="fieldStatus(i) === 'changed'" class="mt-1 text-xs text-amber-600 dark:text-amber-400">
                            was: <span class="line-through">{{ baselineValue(field.key) }}</span>
                          </p>
                        </li>
                      </ul>

                      <!-- Removed / renamed-away keys (staged) -->
                      <ul v-if="removedFields.length > 0" class="mt-2 space-y-1">
                        <li v-for="f in removedFields" :key="f.key" class="text-xs text-gray-400 dark:text-gray-500">
                          <span class="line-through">{{ f.key }}: {{ f.value }}</span> <span class="not-italic">(removed)</span>
                        </li>
                      </ul>

                      <button
                        type="button"
                        @click="addField"
                        class="mt-3 text-sm font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
                      >
                        + Add field
                      </button>
                    </section>

                    <!-- Attached documents -->
                    <section>
                      <h3 v-if="showMetadata" class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                        Documents
                      </h3>
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
                      <DocumentUploadZone :uploading="isUploading" @files-selected="onFiles" class="mt-3" />
                    </section>

                    <!-- System (read-only) -->
                    <details v-if="showMetadata && systemFields.length > 0" class="text-sm">
                      <summary class="cursor-pointer text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                        System (read-only)
                      </summary>
                      <dl class="mt-2 space-y-1">
                        <div v-for="f in systemFields" :key="f.key" class="flex gap-2">
                          <dt class="w-32 flex-shrink-0 text-gray-500 dark:text-gray-400">{{ f.key }}</dt>
                          <dd class="min-w-0 truncate font-mono text-gray-700 dark:text-gray-300" :title="f.value">{{ f.value }}</dd>
                        </div>
                      </dl>
                    </details>

                  </div>

                </div>
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </div>

      <!-- Nested so HeadlessUI suppresses this drawer's Esc/outside-click while
           a document preview is open (prevents the preview closing the drawer). -->
      <DocumentPreviewModal />
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { PaperClipIcon } from '@heroicons/vue/20/solid'
import DocumentUploadZone from '@/components/documents/DocumentUploadZone.vue'
import DocumentPreviewModal from '@/components/documents/DocumentPreviewModal.vue'
import { useDocuments } from '@/composables/useDocuments'
import { listDocuments, addDocument, removeDocument } from '@/utils/documentMeta'
import {
  editableMetaFields,
  systemMetaFields,
  buildMetaFromFields,
  isSurfacedElsewhereKey,
  type MetaField,
} from '@/utils/metadataEditor'
import { isValidMetaKey, isEditableMetaKey } from '@/utils/bulkOperations'
import type { TransactionViewModel } from '@/types/transactions'

const props = withDefaults(defineProps<{
  open: boolean
  transaction: TransactionViewModel | null
  /** Show the metadata + system sections (Transactions view). Off = documents only. */
  showMetadata?: boolean
  /** Last-saved meta, used to show staged metadata changes as a diff. */
  baselineMeta?: Record<string, string> | null
}>(), {
  showMetadata: false,
  baselineMeta: null,
})

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  /** New meta for the transaction; the parent decides whether to persist or stage. */
  (e: 'changed', meta: Record<string, string>): void
}>()

const { uploadDocument, openDocument, isUploading } = useDocuments()

const inputClass = 'block w-full rounded-md bg-white px-3 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500'

const docs = computed(() => listDocuments(props.transaction?.meta))

// ── Metadata editing ─────────────────────────────────────────────────────────
// Local editable copy of the free user fields. Re-seeded only when the drawer
// switches to a different transaction (keyed on id) — not on every meta change —
// so typing a value isn't clobbered by the echo-back from our own emit.
const editFields = ref<MetaField[]>([])
const systemFields = computed(() => systemMetaFields(props.transaction?.meta))

watch(
  () => [props.transaction?.id, props.open] as const,
  () => {
    editFields.value = editableMetaFields(props.transaction?.meta).map(f => ({ ...f }))
  },
  { immediate: true },
)

// Diff against the last-saved metadata so staged edits are visible in context.
const baselineEditable = computed(() => {
  const map = new Map<string, string>()
  for (const f of editableMetaFields(props.baselineMeta)) map.set(f.key, f.value)
  return map
})

function baselineValue(key: string): string | undefined {
  return baselineEditable.value.get(key)
}

function fieldStatus(index: number): '' | 'new' | 'changed' {
  const field = editFields.value[index]
  if (!field.key || fieldError(index)) return ''
  if (!baselineEditable.value.has(field.key)) return 'new'
  return baselineEditable.value.get(field.key) !== field.value ? 'changed' : ''
}

// Keys that existed in the last-saved meta but are no longer present (removed,
// or the "from" side of a rename).
const removedFields = computed<MetaField[]>(() => {
  const currentKeys = new Set(editFields.value.filter(f => f.key).map(f => f.key))
  const out: MetaField[] = []
  for (const [key, value] of baselineEditable.value) {
    if (!currentKeys.has(key)) out.push({ key, value })
  }
  return out
})

function fieldError(index: number): string {
  const field = editFields.value[index]
  if (!field.key) return ''
  if (!isValidMetaKey(field.key)) return 'Invalid key (lowercase letters, digits, - _)'
  if (!isEditableMetaKey(field.key) || isSurfacedElsewhereKey(field.key)) return 'Reserved key'
  if (editFields.value.filter(f => f.key === field.key).length > 1) return 'Duplicate key'
  return ''
}

function validFields(): MetaField[] {
  return editFields.value.filter((f, i) => f.key && !fieldError(i))
}

function onFieldsChanged() {
  if (!props.transaction) return
  emit('changed', buildMetaFromFields(props.transaction.meta, validFields()))
}

function addField() {
  editFields.value.push({ key: '', value: '' })
}

function removeField(index: number) {
  editFields.value.splice(index, 1)
  onFieldsChanged()
}

// ── Documents ────────────────────────────────────────────────────────────────
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
