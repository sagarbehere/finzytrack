<template>
  <div class="w-full">
    <!-- Rule selector -->
    <div class="mb-4">
      <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
        CSV Rule
      </label>
      <div v-if="isLoadingRules" class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
        <div class="animate-spin h-4 w-4 border-2 border-indigo-500 border-t-transparent rounded-full"></div>
        Loading rules...
      </div>
      <div v-else-if="rulesLoadError" class="text-sm text-red-600 dark:text-red-400">
        {{ rulesLoadError }}
      </div>
      <div v-else-if="availableRules.length === 0" class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700 rounded-lg p-4 text-sm text-yellow-800 dark:text-yellow-200 flex items-center justify-between gap-4">
        <span>No CSV rules found. <a href="https://finzytrack.app/docs/csv-rules" target="_blank" rel="noopener noreferrer" class="underline underline-offset-2 hover:text-yellow-900 dark:hover:text-yellow-100">Learn how to create CSV rule files</a>.</span>
        <div class="flex items-center gap-2">
          <button
            @click="handleReloadRules"
            :disabled="isReloadingRules"
            class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
            title="Reload rules from disk"
          >
            {{ isReloadingRules ? 'Reloading…' : 'Reload' }}
          </button>
          <button
            @click="router.push({ path: '/settings', query: { tab: 'rules', type: 'csv' } })"
            class="rounded-md bg-white p-1.5 text-gray-500 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 hover:text-gray-700 shrink-0 dark:bg-white/10 dark:text-gray-400 dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 dark:hover:text-gray-200"
            title="Manage CSV rules"
          >
            <Cog6ToothIcon class="h-5 w-5" />
          </button>
        </div>
      </div>
      <div v-else class="flex gap-2">
        <Listbox as="div" v-model="selectedRuleFilename" class="flex-1">
          <ListboxLabel class="sr-only">CSV Rule</ListboxLabel>
          <div class="relative">
            <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
              <span class="col-start-1 row-start-1 truncate pr-6">{{ csvRuleOptions.find(o => o.value === selectedRuleFilename)?.label || 'Select a rule...' }}</span>
              <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
            </ListboxButton>
            <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
              <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
                <ListboxOption v-for="opt in csvRuleOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
                  <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                    <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ opt.label }}</span>
                    <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                      <CheckIcon class="size-5" aria-hidden="true" />
                    </span>
                  </li>
                </ListboxOption>
              </ListboxOptions>
            </transition>
          </div>
        </Listbox>
        <button
          @click="handleReloadRules"
          :disabled="isReloadingRules"
          class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
          title="Reload rules from disk"
        >
          {{ isReloadingRules ? 'Reloading…' : 'Reload' }}
        </button>
        <button
          @click="router.push({ path: '/settings', query: { tab: 'rules', type: 'csv' } })"
          class="rounded-md bg-white p-1.5 text-gray-500 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 hover:text-gray-700 shrink-0 dark:bg-white/10 dark:text-gray-400 dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 dark:hover:text-gray-200"
          title="Manage CSV rules"
        >
          <Cog6ToothIcon class="h-5 w-5" />
        </button>
      </div>
    </div>

    <!-- Invalid rules banner -->
    <div
      v-if="invalidRules.length > 0"
      class="mb-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700 rounded-lg p-4"
    >
      <div class="flex items-start gap-2">
        <ExclamationTriangleIcon class="h-5 w-5 text-yellow-600 dark:text-yellow-400 shrink-0 mt-0.5" />
        <div class="text-sm text-yellow-800 dark:text-yellow-200">
          <p class="font-medium mb-1">
            {{ invalidRules.length === 1 ? '1 rule file could not be loaded:' : `${invalidRules.length} rule files could not be loaded:` }}
          </p>
          <ul class="space-y-1">
            <li v-for="rule in invalidRules" :key="rule.filename">
              <span class="font-mono font-medium">{{ rule.filename }}</span>
              <span class="text-yellow-700 dark:text-yellow-300"> — {{ rule.error }}</span>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <!-- File Upload Widget (only shown when a rule is selected) -->
    <div
      v-if="selectedRule"
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
      <input
        ref="fileInput"
        type="file"
        accept=".csv,.CSV"
        class="hidden"
        @change="handleFileSelect"
        :disabled="isParsing"
      />

      <!-- Upload icon -->
      <div v-if="!selectedFile">
        <DocumentArrowUpIcon class="mx-auto h-12 w-12 text-gray-400" />
        <div class="mt-4">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white">Upload CSV File</h3>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            Drop your CSV file here or
            <button
              @click="openFilePicker"
              :disabled="isParsing"
              class="text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300 font-medium underline"
            >
              browse files
            </button>
          </p>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Using rule: {{ selectedRule.name }}
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
        v-if="isParsing"
        class="absolute inset-0 flex items-center justify-center bg-white/75 dark:bg-gray-900/75 rounded-lg"
      >
        <div class="flex items-center space-x-2">
          <div class="animate-spin h-5 w-5 border-2 border-indigo-500 border-t-transparent rounded-full"></div>
          <span class="text-sm font-medium text-gray-900 dark:text-white">Parsing CSV file...</span>
        </div>
      </div>
    </div>

    <!-- File Details -->
    <div v-if="selectedFile" class="mt-6">
      <FormFeedback
        v-if="parseError"
        :message="parseError"
        severity="error"
        details="Please ensure you selected a valid CSV file and the correct rule."
        class="mb-4"
      />

      <div v-else-if="fileDetails">
        <!-- File Summary -->
        <div class="bg-gray-50 dark:bg-gray-800 rounded-lg p-4 mb-4">
          <h3 class="text-lg font-semibold text-gray-900 dark:text-white mb-3">File Details</h3>
          <div class="flex flex-wrap gap-6 text-sm">
            <div>
              <span class="text-gray-500 dark:text-gray-400">Transactions:</span>
              <span class="ml-1 font-medium text-gray-900 dark:text-white">{{ fileDetails.transactionCount }}</span>
            </div>
            <div>
              <span class="text-gray-500 dark:text-gray-400">Date Range:</span>
              <span class="ml-1 font-medium text-gray-900 dark:text-white">
                {{ formatDateRange(fileDetails.startDate, fileDetails.endDate) }}
              </span>
            </div>
            <div>
              <span class="text-gray-500 dark:text-gray-400">Rule:</span>
              <span class="ml-1 font-medium text-gray-900 dark:text-white">{{ fileDetails.ruleName }}</span>
            </div>
          </div>
        </div>

        <!-- Preview table -->
        <div class="bg-white dark:bg-gray-800 rounded-lg border dark:border-white/10 mb-4 overflow-x-auto">
          <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Date</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Payee</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Narration</th>
                <th class="px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Amount</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Memo</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
              <tr v-for="(tx, idx) in previewTransactions" :key="idx" class="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td class="px-4 py-2 whitespace-nowrap text-gray-900 dark:text-white">{{ tx.date }}</td>
                <td class="px-4 py-2 text-gray-900 dark:text-white">{{ tx.payee || '-' }}</td>
                <td class="px-4 py-2 text-gray-600 dark:text-gray-400">{{ tx.narration || '-' }}</td>
                <td class="px-4 py-2 text-right whitespace-nowrap" :class="tx.amount < 0 ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'">
                  {{ tx.amount.toFixed(2) }}
                </td>
                <td class="px-4 py-2 text-gray-500 dark:text-gray-400">{{ tx.memo || '-' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="fileDetails.transactionCount > 5" class="px-4 py-2 text-xs text-gray-500 dark:text-gray-400 border-t dark:border-white/10">
            Showing first 5 of {{ fileDetails.transactionCount }} transactions
          </div>
        </div>

        <!-- Account and Currency -->
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
              <p v-if="accountNotInLedger" class="mt-1 text-xs text-amber-600 dark:text-amber-400">
                Account not found in ledger. Create it first or select a different account.
              </p>
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
              @click="proceedWithImport"
              :disabled="!selectedAccount || !selectedCurrency"
              class="rounded-md bg-green-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-green-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-green-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-green-500 dark:shadow-none dark:hover:bg-green-400"
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
  import { ref, computed, watch, onMounted } from 'vue'
  import { Listbox, ListboxButton, ListboxLabel, ListboxOption, ListboxOptions } from '@headlessui/vue'
  import { useRouter } from 'vue-router'
  import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
  import { CheckIcon } from '@heroicons/vue/20/solid'
  import { Cog6ToothIcon } from '@heroicons/vue/24/outline'

  const router = useRouter()
  import { useAccounts } from '@/composables/useAccounts'
  import { useToast } from '@/composables/useNotifications'
  import {
    DocumentArrowUpIcon,
    DocumentCheckIcon,
    ExclamationTriangleIcon,
    XMarkIcon,
  } from '@heroicons/vue/24/outline'
  import FormFeedback from '@/components/common/FormFeedback.vue'
  import AccountDropdown from '@/components/common/AccountDropdown.vue'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import { useCsvParser } from '@/composables/useCsvParser'
  import { ImportService } from '@/services/generated-api'
  import type { CsvRule, CsvRuleSummary, InvalidRuleSummary } from '@/services/generated-api'
  import type { CsvFileDetails } from '@/types/csv'

  const emit = defineEmits<{
    (e: 'proceedWithImport', payload: {
      file: File;
      details: CsvFileDetails;
      account: string;
      currency: string;
    }): void,
    (e: 'fileCleared'): void
  }>()

  const {
    selectedFile,
    fileDetails,
    parseError,
    isParsing,
    processFile,
    clearFile,
  } = useCsvParser()

  // Rule state
  const availableRules = ref<CsvRuleSummary[]>([])
  const invalidRules = ref<InvalidRuleSummary[]>([])
  const selectedRuleFilename = ref<string>('')

  const csvRuleOptions = computed(() => [
    { value: '', label: 'Select a rule...' },
    ...availableRules.value.map(rule => ({
      value: rule.filename,
      label: `${rule.name} (${rule.default_account})`,
    })),
  ])
  const selectedRule = ref<CsvRule | null>(null)
  const isLoadingRules = ref<boolean>(false)
  const isReloadingRules = ref<boolean>(false)
  const rulesLoadError = ref<string | null>(null)
  const toast = useToast()

  const { accountDetails, hasBeenFetched } = useAccounts()

  // Account/currency state
  const selectedAccount = ref<string>('')
  const selectedCurrency = ref<string>('')

  const accountNotInLedger = computed(() => {
    if (!selectedAccount.value || !hasBeenFetched.value) return false
    const openAccounts = accountDetails.value.filter(a => !a.close_date).map(a => a.name)
    return !openAccounts.includes(selectedAccount.value)
  })

  // UI state
  const fileInput = ref<HTMLInputElement | null>(null)
  const isDragOver = ref<boolean>(false)

  const previewTransactions = computed(() => {
    if (!fileDetails.value) return []
    return fileDetails.value.rawTransactions.slice(0, 5)
  })

  // Load rules on mount
  onMounted(async () => {
    isLoadingRules.value = true
    try {
      const response = await ImportService.listCsvRules()
      if (response.success && response.data) {
        availableRules.value = response.data.rules || []
        invalidRules.value = response.data.invalid_rules || []
      }
    } catch (err: any) {
      rulesLoadError.value = 'Failed to load CSV rules.'
      console.error('Failed to load CSV rules:', err)
    } finally {
      isLoadingRules.value = false
    }
  })

  watch(selectedRuleFilename, () => handleRuleChange())

  const handleRuleChange = async () => {
    if (!selectedRuleFilename.value) {
      selectedRule.value = null
      return
    }

    try {
      const response = await ImportService.getCsvRule(selectedRuleFilename.value)
      if (response.success && response.data) {
        selectedRule.value = response.data
        selectedAccount.value = response.data.default_account
        selectedCurrency.value = response.data.default_currency || 'USD'

        // Re-parse if file already selected
        if (selectedFile.value) {
          processFile(selectedFile.value, response.data)
        }
      }
    } catch (err: any) {
      console.error('Failed to load CSV rule:', err)
      parseError.value = 'Failed to load the selected rule.'
    }
  }

  const handleReloadRules = async () => {
    isReloadingRules.value = true
    try {
      const response = await ImportService.listCsvRules()
      if (response.success && response.data) {
        availableRules.value = response.data.rules || []
        invalidRules.value = response.data.invalid_rules || []

        // Re-fetch the selected rule if it still exists, then re-parse if file is loaded
        if (selectedRuleFilename.value) {
          const stillExists = availableRules.value.some(r => r.filename === selectedRuleFilename.value)
          if (stillExists) {
            const ruleResponse = await ImportService.getCsvRule(selectedRuleFilename.value)
            if (ruleResponse.success && ruleResponse.data) {
              selectedRule.value = ruleResponse.data
              selectedAccount.value = ruleResponse.data.default_account
              selectedCurrency.value = ruleResponse.data.default_currency || 'USD'
              if (selectedFile.value) {
                processFile(selectedFile.value, ruleResponse.data)
              }
            }
          } else {
            selectedRuleFilename.value = ''
            selectedRule.value = null
          }
        }

        toast.success('Rules reloaded', `${availableRules.value.length} rule${availableRules.value.length === 1 ? '' : 's'} loaded.`)
      }
    } catch {
      toast.error('Reload failed', 'Could not reload CSV rules.')
    } finally {
      isReloadingRules.value = false
    }
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    isDragOver.value = false
    const files = e.dataTransfer?.files
    if (files && files.length > 0 && selectedRule.value) {
      processFile(files[0], selectedRule.value)
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
    if (files && files.length > 0 && selectedRule.value) {
      processFile(files[0], selectedRule.value)
    }
  }

  const handleClearFile = () => {
    clearFile()
    if (fileInput.value) fileInput.value.value = ''
    emit('fileCleared')
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
</script>
