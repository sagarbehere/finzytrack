<template>
  <div class="space-y-4">
    <!-- Toolbar -->
    <div class="flex items-center justify-between bg-gray-50 dark:bg-gray-800 px-4 py-3 rounded-lg border border-gray-200 dark:border-white/10">
      <div class="flex items-center gap-3">
        <span class="text-sm font-mono text-gray-700 dark:text-gray-300">{{ filePath }}</span>
        <span
          v-if="readonly"
          class="inline-flex items-center rounded-md px-2 py-1 text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
        >
          Read Only
        </span>
        <span
          v-else-if="isDirty"
          class="px-2 py-1 text-xs font-medium rounded bg-yellow-100 dark:bg-yellow-900 text-yellow-800 dark:text-yellow-200"
        >
          Modified
        </span>
      </div>

      <div class="flex items-center gap-2">
        <button
          v-if="readonly && allowEdit"
          @click="enableEditing"
          class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        >
          Enable Editing
        </button>

        <template v-else-if="!readonly">
          <button
            @click="save"
            :disabled="!isDirty || isSaving"
            class="px-4 py-2 text-sm font-medium rounded-lg transition-colors bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {{ isSaving ? 'Saving...' : 'Save' }}
          </button>
          <button
            @click="revert"
            :disabled="!isDirty"
            class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
          >
            Revert
          </button>
        </template>
      </div>
    </div>

    <!-- Size Warning -->
    <div
      v-if="sizeWarning"
      class="flex items-center gap-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 text-yellow-800 dark:text-yellow-200 px-4 py-3 rounded-lg"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        stroke-width="1.5"
        stroke="currentColor"
        class="w-5 h-5 flex-shrink-0"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
        />
      </svg>
      <span>{{ sizeWarning }}</span>
    </div>

    <!-- Monaco Editor -->
    <MonacoEditor
      v-model="content"
      :language="language"
      :readonly="readonly"
      :options="editorOptions"
      @change="handleChange"
      height="600px"
    />

    <!-- Validation Errors -->
    <div v-if="validationError" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4">
      <div class="flex items-center gap-2 text-red-800 dark:text-red-200 font-medium mb-2">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke-width="1.5"
          stroke="currentColor"
          class="w-5 h-5"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
          />
        </svg>
        <span>Validation Error</span>
      </div>
      <pre class="text-sm text-red-700 dark:text-red-300 font-mono whitespace-pre-wrap">{{ validationError }}</pre>
    </div>

    <!-- Utility Panel (for ledger only) -->
    <UtilityPanel
      v-if="showUtilities && fileType === 'ledger'"
      :content="content"
      @transformed="handleTransformed"
      @reload-requested="loadFile"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import MonacoEditor from './MonacoEditor.vue'
import UtilityPanel from './UtilityPanel.vue'
import { FilesService } from '@/services/generated-api'
import type { Config, ErrorInfo } from '@/services/generated-api'
import { useToast } from '@/composables/useNotifications'

interface Props {
  fileType: 'config' | 'ledger'
  allowEdit?: boolean
  showUtilities?: boolean
}

interface Emits {
  (e: 'saved', config: Config): void
  (e: 'restart-required', reason: string): void
  (e: 'error', message: string): void
}

const props = withDefaults(defineProps<Props>(), {
  allowEdit: true,
  showUtilities: false,
})

const emit = defineEmits<Emits>()
const toast = useToast()

// State
const originalContent = ref('')
const content = ref('')
const filePath = ref('')
const readonly = ref(true) // Always start read-only for safety
const isDirty = ref(false)
const isSaving = ref(false)
const validationError = ref('')
const sizeWarning = ref('')

// Computed
const language = computed(() => {
  return props.fileType === 'config' ? 'yaml' : 'plaintext'
})

const editorOptions = computed(() => ({
  minimap: { enabled: false },
  fontSize: 14,
  lineNumbers: 'on' as const,
  scrollBeyondLastLine: false,
  automaticLayout: true,
  tabSize: 2,
  wordWrap: 'on' as const,
  renderWhitespace: 'selection' as const,
}))

// Methods
async function loadFile() {
  try {
    const endpoint =
      props.fileType === 'config'
        ? FilesService.getConfigFileApiFilesConfigGet
        : FilesService.getLedgerFileApiFilesLedgerGet

    const response = await endpoint()

    if (response.success && response.data) {
      originalContent.value = response.data.content
      content.value = response.data.content
      filePath.value = response.data.path
      sizeWarning.value = response.data.size_warning || ''
      readonly.value = true
      isDirty.value = false
      validationError.value = ''
    } else {
      emit('error', response.error?.message || 'Failed to load file')
    }
  } catch (error: any) {
    emit('error', `Failed to load file: ${error?.message || error}`)
  }
}

function enableEditing() {
  readonly.value = false
}

async function save() {
  if (!isDirty.value || isSaving.value) return

  isSaving.value = true
  validationError.value = ''

  try {
    if (props.fileType === 'config') {
      await saveConfigFile()
    } else {
      await saveLedgerFile()
    }
  } finally {
    isSaving.value = false
  }
}

async function saveConfigFile() {
  try {
    const response = await FilesService.updateConfigFileApiFilesConfigPut({
      content: content.value,
    })

    if (response.success && response.data) {
      // Update local state - use current editor content
      originalContent.value = content.value
      isDirty.value = false

      // Emit updated config so parent can update global cache
      emit('saved', response.data.config)

      // Emit restart info if needed
      if (response.data.restart_required) {
        emit('restart-required', response.data.restart_reason || 'Config changes require restart')
      }

      toast.success('Success', 'Configuration saved successfully')
    } else {
      validationError.value = formatError(response.error)
      emit('error', validationError.value)
    }
  } catch (error: any) {
    if (error.body?.error) {
      validationError.value = formatError(error.body.error)
    } else {
      validationError.value = `Failed to save: ${error?.message || error}`
    }
    emit('error', validationError.value)
  }
}

async function saveLedgerFile() {
  try {
    const response = await FilesService.updateLedgerFileApiFilesLedgerPut({
      content: content.value,
    })

    if (response.success && response.data) {
      // Update local state
      originalContent.value = content.value
      isDirty.value = false

      toast.success('Success', 'Ledger saved successfully')

      // Reload content from server (backend may have transformed it)
      await loadFile()
    } else {
      validationError.value = formatError(response.error)
      emit('error', validationError.value)
    }
  } catch (error: any) {
    if (error.body?.error) {
      validationError.value = formatError(error.body.error)
    } else {
      validationError.value = `Failed to save: ${error?.message || error}`
    }
    emit('error', validationError.value)
  }
}

function revert() {
  if (!isDirty.value) return

  if (window.confirm('Are you sure you want to discard all changes?')) {
    content.value = originalContent.value
    isDirty.value = false
    validationError.value = ''
  }
}

function handleChange(newValue: string) {
  isDirty.value = newValue !== originalContent.value
}

function handleTransformed(newContent: string) {
  // Utility operation transformed content (e.g., sorted)
  // Update editor content
  content.value = newContent
  isDirty.value = newContent !== originalContent.value
}

function formatError(error: ErrorInfo | null | undefined): string {
  if (!error) return 'Unknown error'

  let message = error.message || 'Unknown error'

  if (error.details) {
    if (error.details.line) {
      message += `\n\nLine ${error.details.line}`
      if (error.details.column) {
        message += `, Column ${error.details.column}`
      }
      message += `: ${error.details.problem}`
    } else if (error.details.errors) {
      message += '\n\n' + (error.details.errors as string[]).join('\n')
    }
  }

  return message
}

// Lifecycle
onMounted(() => {
  loadFile()
})
</script>
