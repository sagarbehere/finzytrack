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
          @click="clearFile"
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
          @click="clearFile"
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
          <span class="text-sm font-medium text-gray-900 dark:text-white">Parsing OFX file...</span>
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
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Beancount Account
              </label>
              <input
                v-model="selectedAccount"
                v-form-error="fieldErrors.beancount_account"
                type="text"
                class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:text-white"
                placeholder="Assets:Bank:Account"
              />
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Currency
              </label>
              <input
                v-model="selectedCurrency"
                v-form-error="fieldErrors.currency"
                type="text"
                maxlength="10"
                class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:text-white"
                placeholder="USD"
              />
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
  import { ref, computed, watch } from 'vue'
  import {
    DocumentArrowUpIcon,
    DocumentCheckIcon,
    ExclamationTriangleIcon,
    XMarkIcon,
  } from '@heroicons/vue/24/outline'
  import { useToast } from '@/composables/useNotifications'
  import { errorHandler } from '@/utils/ErrorHandler'
  import FormFeedback from '@/components/common/FormFeedback.vue'
  import {
    ImportService,
    ApiError,
    type ValidationError,
    type OFXDetectionRequest,
    type LearnAccountRequest,
    type CreateAccountRequest,
  } from '@/services/generated-api'

  // Define component-specific types
  interface OfxFileDetails {
    institution: string
    institutionFid: string | null
    accountType: string
    accountId: string
    currency: string
    transactionCount: number
    startDate: string | null
    endDate: string | null
    balance: number
  }

  const { success, error, info } = useToast()

  // Props and emits
  const emit = defineEmits<{ (e: 'fileSelected', payload: { file: File; details: OfxFileDetails }): void
    (e: 'fileCleared'): void
    (e: 'parseError', error: string): void
    (e: 'proceedWithImport', payload: { file: File; details: OfxFileDetails, account: string, currency: string }): void
  }>()

  // Reactive state
  const fileInput = ref<HTMLInputElement | null>(null)
  const selectedFile = ref<File | null>(null)
  const fileDetails = ref<OfxFileDetails | null>(null)
  const parseError = ref<string | null>(null)
  const isProcessing = ref<boolean>(false)
  const isDragOver = ref<boolean>(false)

  const accountDetected = ref<boolean>(false)
  const selectedAccount = ref<string>('')
  const selectedCurrency = ref<string>('')
  const isLearning = ref<boolean>(false)

  // A single reactive object to hold all form field errors
  const fieldErrors = ref<{ [key: string]: string }>({});

  // Form-level feedback computed properties
  const formLevelMessage = computed<string>(() => {
    if (accountDetected.value) {
      return 'Account detected successfully'
    } else if (fileDetails.value && Object.keys(fieldErrors.value).length === 0) {
      return 'No matching account found. Please verify account details.'
    }
    return ''
  })

  const formLevelSeverity = computed<'success' | 'warning'>(() => {
    return accountDetected.value ? 'success' : 'warning'
  })

  // Clear field errors when user types
  watch(selectedAccount, () => {
    if (fieldErrors.value.beancount_account) delete fieldErrors.value.beancount_account;
  })
  
  watch(selectedCurrency, () => {
    if (fieldErrors.value.currency) delete fieldErrors.value.currency;
  })

  // Error handling logic
  const handleApiError = (error: unknown) => {
    // Always clear previous errors on a new API call
    fieldErrors.value = {};
    let wasHandledAsFieldLevelError = false;

    if (error instanceof ApiError && error.body?.error?.code === 'VALIDATION_ERROR') {
      const details = error.body.error.details;
      const message = error.body.error.message;

      // Case 1: Handle our manual business logic errors (has `details.field`)
      if (details?.field && typeof details.field === 'string') {
        fieldErrors.value[details.field] = message;
        wasHandledAsFieldLevelError = true;
      }

      // Case 2: Handle Pydantic validation errors (has `details.validation_errors`)
      if (details?.validation_errors) {
        (details.validation_errors as ValidationError[]).forEach(err => {
          // The field name is the last item in the `loc` array
          const fieldName = err.loc[err.loc.length - 1];
          if (typeof fieldName === 'string') {
            fieldErrors.value[fieldName] = err.msg;
            wasHandledAsFieldLevelError = true;
          }
        });
      }
    }

    // If the error was not a field-level validation error that we could map,
    // or if it was any other type of error, pass it to the global handler.
    // This ensures NO errors are ever swallowed.
    if (!wasHandledAsFieldLevelError) {
      errorHandler.display(error);
    }
  }

  // Drag and drop handlers
  const handleDragEnter = (e: DragEvent) => {
    e.preventDefault()
    isDragOver.value = true
  }

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault()
    isDragOver.value = true
  }

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault()
    if (e.currentTarget instanceof HTMLElement && !e.currentTarget.contains(e.relatedTarget as Node)) {
      isDragOver.value = false
    }
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    isDragOver.value = false
    const files = e.dataTransfer?.files
    if (files && files.length > 0) {
      processFile(files[0])
    }
  }

  // File selection handlers
  const openFilePicker = () => {
    if (fileInput.value) {
      fileInput.value.click()
    }
  }

  const handleFileSelect = (e: Event) => {
    const target = e.target as HTMLInputElement
    const files = target.files
    if (files && files.length > 0) {
      processFile(files[0])
    }
  }

  // File validation
  const validateFile = (file: File): boolean => {
    const validExtensions = ['.ofx', '.qfx']
    const fileName = file.name.toLowerCase()
    return validExtensions.some((ext) => fileName.endsWith(ext))
  }

  // File processing
  const processFile = async (file: File) => {
    if (!validateFile(file)) {
      parseError.value = 'Invalid file type. Please select an OFX or QFX file.'
      return
    }

    clearFile()
    selectedFile.value = file
    isProcessing.value = true

    try {
      const fileContent = await readFileAsText(file)
      await parseOFXContent(fileContent)
      if (fileDetails.value) {
        emit('fileSelected', { file: file, details: fileDetails.value })
      }
      await detectAccount()
    } catch (err: any) {
      console.error('Error processing OFX file:', err)
      const errorMessage = err.message || 'Failed to parse OFX file. Please check the file format.'
      parseError.value = errorMessage
      emit('parseError', errorMessage)
    } finally {
      isProcessing.value = false
    }
  }

  // File reading utility
  const readFileAsText = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (e) => resolve(e.target?.result as string)
      reader.onerror = () => reject(new Error('Failed to read file'))
      reader.readAsText(file)
    })
  }

  // OFX parsing using ofx-js
  const parseOFXContent = async (content: string) => {
    try {
      const { parse } = await import('ofx-js')
      const parsed = await parse(content)
      if (!parsed) throw new Error('Invalid OFX file format')
      fileDetails.value = extractFileDetails(parsed)
    } catch (err: any) {
      throw new Error(`OFX parsing failed: ${err.message}`)
    }
  }

  function getStatementDateRange(tranlist: any): { startDate: string | null; endDate: string | null } {
    const parseOfxDate = (dateString: string | undefined): string | null => {
      if (!dateString || dateString.length < 8) return null;
      const year = dateString.substring(0, 4);
      const month = dateString.substring(4, 6);
      const day = dateString.substring(6, 8);
      return `${year}-${month}-${day}`;
    };

    // 1. Prioritize statement dates
    let startDate = parseOfxDate(tranlist?.DTSTART);
    let endDate = parseOfxDate(tranlist?.DTEND);

    // 2. Fallback to transaction dates if statement dates are missing
    if ((!startDate || !endDate) && tranlist && tranlist.STMTTRN) {
      const transactions = Array.isArray(tranlist.STMTTRN)
        ? tranlist.STMTTRN
        : [tranlist.STMTTRN];

      if (transactions.length > 0) {
        const dates = transactions
          .map((t: any) => t.DTPOSTED)
          .filter((d: any) => d)
          .map((d: string) => new Date(d.substring(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')))
          .sort((a: Date, b: Date) => a.getTime() - b.getTime());

        if (dates.length > 0) {
          // Only set if the primary method failed
          if (!startDate) {
            startDate = dates[0].toISOString().split('T')[0];
          }
          if (!endDate) {
            endDate = dates[dates.length - 1].toISOString().split('T')[0];
          }
        }
      }
    }

    return { startDate, endDate };
  }

  const extractFileDetails = (parsedOFX: any): OfxFileDetails => {
    const body = parsedOFX.OFX || parsedOFX
    const signon = body.SIGNONMSGSRSV1?.SONRS?.FI
    const stmt =
      body.BANKMSGSRSV1?.STMTTRNRS?.STMTRS || body.CREDITCARDMSGSRSV1?.CCSTMTTRNRS?.CCSTMTRS
    if (!stmt) throw new Error('No statement data found in OFX file')

    const acctfrom = stmt.BANKACCTFROM || stmt.CCACCTFROM
    const tranlist = stmt.BANKTRANLIST

    let accountType = ''
    if (body.CREDITCARDMSGSRSV1) {
      accountType = ''
    } else if (body.BANKMSGSRSV1 && acctfrom?.ACCTTYPE) {
      accountType = acctfrom.ACCTTYPE
    }

    const { startDate, endDate } = getStatementDateRange(tranlist);

    const details: OfxFileDetails = {
      institution: signon?.ORG || 'Unknown',
      institutionFid: signon?.FID || null,
      accountType: accountType,
      accountId: acctfrom?.ACCTID || 'Unknown',
      currency: stmt.CURDEF || 'USD',
      transactionCount: Array.isArray(tranlist?.STMTTRN) ? tranlist.STMTTRN.length : (tranlist?.STMTTRN ? 1 : 0),
      startDate: startDate,
      endDate: endDate,
      balance: parseFloat(stmt.LEDGERBAL?.BALAMT || stmt.AVAILBAL?.BALAMT || 0),
    };

    return details;
  }

  // Utility functions
  const clearFile = () => {
    selectedFile.value = null
    fileDetails.value = null
    parseError.value = null
    accountDetected.value = false
    selectedAccount.value = ''
    selectedCurrency.value = ''
    isLearning.value = false
    fieldErrors.value = {};
    if (fileInput.value) {
      fileInput.value.value = ''
    }
    emit('fileCleared')
  }

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

  // Account detection functions
  const detectAccount = async () => {
    if (!fileDetails.value) return

    accountDetected.value = false

    try {
      const requestBody: OFXDetectionRequest = {
        institution: fileDetails.value.institution,
        institution_fid: fileDetails.value.institutionFid,
        account_type: fileDetails.value.accountType,
        account_id: fileDetails.value.accountId,
      }
      const response = await ImportService.detectOfxAccount(requestBody)

      if (response.data?.detected) {
        accountDetected.value = true
        selectedAccount.value = response.data.beancount_account
        selectedCurrency.value = response.data.currency
      } else if (response.data) {
        accountDetected.value = false
        selectedAccount.value = response.data.beancount_account || ''
        selectedCurrency.value = response.data.currency || 'USD'
      }
    } catch (err) {
      handleApiError(err)
      accountDetected.value = false
      console.error('Account detection failed:', err)
    }
  }

  const learnAccount = async () => {
    if (!fileDetails.value) {
      error('No File Data', 'No OFX file data available for learning.')
      return
    }

    isLearning.value = true

    try {
      const requestBody: LearnAccountRequest = {
        institution: fileDetails.value.institution,
        institution_fid: fileDetails.value.institutionFid,
        account_type: fileDetails.value.accountType,
        account_id: fileDetails.value.accountId,
        beancount_account: selectedAccount.value,
        currency: selectedCurrency.value,
      }
      const response = await ImportService.learnOfxAccount(requestBody)

      if (response.data?.mapping_saved) {
        await detectAccount() // This will update the form-level feedback to show success
      } else if (response.data?.account_creation_needed) {
        const shouldCreate = confirm(
          `Account ${selectedAccount.value} doesn't exist. Create it?`
        )
        if (shouldCreate) {
          await createAccount(selectedAccount.value, selectedCurrency.value)
        }
      }
    } catch (err) {
      handleApiError(err)
      console.error('Learn account failed:', err)
    } finally {
      isLearning.value = false
    }
  }

  const createAccount = async (accountName: string, currency: string) => {
    try {
      const requestBody: CreateAccountRequest = {
        account_name: accountName,
        currency: currency,
      }
      const response = await ImportService.createAccount(requestBody)

      if (response.data?.account_created) {
        success('Account Created', `Successfully created ${accountName}.`)
        await learnAccount() // Retry learning after creation
      } else {
        info('Account Exists', `Account ${accountName} already exists.`)
        await learnAccount() // Retry learning since it exists
      }
    } catch (err) {
      handleApiError(err)
      console.error('Create account failed:', err)
    }
  }

  const proceedWithImport = () => {
    // Remove frontend validation - let backend handle it
    if (selectedAccount.value && selectedCurrency.value && selectedFile.value && fileDetails.value) {
      info('Coming Soon', 'Transaction import will be implemented in a future phase.')
      emit('proceedWithImport', {
        file: selectedFile.value,
        details: fileDetails.value,
        account: selectedAccount.value,
        currency: selectedCurrency.value,
      })
    }
  }
</script>
