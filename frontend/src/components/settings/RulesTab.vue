<template>
  <div class="pb-6">
    <!-- Rule type selector (pill tabs) -->
    <div class="mb-2 flex space-x-4">
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
      <a :href="docsUrl" target="_blank" rel="noopener noreferrer" class="text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300">View documentation</a>
    </p>

    <!-- Loading state -->
    <div v-if="isLoading" class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 py-8 justify-center">
      <div class="animate-spin h-4 w-4 border-2 border-indigo-500 border-t-transparent rounded-full"></div>
      Loading rules...
    </div>

    <!-- OFX: single-file mode (no file list) -->
    <template v-else-if="ruleType === 'ofx'">
      <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
        <div class="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-white/10">
          <span class="text-sm font-medium text-gray-900 dark:text-white">ofx_mappings.yaml</span>
          <div class="flex items-center gap-2">
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
        v-if="files.length === 0 && invalidFiles.length === 0"
        class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700 rounded-lg p-4 text-sm text-yellow-800 dark:text-yellow-200 flex items-center justify-between gap-4"
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

      <!-- Two-column layout -->
      <div v-else class="flex gap-4" style="min-height: 500px;">
        <!-- File list (left) -->
        <div class="w-64 shrink-0 rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col">
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

        <!-- Editor (right) -->
        <div class="flex-1 rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col">
          <!-- Editor header -->
          <div class="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-white/10">
            <div class="flex items-center gap-2 min-w-0">
              <span v-if="isCreating" class="text-sm font-medium text-gray-900 dark:text-white">New Rule</span>
              <span v-else-if="selectedFile" class="text-sm font-medium text-gray-900 dark:text-white truncate">{{ selectedFile }}</span>
              <span v-else class="text-sm text-gray-500 dark:text-gray-400">Select a file to edit</span>
              <span v-if="isDirty" class="text-xs text-amber-600 dark:text-amber-400">Unsaved changes</span>
            </div>
            <div v-if="selectedFile || isCreating" class="flex items-center gap-2 shrink-0">
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

          <!-- Textarea -->
          <div class="flex-1 p-4">
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
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ExclamationTriangleIcon } from '@heroicons/vue/24/outline'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { ImportService } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

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
    csv: 'https://finzytrack.app/docs/csv-rules',
    xls: 'https://finzytrack.app/docs/xls-rules',
    email: 'https://finzytrack.app/docs/email-rules',
    ofx: 'https://finzytrack.app/docs/ofx-rules',
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

const canSave = computed(() => {
  if (isCreating.value) return newFilename.value.trim().length > 0 && editorContent.value.trim().length > 0
  return isDirty.value
})

const confirmDialog = useConfirmDialog()

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
  await loadFileList()
}

async function startCreate() {
  if (!(await checkDirtyBeforeAction())) return

  isCreating.value = true
  selectedFile.value = null
  newFilename.value = ''
  editorContent.value = ''
  originalContent.value = ''
}

function handleCancel() {
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
