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

              <template v-else>
                <p class="mt-4 text-sm text-gray-600 dark:text-gray-400">
                  These files are not referenced by any transaction or account. Review and select the
                  ones to delete.
                </p>

                <div class="mt-3 max-h-72 overflow-y-auto">
                  <!-- Older orphans (safe to delete; checked by default) -->
                  <div v-if="olderOrphans.length">
                    <div class="flex items-center justify-between">
                      <h4 class="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                        Orphaned documents ({{ olderOrphans.length }})
                      </h4>
                      <button
                        type="button" @click="toggleSection('older')"
                        class="text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
                      >{{ allSelectedIn('older') ? 'Deselect all' : 'Select all' }}</button>
                    </div>
                    <ul class="mt-1 divide-y divide-gray-100 dark:divide-white/5">
                      <li v-for="(o, i) in olderOrphans" :key="o.path" class="flex items-center gap-3 py-2">
                        <input
                          type="checkbox" :checked="isSelected(o.path)" @click.prevent="onItemClick('older', i, $event)"
                          class="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/20 dark:bg-white/5"
                        />
                        <div class="min-w-0 flex-1">
                          <button
                            type="button" @click="openDocument(o.path, o.display_name)"
                            class="block truncate text-left text-sm text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
                          >{{ o.display_name }}</button>
                          <p class="text-xs text-gray-500 dark:text-gray-400">{{ formatSize(o.size) }} · modified {{ formatDate(o.modified) }}</p>
                        </div>
                      </li>
                    </ul>
                  </div>

                  <!-- Recent orphans (may belong to an unsaved draft; unchecked by default) -->
                  <div v-if="recentOrphans.length" class="mt-4">
                    <div class="flex items-center justify-between">
                      <h4 class="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-amber-600 dark:text-amber-400">
                        <ExclamationTriangleIcon class="h-4 w-4" />
                        Recent (last 24h, {{ recentOrphans.length }})
                      </h4>
                      <button
                        type="button" @click="toggleSection('recent')"
                        class="text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
                      >{{ allSelectedIn('recent') ? 'Deselect all' : 'Select all' }}</button>
                    </div>
                    <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                      These were modified recently and may belong to an unsaved draft. They're left
                      unchecked — select them only if you're sure.
                    </p>
                    <ul class="mt-2 divide-y divide-gray-100 dark:divide-white/5">
                      <li v-for="(o, i) in recentOrphans" :key="o.path" class="flex items-center gap-3 py-2">
                        <input
                          type="checkbox" :checked="isSelected(o.path)" @click.prevent="onItemClick('recent', i, $event)"
                          class="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/20 dark:bg-white/5"
                        />
                        <div class="min-w-0 flex-1">
                          <button
                            type="button" @click="openDocument(o.path, o.display_name)"
                            class="block truncate text-left text-sm text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
                          >{{ o.display_name }}</button>
                          <p class="text-xs text-gray-500 dark:text-gray-400">{{ formatSize(o.size) }} · modified {{ formatDate(o.modified) }}</p>
                        </div>
                      </li>
                    </ul>
                  </div>
                </div>
                <p class="mt-2 text-xs text-gray-400 dark:text-gray-500">
                  Tip: shift-click to select a range.
                </p>

                <p class="mt-3 flex items-start gap-2 text-xs text-gray-500 dark:text-gray-400">
                  <InformationCircleIcon class="h-4 w-4 flex-shrink-0" />
                  <span>
                    Deleting permanently removes these files from disk — this can't be undone from
                    Finzytrack. If your documents folder is under version control (e.g. git), you can
                    recover them there.
                  </span>
                </p>
              </template>

              <!-- Footer -->
              <div class="mt-6 flex justify-end gap-3">
                <button
                  type="button" @click="emit('update:open', false)"
                  class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >{{ orphans.length === 0 ? 'Close' : 'Cancel' }}</button>
                <button
                  v-if="orphans.length > 0"
                  type="button" @click="emit('confirm', selected)"
                  :disabled="selected.length === 0 || deleting"
                  class="rounded-md bg-red-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-red-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-red-500 dark:hover:bg-red-400"
                >{{ deleting ? 'Deleting…' : `Delete ${selected.length} selected` }}</button>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>

      <!-- Nested so HeadlessUI suppresses this modal's Esc/outside-click while
           a document preview is open (otherwise closing the preview would also
           close this modal and drop the user back to Settings). -->
      <DocumentPreviewModal />
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { CheckCircleIcon, InformationCircleIcon, ExclamationTriangleIcon } from '@heroicons/vue/24/outline'
import DocumentPreviewModal from '@/components/documents/DocumentPreviewModal.vue'
import { useDocuments } from '@/composables/useDocuments'
import type { OrphanCandidateData } from '@/services/generated-api'

const props = withDefaults(defineProps<{
  open: boolean
  orphans: OrphanCandidateData[]
  graceSeconds?: number
  deleting?: boolean
}>(), {
  graceSeconds: 24 * 60 * 60,
})

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'confirm', paths: string[]): void
}>()

const { openDocument } = useDocuments()

function isRecent(o: OrphanCandidateData): boolean {
  const modifiedMs = Date.parse(o.modified)
  if (Number.isNaN(modifiedMs)) return false
  return (Date.now() - modifiedMs) / 1000 < props.graceSeconds
}

const recentOrphans = computed(() => props.orphans.filter(isRecent))
const olderOrphans = computed(() => props.orphans.filter(o => !isRecent(o)))

// On open, pre-select the older orphans only; recent ones (possible in-flight
// draft uploads) start unchecked so a stray confirm can't delete them.
const selected = ref<string[]>([])
// Anchor for shift-click range selection, tracked per section.
const lastIndex = ref<{ older: number | null; recent: number | null }>({ older: null, recent: null })
watch(
  () => [props.open, props.orphans] as const,
  ([open]) => {
    if (open) {
      selected.value = olderOrphans.value.map(o => o.path)
      lastIndex.value = { older: null, recent: null }
    }
  },
  { immediate: true },
)

type Section = 'older' | 'recent'
function sectionList(section: Section) {
  return section === 'older' ? olderOrphans.value : recentOrphans.value
}

function isSelected(path: string): boolean {
  return selected.value.includes(path)
}

function setSelected(paths: string[], on: boolean) {
  const set = new Set(selected.value)
  for (const p of paths) { if (on) set.add(p); else set.delete(p) }
  selected.value = Array.from(set)
}

function allSelectedIn(section: Section): boolean {
  const list = sectionList(section)
  return list.length > 0 && list.every(o => isSelected(o.path))
}

function toggleSection(section: Section) {
  const paths = sectionList(section).map(o => o.path)
  setSelected(paths, !allSelectedIn(section))
  lastIndex.value[section] = null
}

// Per-row click: toggles the row, or (with shift) applies the toggled state to
// the contiguous range from the last clicked row in the same section.
function onItemClick(section: Section, index: number, event: MouseEvent) {
  const list = sectionList(section)
  const path = list[index].path
  const willSelect = !isSelected(path)
  const anchor = lastIndex.value[section]
  if (event.shiftKey && anchor !== null) {
    const start = Math.min(anchor, index)
    const end = Math.max(anchor, index)
    setSelected(list.slice(start, end + 1).map(o => o.path), willSelect)
  } else {
    setSelected([path], willSelect)
  }
  lastIndex.value[section] = index
}

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
