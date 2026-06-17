<template>
  <div
    class="relative border-2 border-dashed rounded-lg p-6 text-center transition-all duration-200"
    :class="[
      isDragOver
        ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20'
        : 'border-gray-300 dark:border-white/10',
      uploading
        ? 'pointer-events-none opacity-75'
        : 'hover:border-indigo-400 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/10',
    ]"
    @drop.prevent="handleDrop"
    @dragover.prevent="handleDragOver"
    @dragenter.prevent="handleDragEnter"
    @dragleave.prevent="handleDragLeave"
  >
    <input
      ref="fileInput"
      type="file"
      :accept="accept"
      :multiple="multiple"
      class="hidden"
      @change="handleFileSelect"
      :disabled="uploading"
    />

    <DocumentArrowUpIcon class="mx-auto h-8 w-8 text-gray-400" />
    <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
      Drop a file here or
      <button
        type="button"
        @click="openFilePicker"
        :disabled="uploading"
        class="font-medium text-indigo-600 underline hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
      >
        browse files
      </button>
    </p>
    <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ hint }}</p>

    <!-- Uploading overlay -->
    <div
      v-if="uploading"
      class="absolute inset-0 flex items-center justify-center rounded-lg bg-white/75 dark:bg-gray-900/75"
    >
      <div class="flex items-center space-x-2">
        <div class="h-5 w-5 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent"></div>
        <span class="text-sm font-medium text-gray-900 dark:text-white">Uploading…</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { DocumentArrowUpIcon } from '@heroicons/vue/24/outline'

withDefaults(defineProps<{
  /** Accept hint for the native picker. image/* surfaces the device camera on mobile. */
  accept?: string
  multiple?: boolean
  uploading?: boolean
  hint?: string
}>(), {
  accept: 'image/*,application/pdf',
  multiple: true,
  uploading: false,
  hint: 'Images and PDFs work best; any file type is accepted.',
})

const emit = defineEmits<{
  (e: 'files-selected', files: File[]): void
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const isDragOver = ref(false)

function emitFiles(fileList: FileList | null | undefined) {
  if (!fileList || fileList.length === 0) return
  emit('files-selected', Array.from(fileList))
}

function handleDrop(e: DragEvent) {
  isDragOver.value = false
  emitFiles(e.dataTransfer?.files)
}
function handleDragEnter() { isDragOver.value = true }
function handleDragOver() { isDragOver.value = true }
function handleDragLeave(e: DragEvent) {
  // Avoid flicker when dragging over child elements.
  if (e.currentTarget instanceof HTMLElement && !e.currentTarget.contains(e.relatedTarget as Node)) {
    isDragOver.value = false
  }
}

function openFilePicker() {
  fileInput.value?.click()
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement
  emitFiles(target.files)
  // Reset so selecting the same file again re-triggers change.
  if (fileInput.value) fileInput.value.value = ''
}
</script>
