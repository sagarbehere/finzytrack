<template>
  <TransitionRoot :show="open" as="template">
    <Dialog as="div" class="relative z-50" @close="emit('close')">
      <!-- Backdrop -->
      <TransitionChild
        as="template"
        enter="ease-out duration-300"
        enter-from="opacity-0"
        enter-to="opacity-100"
        leave="ease-in duration-200"
        leave-from="opacity-100"
        leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-gray-500/75 transition-opacity dark:bg-gray-900/50" />
      </TransitionChild>

      <div class="fixed inset-0 z-10 overflow-y-auto">
        <div class="flex min-h-full items-end justify-center p-4 text-center sm:items-center sm:p-0">
          <TransitionChild
            as="template"
            enter="ease-out duration-300"
            enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
            enter-to="opacity-100 translate-y-0 sm:scale-100"
            leave="ease-in duration-200"
            leave-from="opacity-100 translate-y-0 sm:scale-100"
            leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
          >
            <DialogPanel class="relative transform overflow-hidden rounded-lg bg-white text-left shadow-xl transition-all sm:my-8 sm:w-full sm:max-w-xl dark:bg-gray-800 dark:outline dark:-outline-offset-1 dark:outline-white/10">
              <!-- Header -->
              <div class="px-4 pt-5 sm:px-6">
                <DialogTitle as="h3" class="text-base font-semibold text-gray-900 dark:text-white">
                  {{ title }}
                </DialogTitle>

                <!-- Navigation bar -->
                <div class="mt-3 flex items-center gap-2">
                  <button
                    @click="navigateTo(homePath!)"
                    :disabled="!homePath || loading"
                    class="rounded-md bg-white px-2 py-1 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                    title="Home directory"
                  >
                    <HomeIcon class="size-4" />
                  </button>
                  <button
                    @click="navigateTo(parentPath!)"
                    :disabled="!parentPath || loading"
                    class="rounded-md bg-white px-2 py-1 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                    title="Go up one level"
                  >
                    <ArrowUpIcon class="size-4" />
                  </button>
                  <!-- Current path breadcrumb -->
                  <div class="flex-1 min-w-0 rounded-md bg-gray-50 px-3 py-1.5 text-sm font-mono text-gray-600 truncate dark:bg-gray-900 dark:text-gray-400">
                    {{ currentPath }}
                  </div>
                </div>
              </div>

              <!-- File list -->
              <div class="mt-3 px-4 sm:px-6">
                <!-- Loading -->
                <div v-if="loading" class="flex items-center justify-center py-12">
                  <svg class="animate-spin size-6 text-indigo-600 dark:text-indigo-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                </div>

                <!-- Error -->
                <div v-else-if="error" class="rounded-md bg-red-50 p-4 dark:bg-red-500/15 dark:outline dark:outline-red-500/25">
                  <p class="text-sm text-red-700 dark:text-red-400">{{ error }}</p>
                </div>

                <!-- Entries -->
                <ul
                  v-else
                  ref="listRef"
                  class="max-h-72 overflow-y-auto divide-y divide-gray-100 rounded-md outline-1 -outline-offset-1 outline-gray-200 dark:divide-white/5 dark:outline-white/10"
                >
                  <li v-if="entries.length === 0" class="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                    {{ mode === 'directory' ? 'No subdirectories found' : 'No matching files found' }}
                  </li>
                  <li
                    v-for="entry in entries"
                    :key="entry.name"
                    @click="handleEntryClick(entry)"
                    @dblclick="handleEntryDblClick(entry)"
                    :class="[
                      'flex items-center gap-3 px-4 py-2.5 cursor-pointer select-none text-sm',
                      selectedEntry?.name === entry.name && selectedEntry?.type === entry.type
                        ? 'bg-indigo-50 dark:bg-indigo-500/10'
                        : 'hover:bg-gray-50 dark:hover:bg-white/5',
                    ]"
                  >
                    <FolderIcon v-if="entry.type === 'directory'" class="size-5 shrink-0 text-indigo-500 dark:text-indigo-400" />
                    <DocumentIcon v-else class="size-5 shrink-0 text-gray-400 dark:text-gray-500" />
                    <span class="flex-1 truncate text-gray-900 dark:text-white">{{ entry.name }}</span>
                    <span v-if="entry.type === 'file' && entry.size != null" class="shrink-0 text-xs text-gray-400 dark:text-gray-500">
                      {{ formatSize(entry.size) }}
                    </span>
                  </li>
                </ul>
              </div>

              <!-- Selected path preview -->
              <div v-if="selectedPath" class="mt-3 px-4 sm:px-6">
                <p class="text-xs text-gray-500 dark:text-gray-400 truncate" :title="selectedPath">
                  Selected: <span class="font-mono">{{ selectedPath }}</span>
                </p>
              </div>

              <!-- Footer -->
              <div class="mt-4 bg-gray-50 dark:bg-gray-900 px-4 py-3 sm:flex sm:flex-row-reverse sm:px-6">
                <button
                  @click="handleSelect"
                  :disabled="!selectedPath"
                  class="inline-flex w-full justify-center rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed sm:ml-3 sm:w-auto dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400"
                >
                  Select
                </button>
                <button
                  @click="emit('close')"
                  class="mt-3 inline-flex w-full justify-center rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 sm:mt-0 sm:w-auto dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >
                  Cancel
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
import { Dialog, DialogPanel, DialogTitle, TransitionRoot, TransitionChild } from '@headlessui/vue'
import { FolderIcon, DocumentIcon, HomeIcon, ArrowUpIcon } from '@heroicons/vue/24/outline'
import { FilesystemService } from '@/services/generated-api'
import type { FileEntry } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

interface Props {
  open: boolean
  title?: string
  mode: 'file' | 'directory'
  extensions?: string[]   // e.g. ['.beancount', '.bean']
  initialPath?: string    // starting directory or file path
}

const props = withDefaults(defineProps<Props>(), {
  title: 'Select File',
})

const emit = defineEmits<{
  (e: 'select', path: string): void
  (e: 'close'): void
}>()

const loading = ref(false)
const error = ref<string | null>(null)
const currentPath = ref('')
const parentPath = ref<string | null>(null)
const homePath = ref<string | null>(null)
const entries = ref<FileEntry[]>([])
const selectedEntry = ref<FileEntry | null>(null)
const listRef = ref<HTMLUListElement | null>(null)

/** Full path to the currently selected entry (file or directory). */
const selectedPath = ref<string | null>(null)

// ── Load directory contents ─────────────────────────────────────────────────

async function loadDirectory(path?: string) {
  loading.value = true
  error.value = null
  selectedEntry.value = null
  selectedPath.value = null

  try {
    const extensionStr = props.extensions?.join(',') || undefined
    const response = await FilesystemService.browseDirectoryApiFilesystemBrowseGet(
      path || undefined,
      props.mode,
      extensionStr,
    )
    const data = response.data!
    currentPath.value = data.current_path
    parentPath.value = data.parent_path ?? null
    homePath.value = data.home_path
    entries.value = data.entries

    // In directory mode, the current directory is the selection
    if (props.mode === 'directory') {
      selectedPath.value = data.current_path
    }
  } catch (e: any) {
    error.value = e?.body?.error?.message || e?.message || 'Failed to browse directory'
    errorHandler.display(e)
  } finally {
    loading.value = false
  }
}

function navigateTo(path: string) {
  loadDirectory(path)
}

// ── Entry interactions ──────────────────────────────────────────────────────

function handleEntryClick(entry: FileEntry) {
  if (entry.type === 'directory') {
    if (props.mode === 'directory') {
      // In directory mode, clicking a directory selects it
      selectedEntry.value = entry
      selectedPath.value = joinPath(currentPath.value, entry.name)
    } else {
      // In file mode, clicking a directory navigates into it
      navigateTo(joinPath(currentPath.value, entry.name))
    }
  } else {
    // File — select it
    selectedEntry.value = entry
    selectedPath.value = joinPath(currentPath.value, entry.name)
  }
}

function handleEntryDblClick(entry: FileEntry) {
  if (entry.type === 'directory') {
    // Always navigate on double-click
    navigateTo(joinPath(currentPath.value, entry.name))
  } else {
    // Double-click file = select and confirm
    selectedPath.value = joinPath(currentPath.value, entry.name)
    handleSelect()
  }
}

function handleSelect() {
  if (selectedPath.value) {
    emit('select', selectedPath.value)
    emit('close')
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function joinPath(dir: string, name: string): string {
  // Use the separator from the current path (works for both / and \)
  const sep = dir.includes('\\') ? '\\' : '/'
  return dir.endsWith(sep) ? dir + name : dir + sep + name
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Derive the starting directory from a file path or directory path. */
function deriveStartPath(): string | undefined {
  if (!props.initialPath) return undefined
  // If path contains a separator, take the directory part
  const sep = props.initialPath.includes('\\') ? '\\' : '/'
  const lastSep = props.initialPath.lastIndexOf(sep)
  if (lastSep > 0) {
    return props.initialPath.substring(0, lastSep)
  }
  return undefined
}

// ── Load on open ────────────────────────────────────────────────────────────

watch(() => props.open, (isOpen) => {
  if (isOpen) {
    loadDirectory(deriveStartPath())
  }
})
</script>
