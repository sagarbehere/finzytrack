<template>
  <TransitionRoot appear :show="open" as="template">
    <Dialog as="div" @close="emit('update:open', false)" class="relative z-50">
      <TransitionChild
        as="template"
        enter="duration-300 ease-out" enter-from="opacity-0" enter-to="opacity-100"
        leave="duration-200 ease-in" leave-from="opacity-100" leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/25 dark:bg-black/50" />
      </TransitionChild>

      <div class="fixed inset-0 overflow-y-auto">
        <div class="flex min-h-full items-center justify-center p-4">
          <TransitionChild
            as="template"
            enter="duration-300 ease-out" enter-from="opacity-0 scale-95" enter-to="opacity-100 scale-100"
            leave="duration-200 ease-in" leave-from="opacity-100 scale-100" leave-to="opacity-0 scale-95"
          >
            <DialogPanel class="w-full max-w-lg transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 p-6 shadow-xl">
              <DialogTitle as="h3" class="text-lg font-medium text-gray-900 dark:text-white">
                Orphaned documents
              </DialogTitle>

              <!-- No orphans -->
              <div v-if="orphans.length === 0" class="mt-4 flex items-start gap-3 rounded-md bg-green-50 p-4 text-sm dark:bg-green-500/10">
                <CheckCircleIcon class="h-5 w-5 flex-shrink-0 text-green-600 dark:text-green-400" />
                <p class="text-gray-700 dark:text-gray-300">
                  No orphaned documents found. Every file in your documents folder is referenced by the ledger.
                </p>
              </div>

              <!-- Orphans list -->
              <div v-else class="mt-4">
                <p class="text-sm text-gray-600 dark:text-gray-400">
                  These files are not referenced by any transaction or account. Review and select the
                  ones to delete.
                </p>

                <ul class="mt-3 max-h-72 overflow-y-auto divide-y divide-gray-100 dark:divide-white/5">
                  <li v-for="o in orphans" :key="o.path" class="flex items-center gap-3 py-2">
                    <input
                      type="checkbox"
                      :value="o.path"
                      v-model="selected"
                      class="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/20 dark:bg-white/5"
                    />
                    <div class="min-w-0 flex-1">
                      <button
                        type="button"
                        @click="openDocument(o.path)"
                        class="block truncate text-left text-sm text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
                      >
                        {{ o.display_name }}
                      </button>
                      <p class="text-xs text-gray-500 dark:text-gray-400">
                        {{ formatSize(o.size) }} · modified {{ formatDate(o.modified) }}
                      </p>
                    </div>
                  </li>
                </ul>

                <!-- Git recoverability note -->
                <p class="mt-3 flex items-start gap-2 text-xs text-gray-500 dark:text-gray-400">
                  <InformationCircleIcon class="h-4 w-4 flex-shrink-0" />
                  <span>Deleted files can be restored from your git history.</span>
                </p>
              </div>

              <!-- Footer -->
              <div class="mt-6 flex justify-end gap-3">
                <button
                  type="button"
                  @click="emit('update:open', false)"
                  class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >
                  {{ orphans.length === 0 ? 'Close' : 'Cancel' }}
                </button>
                <button
                  v-if="orphans.length > 0"
                  type="button"
                  @click="emit('confirm', selected)"
                  :disabled="selected.length === 0 || deleting"
                  class="rounded-md bg-red-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-red-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-red-500 dark:hover:bg-red-400"
                >
                  {{ deleting ? 'Deleting…' : `Delete ${selected.length} selected` }}
                </button>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { CheckCircleIcon, InformationCircleIcon } from '@heroicons/vue/24/outline'
import { useDocuments } from '@/composables/useDocuments'
import type { OrphanCandidateData } from '@/services/generated-api'

const props = defineProps<{
  open: boolean
  orphans: OrphanCandidateData[]
  deleting?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'confirm', paths: string[]): void
}>()

const { openDocument } = useDocuments()

// All orphans selected by default each time the modal opens with new results.
const selected = ref<string[]>([])
watch(
  () => [props.open, props.orphans] as const,
  ([open]) => { if (open) selected.value = props.orphans.map(o => o.path) },
  { immediate: true },
)

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString()
}
</script>
