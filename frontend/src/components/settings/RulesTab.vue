<template>
  <div class="pb-6">
    <!-- Rule type selector (pill tabs) -->
    <div class="mb-2 flex flex-wrap gap-2 sm:space-x-4 sm:gap-0">
      <button
        v-for="rt in ruleTypes"
        :key="rt.id"
        @click="switchRuleType(rt.id)"
        :class="[
          ruleType === rt.id
            ? 'rounded-md bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300'
            : 'rounded-md px-3 py-2 text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
        ]"
      >
        {{ rt.label }}
      </button>
    </div>

    <p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
      Manage import rule files for each format.
      <a :href="docsUrl" target="_blank" rel="noopener noreferrer" class="text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300">View documentation</a>.
      <template v-if="ruleType !== 'ofx'"> To rename a rule file, create a new one and delete the old.</template>
    </p>

    <!-- Loading state -->
    <div v-if="isLoading" class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 py-8 justify-center">
      <div class="animate-spin h-4 w-4 border-2 border-indigo-500 border-t-transparent rounded-full"></div>
      Loading rules...
    </div>

    <!-- OFX: single-file mode (no file list) -->
    <template v-else-if="ruleType === 'ofx'">
      <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
        <div class="flex flex-col gap-2 border-b border-gray-200 px-4 py-3 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
          <span class="text-sm font-medium text-gray-900 dark:text-white">ofx_mappings.yaml</span>
          <div class="flex items-center gap-2 self-end sm:self-auto">
            <span v-if="isDirty" class="text-xs text-amber-600 dark:text-amber-400">Unsaved changes</span>
            <button
              v-if="isDirty"
              @click="handleCancel"
              class="rounded-md bg-white px-3 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
            >
              Cancel
            </button>
            <button
              @click="handleSave"
              :disabled="!isDirty || isSaving"
              class="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
            >
              {{ isSaving ? 'Saving...' : 'Save' }}
            </button>
          </div>
        </div>
        <div class="p-4">
          <div v-if="editorError" class="mb-3 rounded-md bg-red-50 p-3 dark:bg-red-900/20">
            <div class="flex items-start gap-2">
              <XCircleIcon class="h-5 w-5 shrink-0 text-red-400 dark:text-red-500 mt-0.5" />
              <pre class="text-sm text-red-700 dark:text-red-300 whitespace-pre-wrap font-sans">{{ editorError }}</pre>
            </div>
          </div>
          <textarea
            v-model="editorContent"
            spellcheck="false"
            class="w-full font-mono text-sm rounded-md bg-white px-3 py-2 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500 min-h-[400px] resize-y"
            placeholder="# OFX account mappings YAML..."
          />
        </div>
      </div>
    </template>

    <!-- CSV / XLS / Email: file list + editor -->
    <template v-else>
      <!-- Empty state -->
      <div
        v-if="files.length === 0 && invalidFiles.length === 0 && !isCreating"
        class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700 rounded-lg p-4 text-sm text-yellow-800 dark:text-yellow-200 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
      >
        <span>
          No {{ ruleTypeLabel }} rules found.
          <a :href="docsUrl" target="_blank" rel="noopener noreferrer" class="underline underline-offset-2 hover:text-yellow-900 dark:hover:text-yellow-100">
            Learn how to create {{ ruleTypeLabel }} rule files
          </a>.
        </span>
        <button
          @click="startCreate"
          class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        >
          New Rule
        </button>
      </div>

      <!-- Two-column layout (stacked on mobile) -->
      <div v-else class="flex flex-col gap-4 md:flex-row" style="min-height: 500px;">
        <!-- File list -->
        <div class="w-full md:w-64 md:shrink-0 rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col max-h-48 md:max-h-none">
          <div class="border-b border-gray-200 px-3 py-2.5 dark:border-white/10 flex items-center justify-between">
            <span class="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">Files</span>
            <button
              @click="startCreate"
              class="text-xs font-semibold text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
            >
              + New
            </button>
          </div>
          <ul class="flex-1 overflow-y-auto py-1">
            <li
              v-for="file in allFiles"
              :key="file.filename"
              @click="selectFile(file.filename)"
              :class="[
                'px-3 py-2 text-sm cursor-pointer flex items-center gap-2',
                selectedFile === file.filename
                  ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300'
                  : 'text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-white/5',
              ]"
            >
              <ExclamationTriangleIcon v-if="file.isInvalid" class="h-4 w-4 text-yellow-500 shrink-0" :title="file.error" />
              <span class="truncate">{{ file.filename }}</span>
            </li>
          </ul>
        </div>

        <!-- Editor + Preview (right) -->
        <div class="flex-1 flex gap-4 min-w-0" :class="previewLayoutVertical || !previewSheets ? 'flex-col' : 'flex-row'">
          <!-- Editor card -->
          <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col" :class="!previewLayoutVertical && previewSheets ? 'flex-1 min-w-0' : ''" style="min-height: 500px;">
            <!-- Editor header -->
            <div class="flex flex-col gap-2 border-b border-gray-200 px-4 py-3 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
              <div class="flex items-center gap-2 min-w-0">
                <span v-if="isCreating" class="text-sm font-medium text-gray-900 dark:text-white">New Rule</span>
                <span v-else-if="selectedFile" class="text-sm font-medium text-gray-900 dark:text-white truncate">{{ selectedFile }}</span>
                <span v-else class="text-sm text-gray-500 dark:text-gray-400">Select a file to edit</span>
                <span v-if="isDirty" class="text-xs text-amber-600 dark:text-amber-400">Unsaved changes</span>
              </div>
              <div v-if="selectedFile || isCreating" class="flex items-center gap-2 shrink-0 self-end sm:self-auto">
                <button
                  v-if="selectedFile && !isCreating"
                  @click="handleDelete"
                  :disabled="isDeleting"
                  class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-red-600 shadow-xs inset-ring inset-ring-gray-300 hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed shrink-0 dark:bg-white/10 dark:text-red-400 dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-red-900/20"
                >
                  {{ isDeleting ? 'Deleting...' : 'Delete' }}
                </button>
                <button
                  @click="handleCancel"
                  class="rounded-md bg-white px-3 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >
                  Cancel
                </button>
                <button
                  @click="handleSave"
                  :disabled="!canSave || isSaving"
                  class="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
                >
                  {{ isSaving ? 'Saving...' : 'Save' }}
                </button>
              </div>
            </div>

            <!-- New file: filename input -->
            <div v-if="isCreating" class="border-b border-gray-200 px-4 py-3 dark:border-white/10">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Filename</label>
              <input
                v-model="newFilename"
                type="text"
                placeholder="my-rule.yaml"
                class="block w-full rounded-md bg-white px-3 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
              />
            </div>

            <!-- File upload prompt (CSV / XLS only) -->
            <div v-if="showFilePreview && (selectedFile || isCreating)" class="border-b border-gray-200 px-4 py-3 dark:border-white/10">
              <div class="flex flex-wrap items-center gap-3">
                <DocumentArrowUpIcon class="h-5 w-5 shrink-0 text-gray-400 dark:text-gray-500 hidden sm:block" />
                <div class="flex-1 min-w-0">
                  <span v-if="!previewFileName" class="text-sm text-gray-500 dark:text-gray-400">
                    Upload an example {{ ruleTypeLabel }} file to see a preview that helps you configure the rule.
                  </span>
                  <span v-else class="text-sm text-gray-700 dark:text-gray-300 truncate block">
                    {{ previewFileName }}
                  </span>
                </div>
                <input
                  ref="fileInputRef"
                  type="file"
                  :accept="acceptedExtensions()"
                  class="hidden"
                  @change="handlePreviewFile"
                />
                <button
                  @click="triggerFilePicker"
                  class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >
                  {{ previewFileName ? 'Change file' : 'Choose file' }}
                </button>
                <button
                  v-if="previewFileName"
                  @click="clearPreview"
                  class="text-sm text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                  title="Remove preview"
                >&times;</button>
              </div>
            </div>

            <!-- Textarea -->
            <div class="flex-1 p-4">
              <div v-if="editorError && (selectedFile || isCreating)" class="mb-3 rounded-md bg-red-50 p-3 dark:bg-red-900/20">
                <div class="flex items-start gap-2">
                  <XCircleIcon class="h-5 w-5 shrink-0 text-red-400 dark:text-red-500 mt-0.5" />
                  <pre class="text-sm text-red-700 dark:text-red-300 whitespace-pre-wrap font-sans">{{ editorError }}</pre>
                </div>
              </div>
              <textarea
                v-if="selectedFile || isCreating"
                v-model="editorContent"
                spellcheck="false"
                class="w-full h-full font-mono text-sm rounded-md bg-white px-3 py-2 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500 min-h-[400px] resize-y"
                :placeholder="`# ${ruleTypeLabel} rule YAML...`"
              />
              <div v-else class="flex items-center justify-center h-full text-sm text-gray-400 dark:text-gray-500">
                Select a file from the list or create a new rule
              </div>
            </div>
          </div>

          <!-- File preview card -->
          <div
            v-if="showFilePreview && (previewSheets || previewError) && (selectedFile || isCreating)"
            class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col overflow-hidden"
            :class="!previewLayoutVertical && previewSheets ? 'flex-1 min-w-0' : ''"
          >
            <div class="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-white/10">
              <span class="text-sm font-medium text-gray-900 dark:text-white">File Preview</span>
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400 dark:text-gray-500 hidden sm:inline">Row numbers help identify skip_lines; column numbers help identify column indices</span>
                <button
                  @click="togglePreviewLayout"
                  class="rounded-md bg-white p-1.5 text-gray-500 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 hover:text-gray-700 shrink-0 dark:bg-white/10 dark:text-gray-400 dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 dark:hover:text-gray-200"
                  :title="previewLayoutVertical ? 'Switch to side-by-side layout' : 'Switch to stacked layout'"
                >
                  <!-- Side-by-side icon (currently stacked, click to go side-by-side) -->
                  <svg v-if="previewLayoutVertical" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 4.5v15m6-15v15M4.5 19.5h15a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5h-15A1.5 1.5 0 003 6v12a1.5 1.5 0 001.5 1.5z" />
                  </svg>
                  <!-- Stacked icon (currently side-by-side, click to go stacked) -->
                  <svg v-else class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 9h15m-15 6h15M4.5 4.5h15a1.5 1.5 0 011.5 1.5v12a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18V6a1.5 1.5 0 011.5-1.5z" />
                  </svg>
                </button>
              </div>
            </div>
            <!-- Preview error -->
            <div v-if="previewError" class="p-4">
              <div class="rounded-md bg-red-50 p-3 dark:bg-red-900/20">
                <div class="flex items-start gap-2">
                  <XCircleIcon class="h-5 w-5 shrink-0 text-red-400 dark:text-red-500 mt-0.5" />
                  <p class="text-sm text-red-700 dark:text-red-300">{{ previewError }}</p>
                </div>
              </div>
            </div>
            <!-- Preview table -->
            <FilePreviewTable v-else-if="previewSheets" :sheets="previewSheets" />
          </div>
        </div>
      </div>
    </template>

    <!-- Confirm dialog -->
    <ConfirmDialog
      :is-open="confirmDialog.isOpen.value"
      :title="confirmDialog.dialogOptions.value.title"
      :message="confirmDialog.dialogOptions.value.message"
      :confirm-text="confirmDialog.dialogOptions.value.confirmText"
      :cancel-text="confirmDialog.dialogOptions.value.cancelText"
      :variant="confirmDialog.dialogOptions.value.variant"
      @confirm="confirmDialog.handleConfirm"
      @cancel="confirmDialog.handleCancel"
      @close="confirmDialog.handleClose"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ExclamationTriangleIcon, XCircleIcon, DocumentArrowUpIcon } from '@heroicons/vue/24/outline'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import FilePreviewTable from '@/components/FilePreviewTable.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { ImportService, ApiError, RuleTemplateType } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { extractCsvRows } from '@/composables/useCsvParser'
import { extractXlsSheets } from '@/composables/useXlsParser'
import { getStorageAdapter } from '@/services/storage'
import { STORAGE_KEYS } from '@/services/storage/keys'

import type { CsvRuleSummary } from '@/services/generated-api/models/CsvRuleSummary'
import type { InvalidRuleSummary } from '@/services/generated-api/models/InvalidRuleSummary'
import type { ProfileInfo } from '@/services/generated-api/models/ProfileInfo'
import type { InvalidProfileInfo } from '@/services/generated-api/models/InvalidProfileInfo'

const props = defineProps<{
  initialRuleType?: string
}>()

type RuleTypeId = 'csv' | 'xls' | 'email' | 'ofx'

const ruleTypes = [
  { id: 'csv' as const, label: 'CSV' },
  { id: 'xls' as const, label: 'XLS/XLSX' },
  { id: 'email' as const, label: 'Email' },
  { id: 'ofx' as const, label: 'OFX' },
]

const ruleType = ref<RuleTypeId>(
  ruleTypes.some(rt => rt.id === props.initialRuleType) ? (props.initialRuleType as RuleTypeId) : 'csv'
)

const ruleTypeLabel = computed(() => {
  const rt = ruleTypes.find(r => r.id === ruleType.value)
  return rt?.label ?? ruleType.value.toUpperCase()
})

const docsUrl = computed(() => {
  const map: Record<RuleTypeId, string> = {
    csv: 'https://docs.finzytrack.com/reference/file-import-rules/',
    xls: 'https://docs.finzytrack.com/reference/file-import-rules/',
    email: 'https://docs.finzytrack.com/reference/email-import-rules/',
    ofx: 'https://docs.finzytrack.com/views/import/#ofx',
  }
  return map[ruleType.value]
})

// --- File list state ---

interface FileEntry {
  filename: string
  name?: string
  account?: string
  isInvalid: boolean
  error?: string
}

const files = ref<FileEntry[]>([])
const invalidFiles = ref<FileEntry[]>([])
const allFiles = computed(() => [...files.value, ...invalidFiles.value])

const selectedFile = ref<string | null>(null)
const editorContent = ref('')
const originalContent = ref('')
const isDirty = computed(() => editorContent.value !== originalContent.value)

const isLoading = ref(false)
const isSaving = ref(false)
const isDeleting = ref(false)

const isCreating = ref(false)
const newFilename = ref('')
const editorError = ref<string | null>(null)

const canSave = computed(() => {
  if (isCreating.value) return newFilename.value.trim().length > 0 && editorContent.value.trim().length > 0
  return isDirty.value
})

const confirmDialog = useConfirmDialog()

// --- File preview state (CSV / XLS only) ---

interface FileSheet {
  name: string
  rows: string[][]
}

const previewSheets = ref<FileSheet[] | null>(null)
const previewError = ref<string | null>(null)
const previewFileName = ref<string | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

const showFilePreview = computed(() => ruleType.value === 'csv' || ruleType.value === 'xls')

const previewLayoutVertical = ref(getStorageAdapter().get<string>(STORAGE_KEYS.RULES_TAB_LAYOUT) !== 'horizontal')

function togglePreviewLayout() {
  previewLayoutVertical.value = !previewLayoutVertical.value
  getStorageAdapter().set(STORAGE_KEYS.RULES_TAB_LAYOUT, previewLayoutVertical.value ? 'vertical' : 'horizontal')
}

function acceptedExtensions(): string {
  return ruleType.value === 'csv' ? '.csv,.tsv,.txt' : '.xls,.xlsx,.xlsm,.xlsb'
}

function triggerFilePicker() {
  fileInputRef.value?.click()
}

async function handlePreviewFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // Reset so the same file can be re-selected
  input.value = ''
  if (!file) return
  await parsePreviewFile(file)
}

async function parsePreviewFile(file: File) {
  previewError.value = null
  previewSheets.value = null
  previewFileName.value = file.name

  try {
    const lower = file.name.toLowerCase()
    if (ruleType.value === 'csv') {
      if (!lower.endsWith('.csv') && !lower.endsWith('.tsv') && !lower.endsWith('.txt')) {
        previewError.value = 'Please select a CSV, TSV, or TXT file.'
        return
      }
      const text = await readFileAsText(file)
      const rows = extractCsvRows(text)
      if (rows.length === 0) {
        previewError.value = 'The file appears to be empty.'
        return
      }
      previewSheets.value = [{ name: file.name, rows }]
    } else if (ruleType.value === 'xls') {
      if (!lower.endsWith('.xls') && !lower.endsWith('.xlsx') && !lower.endsWith('.xlsm') && !lower.endsWith('.xlsb')) {
        previewError.value = 'Please select an XLS or XLSX file.'
        return
      }
      const buffer = await readFileAsArrayBuffer(file)
      const sheets = extractXlsSheets(buffer)
      if (sheets.length === 0 || sheets.every(s => s.rows.length === 0)) {
        previewError.value = 'The file appears to be empty or has no readable sheets.'
        return
      }
      previewSheets.value = sheets
    }
  } catch (err) {
    previewError.value = `Failed to parse file: ${err instanceof Error ? err.message : String(err)}`
  }
}

function clearPreview() {
  previewSheets.value = null
  previewError.value = null
  previewFileName.value = null
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => resolve(e.target?.result as string)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsText(file)
  })
}

function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => resolve(e.target?.result as ArrayBuffer)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsArrayBuffer(file)
  })
}

// Clear inline error when user edits content
watch(editorContent, () => {
  if (editorError.value) editorError.value = null
})

// --- Data loading ---

async function loadFileList() {
  isLoading.value = true
  try {
    if (ruleType.value === 'csv') {
      const resp = await ImportService.listCsvRules()
      const data = resp.data!
      files.value = (data.rules ?? []).map((r: CsvRuleSummary) => ({
        filename: r.filename, name: r.name, account: r.default_account, isInvalid: false,
      }))
      invalidFiles.value = (data.invalid_rules ?? []).map((r: InvalidRuleSummary) => ({
        filename: r.filename, isInvalid: true, error: r.error,
      }))
    } else if (ruleType.value === 'xls') {
      const resp = await ImportService.listXlsRules()
      const data = resp.data!
      files.value = (data.rules ?? []).map((r: CsvRuleSummary) => ({
        filename: r.filename, name: r.name, account: r.default_account, isInvalid: false,
      }))
      invalidFiles.value = (data.invalid_rules ?? []).map((r: InvalidRuleSummary) => ({
        filename: r.filename, isInvalid: true, error: r.error,
      }))
    } else if (ruleType.value === 'email') {
      const resp = await ImportService.listEmailProfilesApiImportEmailProfilesGet()
      const data = resp.data!
      files.value = data.profiles.map((p: ProfileInfo) => ({
        filename: p.file, name: p.name, account: p.beancount_account, isInvalid: false,
      }))
      invalidFiles.value = data.invalid_profiles.map((p: InvalidProfileInfo) => ({
        filename: p.filename, isInvalid: true, error: p.error,
      }))
    } else if (ruleType.value === 'ofx') {
      // OFX: single file, load content directly
      const resp = await ImportService.getOfxMappingsRaw()
      const data = resp.data!
      editorContent.value = data.content
      originalContent.value = data.content
    }
  } catch (e) {
    errorHandler.display(e)
  } finally {
    isLoading.value = false
  }
}

async function loadFileContent(filename: string) {
  try {
    let content: string
    if (ruleType.value === 'csv') {
      const resp = await ImportService.getCsvRuleRaw(filename)
      content = resp.data!.content
    } else if (ruleType.value === 'xls') {
      const resp = await ImportService.getXlsRuleRaw(filename)
      content = resp.data!.content
    } else if (ruleType.value === 'email') {
      const resp = await ImportService.getEmailRuleRaw(filename)
      content = resp.data!.content
    } else {
      return
    }
    editorContent.value = content
    originalContent.value = content
  } catch (e) {
    errorHandler.display(e)
  }
}

// --- Actions ---

async function checkDirtyBeforeAction(): Promise<boolean> {
  if (!isDirty.value) return true
  return await confirmDialog.showConfirm({
    title: 'Unsaved Changes',
    message: 'You have unsaved changes. Discard them?',
    confirmText: 'Discard',
    cancelText: 'Cancel',
    variant: 'warning',
  })
}

async function selectFile(filename: string) {
  if (filename === selectedFile.value && !isCreating.value) return
  if (!(await checkDirtyBeforeAction())) return

  isCreating.value = false
  newFilename.value = ''
  selectedFile.value = filename
  await loadFileContent(filename)
}

async function switchRuleType(newType: RuleTypeId) {
  if (newType === ruleType.value) return
  if (!(await checkDirtyBeforeAction())) return

  ruleType.value = newType
  selectedFile.value = null
  isCreating.value = false
  newFilename.value = ''
  editorContent.value = ''
  originalContent.value = ''
  clearPreview()
  await loadFileList()
}

const _ruleTypeToTemplateType: Partial<Record<RuleTypeId, RuleTemplateType>> = {
  csv: RuleTemplateType.CSV,
  xls: RuleTemplateType.XLS,
  email: RuleTemplateType.EMAIL,
}

async function startCreate() {
  if (!(await checkDirtyBeforeAction())) return

  isCreating.value = true
  selectedFile.value = null
  newFilename.value = ''
  editorError.value = null

  // Pre-fill with template for this rule type
  const templateType = _ruleTypeToTemplateType[ruleType.value]
  if (templateType) {
    try {
      const resp = await ImportService.getRuleTemplate(templateType)
      editorContent.value = resp.data!.content
    } catch {
      editorContent.value = ''
    }
  } else {
    editorContent.value = ''
  }
  originalContent.value = ''
}

function handleCancel() {
  editorError.value = null
  if (isCreating.value) {
    isCreating.value = false
    newFilename.value = ''
    editorContent.value = ''
    originalContent.value = ''
  } else {
    editorContent.value = originalContent.value
  }
}

async function handleSave() {
  isSaving.value = true
  editorError.value = null
  try {
    if (ruleType.value === 'ofx') {
      await ImportService.updateOfxMappings({ content: editorContent.value })
      originalContent.value = editorContent.value
    } else if (isCreating.value) {
      const filename = newFilename.value.trim()
      const body = { filename, content: editorContent.value }
      if (ruleType.value === 'csv') {
        await ImportService.createCsvRule(body)
      } else if (ruleType.value === 'xls') {
        await ImportService.createXlsRule(body)
      } else if (ruleType.value === 'email') {
        await ImportService.createEmailRule(body)
      }
      isCreating.value = false
      // Ensure filename has extension for selection
      const savedFilename = filename.endsWith('.yaml') || filename.endsWith('.yml') ? filename : filename + '.yaml'
      await loadFileList()
      selectedFile.value = savedFilename
      originalContent.value = editorContent.value
    } else if (selectedFile.value) {
      const body = { content: editorContent.value }
      if (ruleType.value === 'csv') {
        await ImportService.updateCsvRule(selectedFile.value, body)
      } else if (ruleType.value === 'xls') {
        await ImportService.updateXlsRule(selectedFile.value, body)
      } else if (ruleType.value === 'email') {
        await ImportService.updateEmailRule(selectedFile.value, body)
      }
      originalContent.value = editorContent.value
      await loadFileList()
    }
  } catch (e) {
    if (e instanceof ApiError) {
      editorError.value = e.body?.error?.message || e.statusText || 'Save failed'
    }
    errorHandler.display(e)
  } finally {
    isSaving.value = false
  }
}

async function handleDelete() {
  if (!selectedFile.value) return

  const confirmed = await confirmDialog.showConfirm({
    title: 'Delete Rule',
    message: `Are you sure you want to delete "${selectedFile.value}"? This cannot be undone.`,
    confirmText: 'Delete',
    cancelText: 'Cancel',
    variant: 'danger',
  })
  if (!confirmed) return

  isDeleting.value = true
  try {
    if (ruleType.value === 'csv') {
      await ImportService.deleteCsvRule(selectedFile.value)
    } else if (ruleType.value === 'xls') {
      await ImportService.deleteXlsRule(selectedFile.value)
    } else if (ruleType.value === 'email') {
      await ImportService.deleteEmailRule(selectedFile.value)
    }
    selectedFile.value = null
    editorContent.value = ''
    originalContent.value = ''
    await loadFileList()
  } catch (e) {
    errorHandler.display(e)
  } finally {
    isDeleting.value = false
  }
}

// --- Navigation guard ---

onBeforeRouteLeave(async (_to, _from, next) => {
  if (isDirty.value) {
    const ok = await confirmDialog.showConfirm({
      title: 'Unsaved Changes',
      message: 'You have unsaved changes. Leave without saving?',
      confirmText: 'Leave',
      cancelText: 'Stay',
      variant: 'warning',
    })
    next(ok)
  } else {
    next()
  }
})

// --- Lifecycle ---

// Watch for changes to initialRuleType prop (e.g. when deep-linking changes query)
watch(() => props.initialRuleType, (newType) => {
  if (newType && ruleTypes.some(rt => rt.id === newType) && newType !== ruleType.value) {
    switchRuleType(newType as RuleTypeId)
  }
})

// Initial load
loadFileList()
</script>
