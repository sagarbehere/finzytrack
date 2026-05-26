<template>
  <div class="w-full">
    <!-- AI not configured banner -->
    <div v-if="!config?.ai?.llm?.is_configured" class="rounded-lg border border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 p-4 space-y-2">
      <p class="text-sm font-medium text-amber-800 dark:text-amber-300">AI is not configured.</p>
      <p class="text-sm text-amber-700 dark:text-amber-400">
        Set a model under <strong>AI</strong> in <router-link to="/settings" class="underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-200">Settings</router-link>.
        <a href="https://docs.finzytrack.com/quick-start/#configuring-ai" target="_blank" rel="noopener noreferrer" class="underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-200">Learn more</a>.
      </p>
    </div>

    <!-- File Upload Widget (hidden when AI not configured) -->
    <template v-else>
    <div
      class="relative border-2 border-dashed rounded-lg p-8 text-center transition-all duration-200"
      :class="[
        isDragOver
          ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20'
          : 'border-gray-300 dark:border-white/10',
        isParsing
          ? 'pointer-events-none opacity-75'
          : 'hover:border-indigo-400 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/10',
      ]"
      @drop.prevent="handleDrop"
      @dragover.prevent="handleDragOver"
      @dragenter.prevent="handleDragEnter"
      @dragleave.prevent="handleDragLeave"
    >
      <!-- Hidden file input -->
      <input
        ref="fileInput"
        type="file"
        accept=".csv,.tsv,.txt,.xls,.xlsx,.xlsm,.xlsb,.pdf,.eml,.jpg,.jpeg,.png,.gif,.webp"
        class="hidden"
        @change="handleFileSelect"
        :disabled="isParsing"
      />

      <!-- Upload prompt -->
      <div v-if="!selectedFile">
        <DocumentArrowUpIcon class="mx-auto h-12 w-12 text-gray-400" />
        <div class="mt-4">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white">Upload File for AI Parsing</h3>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            Drop your file here or
            <button
              @click="openFilePicker"
              :disabled="isParsing"
              class="text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300 font-medium underline"
            >
              browse files
            </button>
          </p>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Supported formats: CSV, XLS/XLSX, PDF, EML, JPG, PNG
          </p>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
            AI can make mistakes — review output carefully.
            <a href="https://docs.finzytrack.com/reference/ai-data-sharing/" target="_blank" rel="noopener noreferrer" class="text-indigo-500 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 underline underline-offset-2">Data shared with AI</a>
          </p>
        </div>
      </div>

      <!-- Selected file info -->
      <div v-else class="flex items-center justify-center space-x-3">
        <DocumentCheckIcon class="h-8 w-8 text-green-500" />
        <div class="text-left">
          <p class="text-sm font-medium text-gray-900 dark:text-white">
            {{ selectedFile.name }}
          </p>
          <p class="text-xs text-gray-500 dark:text-gray-400">
            {{ formatFileSize(selectedFile.size) }}
          </p>
        </div>
        <button
          @click="handleClearFile"
          :disabled="isParsing"
          class="ml-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          title="Remove file"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <!-- Processing overlay -->
      <div
        v-if="isParsing"
        class="absolute inset-0 flex items-center justify-center bg-white/75 dark:bg-gray-900/75 rounded-lg"
      >
        <div class="flex flex-col items-center space-y-1">
          <div class="flex items-center space-x-2">
            <div
              class="animate-spin h-5 w-5 border-2 border-indigo-500 border-t-transparent rounded-full"
            ></div>
            <span class="text-sm font-medium text-gray-900 dark:text-white">
              {{ parsingStatus || 'AI is parsing transactions...' }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Account and Currency Selection + Parse Button -->
    <div v-if="selectedFile" class="mt-6">
      <!-- Error display (inline, not toast) -->
      <FormFeedback
        v-if="parseError"
        :message="parseError"
        severity="error"
        class="mb-4"
      />

      <!-- Warning display -->
      <FormFeedback
        v-if="parseWarning && !parseError"
        :message="parseWarning"
        severity="warning"
        class="mb-4"
      />

      <div class="bg-white dark:bg-gray-800 rounded-lg p-4 border dark:border-white/10">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
          <div>
            <AccountDropdown
              v-model="selectedAccount"
              label="Beancount Account"
              :account-types="['Assets', 'Liabilities']"
              :allow-custom="true"
              placeholder="Select or type account name..."
            />
          </div>
          <div>
            <CommodityDropdown
              v-model="selectedCurrency"
              label="Currency"
              :allow-custom="true"
              placeholder="Select or type currency..."
            />
          </div>
        </div>

        <div class="flex justify-end">
          <button
            @click="parseWithAI"
            :disabled="!canProceed"
            class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500 flex items-center gap-2"
          >
            <SparklesIcon class="h-4 w-4" />
            <span>{{ isParsing ? 'Parsing...' : 'Parse with AI' }}</span>
          </button>
        </div>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup lang="ts">
  import { ref, computed } from 'vue'
  import {
    DocumentArrowUpIcon,
    DocumentCheckIcon,
    XMarkIcon,
    SparklesIcon,
  } from '@heroicons/vue/24/outline'
  import FormFeedback from '@/components/common/FormFeedback.vue'
  import AccountDropdown from '@/components/common/AccountDropdown.vue'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import { ImportService } from '@/services/generated-api'
  import { useConfig } from '@/composables/useConfig'
  import type { CsvParsedTransaction, CsvFileDetails } from '@/types/csv'
  import { toMoney } from '@/utils/money'

  const { config } = useConfig()

  const emit = defineEmits<{
    (e: 'proceedWithImport', payload: {
      file: File
      details: CsvFileDetails
      account: string
      currency: string
    }): void
    (e: 'fileCleared'): void
  }>()

  // State
  const selectedFile = ref<File | null>(null)
  const selectedAccount = ref<string>('')
  const selectedCurrency = ref<string>('')
  const isParsing = ref(false)
  const parseError = ref<string | null>(null)
  const parseWarning = ref<string | null>(null)
  const parsingStatus = ref<string | null>(null)
  const fileInput = ref<HTMLInputElement | null>(null)
  const isDragOver = ref(false)

  const canProceed = computed(() =>
    selectedFile.value && selectedAccount.value && selectedCurrency.value && !isParsing.value
  )

  // File handlers
  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    isDragOver.value = false
    const files = e.dataTransfer?.files
    if (files && files.length > 0) {
      setFile(files[0])
    }
  }

  const handleDragEnter = (e: DragEvent) => { e.preventDefault(); isDragOver.value = true }
  const handleDragOver = (e: DragEvent) => { e.preventDefault(); isDragOver.value = true }
  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault()
    if (e.currentTarget instanceof HTMLElement && !e.currentTarget.contains(e.relatedTarget as Node)) {
      isDragOver.value = false
    }
  }

  const openFilePicker = () => fileInput.value?.click()

  const handleFileSelect = (e: Event) => {
    const target = e.target as HTMLInputElement
    const files = target.files
    if (files && files.length > 0) {
      setFile(files[0])
    }
  }

  const setFile = (file: File) => {
    selectedFile.value = file
    parseError.value = null
    parseWarning.value = null
  }

  const handleClearFile = () => {
    selectedFile.value = null
    parseError.value = null
    parseWarning.value = null
    if (fileInput.value) fileInput.value.value = ''
    emit('fileCleared')
  }

  /** Call the LLM parse endpoint via the generated API client. */
  const callLlmParse = async (forceTextExtraction = false) => {
    return ImportService.llmParseApiImportLlmParsePost({
      file: selectedFile.value!,
      account: selectedAccount.value,
      currency: selectedCurrency.value,
      text_extraction: forceTextExtraction ? 'true' : 'false',
    })
  }

  /** Extract error code and message from an API error. */
  const extractError = (error: any): { code: string; message: string } => {
    const body = error?.body
    if (body?.error?.code && body?.error?.message) {
      return { code: body.error.code, message: body.error.message }
    }
    if (typeof body === 'string') {
      return { code: '', message: body }
    }
    return { code: '', message: error?.message || 'An unexpected error occurred while parsing the file.' }
  }

  // Parse with AI
  const parseWithAI = async () => {
    if (!selectedFile.value || !selectedAccount.value || !selectedCurrency.value) return

    isParsing.value = true
    parseError.value = null
    parseWarning.value = null
    parsingStatus.value = null

    try {
      let response: any
      try {
        response = await callLlmParse()
      } catch (error: any) {
        const { code, message } = extractError(error)
        if (code === 'PDF_NATIVE_NOT_SUPPORTED') {
          // Show warning immediately and retry with text extraction
          parseWarning.value = 'Your model does not support native PDF input. Retrying with local text extraction...'
          parsingStatus.value = 'Retrying with text extraction...'
          response = await callLlmParse(true)
        } else {
          parseError.value = message
          return
        }
      }

      const data = response.data
      if (!data || !data.transactions || data.transactions.length === 0) {
        parseError.value = 'No transactions were found in the file.'
        return
      }

      // Append any backend warnings (e.g., validation)
      if (data.warning) {
        parseWarning.value = parseWarning.value
          ? `${parseWarning.value}; ${data.warning}`
          : data.warning
      }

      // Update the "retrying" warning to its final form
      if (parseWarning.value?.includes('Retrying with')) {
        parseWarning.value = parseWarning.value.replace(
          'Retrying with local text extraction...',
          'PDF was processed using local text extraction, which may reduce accuracy for complex layouts.'
        )
      }

      // Convert to CsvParsedTransaction format (same shape).
      // LLM endpoint returns Decimal strings; canonicalise via toMoney so
      // downstream code sees the same form CSV/XLS parsers produce.
      const transactions: CsvParsedTransaction[] = data.transactions.map((tx: any) => ({
        date: tx.date,
        payee: tx.payee || '',
        narration: tx.narration || '',
        amount: toMoney(tx.amount ?? 0),
        memo: tx.memo || '',
      }))

      // Compute date range
      const dates = transactions.map(tx => tx.date).filter(Boolean).sort()
      const startDate = dates.length > 0 ? dates[0] : null
      const endDate = dates.length > 0 ? dates[dates.length - 1] : null

      const details: CsvFileDetails = {
        filename: selectedFile.value.name,
        ruleName: 'AI Import',
        transactionCount: transactions.length,
        startDate,
        endDate,
        rawTransactions: transactions,
      }

      emit('proceedWithImport', {
        file: selectedFile.value,
        details,
        account: selectedAccount.value,
        currency: selectedCurrency.value,
      })
    } catch (error: any) {
      // Retry also failed
      const { message } = extractError(error)
      parseError.value = message
    } finally {
      isParsing.value = false
      parsingStatus.value = null
    }
  }

  // Utils
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes'
    const k = 1024
    const sizes = ['Bytes', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }
</script>
