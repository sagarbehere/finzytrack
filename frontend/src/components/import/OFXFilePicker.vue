<template>
  <div class="w-full">
    <!-- File Upload Widget -->
    <div
      class="relative border-2 border-dashed rounded-lg p-8 text-center transition-all duration-200"
      :class="[
        isDragOver
          ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
          : 'border-gray-300 dark:border-gray-600',
        isProcessing
          ? 'pointer-events-none opacity-75'
          : 'hover:border-blue-400 hover:bg-blue-50/50 dark:hover:bg-blue-900/10',
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
        accept=".ofx,.qfx,.OFX,.QFX"
        class="hidden"
        @change="handleFileSelect"
        :disabled="isProcessing"
      />

      <!-- Upload icon and content -->
      <div v-if="!selectedFile">
        <DocumentArrowUpIcon class="mx-auto h-12 w-12 text-gray-400" />
        <div class="mt-4">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white">Upload OFX/QFX File</h3>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            Drop your bank statement file here or
            <button
              @click="openFilePicker"
              :disabled="isProcessing"
              class="text-blue-600 hover:text-blue-500 dark:text-blue-400 dark:hover:text-blue-300 font-medium underline"
            >
              browse files
            </button>
          </p>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Supported formats: .ofx, .qfx files
          </p>
        </div>
      </div>

      <!-- Selected file info -->
      <div
        v-else-if="selectedFile && !parseError"
        class="flex items-center justify-center space-x-3"
      >
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
          class="ml-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          title="Remove file"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <!-- Error state -->
      <div v-else-if="parseError" class="flex items-center justify-center space-x-3">
        <ExclamationTriangleIcon class="h-8 w-8 text-red-500" />
        <div class="text-left">
          <p class="text-sm font-medium text-red-900 dark:text-red-300">
            {{ selectedFile?.name }}
          </p>
          <p class="text-xs text-red-600 dark:text-red-400">Failed to parse file</p>
        </div>
        <button
          @click="handleClearFile"
          class="ml-4 text-red-400 hover:text-red-600 dark:hover:text-red-300"
          title="Remove file"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <!-- Processing state -->
      <div
        v-if="isProcessing"
        class="absolute inset-0 flex items-center justify-center bg-white/75 dark:bg-gray-900/75 rounded-lg"
      >
        <div class="flex items-center space-x-2">
          <div
            class="animate-spin h-5 w-5 border-2 border-blue-500 border-t-transparent rounded-full"
          ></div>
          <span class="text-sm font-medium text-gray-900 dark:text-white">
            {{ isParsing ? 'Parsing OFX file...' : isDetecting ? 'Detecting account...' : isLearning ? 'Learning account...' : 'Processing...' }}
          </span>
        </div>
      </div>
    </div>

    <!-- File Details or Error Display -->
    <div v-if="selectedFile" class="mt-6">
      <!-- Parsing Error -->
      <FormFeedback 
        v-if="parseError"
        :message="parseError"
        severity="error"
        details="Please ensure you selected a valid OFX or QFX file from your bank."
        class="mb-4"
      />

      <!-- File Details AND Account Detection Results -->
      <div v-else-if="fileDetails">
        <!-- File Summary (compact) -->
        <div class="bg-gray-50 dark:bg-gray-800 rounded-lg p-4 mb-4">
          <h3 class="text-lg font-semibold text-gray-900 dark:text-white mb-3">File Details</h3>
          <div class="flex flex-wrap gap-6 text-sm">
            <div>
              <span class="text-gray-500 dark:text-gray-400">Transactions:</span>
              <span class="ml-1 font-medium text-gray-900 dark:text-white">{{
                fileDetails.transactionCount
              }}</span>
            </div>
            <div>
              <span class="text-gray-500 dark:text-gray-400">Date Range:</span>
              <span class="ml-1 font-medium text-gray-900 dark:text-white">
                {{ formatDateRange(fileDetails.startDate, fileDetails.endDate) }}
              </span>
            </div>
            <div v-if="fileDetails.balance !== undefined">
              <span class="text-gray-500 dark:text-gray-400">Balance:</span>
              <span class="ml-1 font-medium text-gray-900 dark:text-white">
                {{ formatCurrency(fileDetails.balance, fileDetails.currency) }}
              </span>
            </div>
          </div>
        </div>

        <!-- Account Detection Results -->
        <div class="bg-white dark:bg-gray-800 rounded-lg p-4 border dark:border-gray-700">
          <!-- Detection Status -->
          <FormFeedback 
            v-if="formLevelMessage"
            :message="formLevelMessage"
            :severity="formLevelSeverity"
            class="mb-4"
          />

          <!-- Editable Account Information -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
            <div>
              <AccountDropdown 
                v-model="selectedAccount"
                label="Beancount Account"
                :account-types="['Assets', 'Liabilities']"
                :allow-custom="true"
                placeholder="Select or type account name..."
                :custom-class="fieldErrors.beancount_account ? 'border-red-300 dark:border-red-600' : ''"
              />
              <!-- Display field errors for the account dropdown -->
              <div v-if="fieldErrors.beancount_account" class="mt-1 text-sm text-red-600 dark:text-red-400">
                {{ fieldErrors.beancount_account }}
              </div>
            </div>
            <div>
              <!-- Show all commodities (to filter only currencies, add :commodity-types="['Currency']" prop) -->
              <CommodityDropdown 
                v-model="selectedCurrency"
                label="Currency"
                :allow-custom="true"
                placeholder="Select or type currency..."
                :custom-class="fieldErrors.currency ? 'border-red-300 dark:border-red-600' : ''"
              />
              <!-- Display field errors for the currency dropdown -->
              <div v-if="fieldErrors.currency" class="mt-1 text-sm text-red-600 dark:text-red-400">
                {{ fieldErrors.currency }}
              </div>
            </div>
          </div>

          <!-- Action Buttons -->
          <div class="flex justify-between">
            <button
              @click="learnAccount"
              :disabled="isLearning || !selectedAccount || !selectedCurrency"
              class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
            >
              <span v-if="!isLearning">Learn Account</span>
              <span v-else class="flex items-center">
                <svg
                  class="animate-spin -ml-1 mr-2 h-4 w-4 text-white"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    class="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    stroke-width="4"
                  ></circle>
                  <path
                    class="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  ></path>
                </svg>
                Learning...
              </span>
            </button>

            <button
              @click="proceedWithImport"
              :disabled="!selectedAccount || !selectedCurrency"
              class="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2"
            >
              Proceed
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { ref, computed } from 'vue'
  import {
    DocumentArrowUpIcon,
    DocumentCheckIcon,
    ExclamationTriangleIcon,
    XMarkIcon,
  } from '@heroicons/vue/24/outline'
  import FormFeedback from '@/components/common/FormFeedback.vue'
  import AccountDropdown from '@/components/common/AccountDropdown.vue'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import { useOfxParser, type OfxFileDetails } from '@/composables/useOfxParser'
  import { useAccountDetector } from '@/composables/useAccountDetector'

  // Emits
  const emit = defineEmits<{
    (e: 'proceedWithImport', payload: { 
        file: File; 
        details: OfxFileDetails, 
        account: string, 
        currency: string 
    }): void
  }>()

  // Composables
  const { 
    selectedFile, 
    fileDetails, 
    parseError, 
    isParsing, 
    processFile, 
    clearFile 
  } = useOfxParser()

  const { 
    selectedAccount, 
    selectedCurrency, 
    isDetecting, 
    isLearning, 
    fieldErrors, 
    formLevelMessage, 
    formLevelSeverity, 
    learnAccount 
  } = useAccountDetector(fileDetails)

  // UI State
  const fileInput = ref<HTMLInputElement | null>(null)
  const isDragOver = ref<boolean>(false)

  const isProcessing = computed(() => isParsing.value || isDetecting.value || isLearning.value)

  // Event Handlers
  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    isDragOver.value = false
    const files = e.dataTransfer?.files
    if (files && files.length > 0) {
      processFile(files[0])
    }
  }

  const handleDragEnter = (e: DragEvent) => { e.preventDefault(); isDragOver.value = true; }
  const handleDragOver = (e: DragEvent) => { e.preventDefault(); isDragOver.value = true; }
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
      processFile(files[0])
    }
  }

  const handleClearFile = () => {
    clearFile()
    if (fileInput.value) fileInput.value.value = ''
  }

  const proceedWithImport = () => {
    if (selectedFile.value && fileDetails.value && selectedAccount.value && selectedCurrency.value) {
      emit('proceedWithImport', {
        file: selectedFile.value,
        details: fileDetails.value,
        account: selectedAccount.value,
        currency: selectedCurrency.value,
      })
    }
  }

  // Utility Functions
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes'
    const k = 1024
    const sizes = ['Bytes', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  const formatDateRange = (startDate: string | null, endDate: string | null): string => {
    if (!startDate || !endDate) return 'Unknown'
    const formatDate = (date: string) =>
      new Date(date).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    return `${formatDate(startDate)} - ${formatDate(endDate)}`
  }

  const formatCurrency = (amount: number | null | undefined, currency: string = 'USD'): string => {
    if (amount === null || amount === undefined) return 'Unknown'
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency }).format(amount)
  }
</script>