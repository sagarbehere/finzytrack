<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Import Financial Data</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        Import transactions from OFX files, CSV files, or natural language
      </p>
    </div>

    <!-- Import tabs -->
    <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
      <div class="border-b border-gray-200 dark:border-gray-700">
        <nav class="-mb-px flex space-x-8 px-6" aria-label="Tabs">
          <button
            @click="activeTab = 'ofx'"
            :class="[
              activeTab === 'ofx'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            OFX Import
          </button>
          <button
            @click="activeTab = 'csv'"
            :class="[
              activeTab === 'csv'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            CSV Import
          </button>
          <button
            @click="activeTab = 'natural'"
            :class="[
              activeTab === 'natural'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            Natural Language
          </button>
        </nav>
      </div>

      <div class="p-6">
        <!-- OFX Import Tab -->
        <div v-if="activeTab === 'ofx'">
          <OFXFilePicker
            :key="importerKey"
            @fileCleared="handleFileCleared"
            @proceedWithImport="handleProceedWithImport"
          />
        </div>

        <!-- CSV Import Tab -->
        <div v-else-if="activeTab === 'csv'">
          <CSVFilePicker
            :key="importerKey"
            @fileCleared="handleFileCleared"
            @proceedWithImport="handleCsvProceedWithImport"
          />
        </div>

        <!-- Natural Language Import Tab (placeholder) -->
        <div v-else-if="activeTab === 'natural'" class="text-center py-12">
          <div class="text-gray-400 dark:text-gray-500 text-6xl mb-4">🗣️</div>
          <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-2">
            Natural Language Import
          </h3>
          <p class="text-gray-600 dark:text-gray-400 mb-4">
            Enter transactions using natural language or voice input
          </p>
          <div class="text-sm text-gray-500 dark:text-gray-400">
            <p><strong>Coming Soon:</strong></p>
            <ul class="mt-2 text-left max-w-md mx-auto space-y-1">
              <li>• Voice-to-text transaction entry</li>
              <li>• Natural language parsing ("Coffee $5 yesterday")</li>
              <li>• Multi-transaction batch entry</li>
              <li>• Smart date and amount recognition</li>
            </ul>
          </div>
        </div>
      </div>
    </div>

    <!-- Transaction table for imported transactions (appears below the tabs) -->
    <div v-if="showTransactionTable" class="mt-6">
      <!-- Buttons above the table -->
      <div class="flex justify-between items-center mb-4">
        <button
          @click="resetTable($event)"
          :disabled="isLoading"
          class="px-4 py-2 bg-gray-500 text-white rounded-md hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-gray-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Reset
        </button>
        <button
          @click="autocategorize($event)"
          :disabled="isLoading"
          class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
        >
          <svg v-if="isLoading" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{{ isLoading ? 'Categorizing...' : 'Autocategorize' }}</span>
        </button>
      </div>
      
      <!-- Transaction table component with loading overlay -->
      <div class="relative">
        <!-- Loading overlay -->
        <div v-if="isLoading" class="absolute inset-0 bg-white/50 dark:bg-gray-900/50 z-10 flex items-center justify-center backdrop-blur-sm">
          <div class="bg-white dark:bg-gray-800 rounded-lg shadow-lg p-6 flex items-center gap-3">
            <svg class="animate-spin h-6 w-6 text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-gray-700 dark:text-gray-300 font-medium">Processing transactions...</span>
          </div>
        </div>

        <TransactionTable
          ref="transactionTableRef"
          :transactions="transactionViewModels"
          :import-context="importContext"
          :editable="!isLoading"
          :show-search="false"
          :show-column-filters="false"
          :show-summary="true"
          @transactions-updated="handleTransactionsUpdated"
          @duplicate-click="handleDuplicateIconClick"
        />
      </div>
      
      <!-- Button below the table -->
      <div class="flex justify-end mt-4">
        <button
          @click="registerTransactions"
          :disabled="isLoading"
          class="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
        >
          <svg v-if="isLoading" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{{ isLoading ? 'Registering...' : 'Register Transactions' }}</span>
        </button>
      </div>
    </div>

    <!-- Duplicate Comparison Modal -->
    <DuplicateComparisonModal
      v-if="duplicateTransactionsList.length > 0"
      :is-open="duplicateModalOpen"
      :duplicate-transactions="duplicateTransactionsList"
      :initial-index="duplicateModalInitialIndex"
      @close="handleCloseDuplicateModal"
      @keep-transaction="handleKeepTransaction"
      @remove-duplicate="handleRemoveDuplicate"
    />
  </div>
</template>

<script setup lang="ts">
  import { ref, nextTick } from 'vue'
  import OFXFilePicker from '@/components/import/OFXFilePicker.vue'
  import CSVFilePicker from '@/components/import/CSVFilePicker.vue'
  import TransactionTable from '@/components/common/TransactionTable.vue'
  import DuplicateComparisonModal from '@/components/import/DuplicateComparisonModal.vue'
  import { v7 as uuidv7 } from 'uuid'
  import type { TransactionViewModel, PostingViewModel, ImportContext, TransactionImportBundle } from '@/types/transactions'
  import type { OFXTransaction, OfxFileDetails } from '@/types/ofx'
  import type { CsvParsedTransaction, CsvFileDetails } from '@/types/csv'
  import type { DuplicateInfo } from '@/services/generated-api'
  import { useTransactionImporter } from '@/composables/useTransactionImporter'
  import { useToast } from '@/composables/useNotifications'

  const { performCategorization, performCommit, isLoading, categorizeError, commitError } = useTransactionImporter()
  const { success: showSuccessToast, error: showErrorToast } = useToast()

  // Tab state
  const activeTab = ref<string>('ofx')

  // Importer reset key - increment to reset all importer components
  const importerKey = ref<number>(0)

  // Transaction table state
  const showTransactionTable = ref<boolean>(false)
  const rawTransactions = ref<OFXTransaction[]>([])
  const rawCsvTransactions = ref<CsvParsedTransaction[]>([])
  const importSource = ref<'ofx' | 'csv'>('ofx')
  const transactionViewModels = ref<TransactionViewModel[]>([])
  const importContext = ref<Map<string, ImportContext>>(new Map())
  const sourceAccount = ref<string>('')
  const sourceCurrency = ref<string>('')
  const transactionTableRef = ref<InstanceType<typeof TransactionTable> | null>(null)

  // Duplicate modal state
  const duplicateModalOpen = ref(false)
  const duplicateModalInitialIndex = ref(0)
  const duplicateTransactionsList = ref<Array<{ transaction: TransactionViewModel; duplicateMatches: DuplicateInfo[] }>>([])

  // Event handlers
  const handleFileCleared = () => {
    showTransactionTable.value = false
    transactionViewModels.value = []
    importContext.value.clear()
    rawCsvTransactions.value = []
  }

  // Handle the Proceed button click from OFXFilePicker
  const handleProceedWithImport = (payload: { file: File, details: OfxFileDetails, account: string, currency: string }) => {
    // Set the source account and currency
    sourceAccount.value = payload.account
    sourceCurrency.value = payload.currency
    importSource.value = 'ofx'

    // Convert raw OFX transactions to TransactionViewModel format and create import context
    rawTransactions.value = payload.details.rawTransactions
    const bundle = convertRawTransactionsToViewModels(payload.details.rawTransactions, payload.account, payload.currency)
    transactionViewModels.value = bundle.transactions
    importContext.value = bundle.importContext

    // Show the transaction table
    showTransactionTable.value = true

    // Scroll to the transaction table after it's rendered
    nextTick(() => {
      if (transactionTableRef.value) {
        // Reset table state to ensure clean start, then scroll
        transactionTableRef.value.resetToOriginal()
        transactionTableRef.value.scrollToTable()
      }
    })
  }

  // Convert raw OFX transactions to TransactionViewModel format
  const convertRawTransactionsToViewModels = (rawTransactions: OFXTransaction[], sourceAccount: string, currency: string): TransactionImportBundle => {
    const transactions: TransactionViewModel[] = []
    const importContext = new Map<string, ImportContext>()

    rawTransactions.forEach(tx => {
      // Extract payee and memo from the raw transaction
      const payee = tx.NAME || tx.PAYEE || 'Unknown Payee'
      const memo = tx.MEMO || tx.CHECKNUM || ''
      const amount = parseFloat(tx.TRNAMT || '0') || 0
      const date = tx.DTPOSTED ? new Date(tx.DTPOSTED.substring(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')).toISOString().split('T')[0] : new Date().toISOString().split('T')[0]

      // Create postings - preserve original amount signs from OFX
      const postings: PostingViewModel[] = [
        {
          account: sourceAccount,
          amount: amount, // Preserve the original amount and its sign from OFX file
          currency: currency,
          // NEW fields (undefined for OFX imports)
          cost: undefined,
          price: undefined,
          meta: undefined
        },
        {
          account: 'Expenses:Unknown', // Default category to be updated later
          amount: -amount, // Opposite sign to balance the transaction
          currency: currency,
          // NEW fields (undefined for OFX imports)
          cost: undefined,
          price: undefined,
          meta: undefined
        }
      ]

      const transactionId = uuidv7() // Generate temporary UUIDv7 for frontend preview (backend will replace with its own UUID when committing)

      // Build metadata object conditionally
      const meta: Record<string, string> = {
        source_account: sourceAccount
      }

      // Add ofx_id only if it exists
      const ofxId = tx.TRNTYPE ? `${tx.TRNTYPE}_${tx.FITID || tx.DTPOSTED || ''}` : null
      if (ofxId) {
        meta['ofx_id'] = ofxId
      }

      const transaction: TransactionViewModel = {
        id: transactionId,
        date: date,
        flag: '*',
        payee: payee,
        memo: memo || undefined,
        narration: '',
        tags: [],
        links: [],
        postings: postings,

        // Beancount metadata
        meta: meta,

        // Frontend-only state
        internal: {
          isNew: true,
          isModified: false,
          source_currency: currency
        }
      }
      

      transactions.push(transaction)

      // Create import context for this transaction
      // Initially, no duplicates detected and no confidence score
      importContext.set(transactionId, {
        is_duplicate: false
        // confidence will be set after autocategorization
        // duplicate_info will be set if duplicates are found
      })
    })

    return { transactions, importContext }
  }

  // Handle the Proceed button click from CSVFilePicker
  const handleCsvProceedWithImport = (payload: { file: File, details: CsvFileDetails, account: string, currency: string }) => {
    sourceAccount.value = payload.account
    sourceCurrency.value = payload.currency
    importSource.value = 'csv'

    rawCsvTransactions.value = payload.details.rawTransactions
    const bundle = convertCsvTransactionsToViewModels(payload.details.rawTransactions, payload.account, payload.currency)
    transactionViewModels.value = bundle.transactions
    importContext.value = bundle.importContext

    showTransactionTable.value = true

    nextTick(() => {
      if (transactionTableRef.value) {
        transactionTableRef.value.resetToOriginal()
        transactionTableRef.value.scrollToTable()
      }
    })
  }

  // Convert CSV parsed transactions to TransactionViewModel format
  const convertCsvTransactionsToViewModels = (csvTransactions: CsvParsedTransaction[], sourceAccount: string, currency: string): TransactionImportBundle => {
    const transactions: TransactionViewModel[] = []
    const importContext = new Map<string, ImportContext>()

    csvTransactions.forEach(tx => {
      const payee = tx.payee || 'Unknown Payee'
      const memo = tx.memo || ''
      const amount = tx.amount
      const date = tx.date

      const postings: PostingViewModel[] = [
        {
          account: sourceAccount,
          amount: amount,
          currency: currency,
          cost: undefined,
          price: undefined,
          meta: undefined
        },
        {
          account: 'Expenses:Unknown',
          amount: -amount,
          currency: currency,
          cost: undefined,
          price: undefined,
          meta: undefined
        }
      ]

      const transactionId = uuidv7()

      const meta: Record<string, string> = {
        source_account: sourceAccount
      }

      const transaction: TransactionViewModel = {
        id: transactionId,
        date: date,
        flag: '*',
        payee: payee,
        memo: memo || undefined,
        narration: tx.narration || '',
        tags: [],
        links: [],
        postings: postings,
        meta: meta,
        internal: {
          isNew: true,
          isModified: false,
          source_currency: currency
        }
      }

      transactions.push(transaction)

      importContext.set(transactionId, {
        is_duplicate: false
      })
    })

    return { transactions, importContext }
  }

  // Handle updates to transactions in the table
  const handleTransactionsUpdated = (updatedTransactions: TransactionViewModel[]) => {
    transactionViewModels.value = updatedTransactions

    // Clean up orphaned context entries when transactions are removed
    const validIds = new Set(updatedTransactions.map(t => t.id))
    for (const id of importContext.value.keys()) {
      if (!validIds.has(id)) {
        importContext.value.delete(id)
      }
    }
  }

  // Reset the table to original raw transactions
  const resetTable = (event?: Event) => {
    const bundle = importSource.value === 'csv'
      ? convertCsvTransactionsToViewModels(rawCsvTransactions.value, sourceAccount.value, sourceCurrency.value)
      : convertRawTransactionsToViewModels(rawTransactions.value, sourceAccount.value, sourceCurrency.value)
    transactionViewModels.value = bundle.transactions
    importContext.value = bundle.importContext

    // Reinitialize child table's baselines since we just regenerated all transaction IDs
    nextTick(() => {
      if (transactionTableRef.value) {
        transactionTableRef.value.reinitializeBaselines()
      }
    })

    // Remove focus from the button to hide the focus ring
    if (event?.target) {
      (event.target as HTMLElement).blur()
    }
  }

  // Autocategorize function
  const autocategorize = async (event?: Event) => {
    // Remove focus from the button to hide the focus ring
    if (event?.target) {
      (event.target as HTMLElement).blur()
    }

    if (!transactionViewModels.value.length) {
      return
    }

    try {
      const results = await performCategorization(
        transactionViewModels.value,
        sourceAccount.value,
        sourceCurrency.value
      )

      // Build a map of results by ID for O(1) lookup
      const resultMap = new Map(results.map(r => [r.id, r]))

      // Verify all transactions have corresponding results
      transactionViewModels.value.forEach(tx => {
        if (!resultMap.has(tx.id)) {
          console.error('Missing categorization result for transaction:', tx)
          throw new Error(`No categorization result found for transaction ${tx.id}`)
        }
      })

      // Verify no extra results were returned
      if (results.length !== transactionViewModels.value.length) {
        console.error('Result count mismatch!', {
          sent: transactionViewModels.value.length,
          received: results.length
        })
        throw new Error('Backend returned unexpected number of results')
      }

      // Update transactions with suggested categories and import context using ID-based matching
      transactionViewModels.value = transactionViewModels.value.map(tx => {
        const result = resultMap.get(tx.id)!

        // Update import context
        const existing = importContext.value.get(tx.id)
        if (existing) {
          importContext.value.set(tx.id, {
            ...existing,
            confidence: result.confidence ?? undefined,
            is_duplicate: result.is_duplicate ?? false,
            duplicate_info: result.duplicate_info ?? undefined
          })
        }

        // Update transaction postings with suggested category
        if (result.suggested_category) {
          const updatedPostings = [...tx.postings]
          if (updatedPostings.length >= 2) {
            updatedPostings[1] = {
              ...updatedPostings[1],
              account: result.suggested_category
            }
          }
          return { ...tx, postings: updatedPostings }
        }
        return tx
      })

      // Establish new edit baseline
      nextTick(() => {
        transactionTableRef.value?.setNewEditBaseline()
      })

    } catch (error) {
      if (categorizeError.value) {
        showErrorToast('Categorization Failed', categorizeError.value.message)
      }
    }
  }

  // Register transactions function
  const registerTransactions = async () => {
    if (!transactionViewModels.value.length) {
      return
    }

    try {
      const result = await performCommit(transactionViewModels.value)

      if (result.success) {
        // Clear state
        transactionViewModels.value = []
        importContext.value.clear()
        showTransactionTable.value = false

        // Reset all importer components by incrementing the key
        importerKey.value++

        // Show success message
        showSuccessToast('Transactions Committed', `Successfully committed ${result.count} transactions`)
      }
    } catch (error) {
      if (commitError.value) {
        showErrorToast('Commit Failed', commitError.value.message)
      }
    }
  }

  // Duplicate modal handlers
  const handleDuplicateIconClick = (transactionId: string) => {
    // Build list of all duplicate transactions
    buildDuplicateTransactionsList()

    // Find the index of the clicked transaction
    const clickedIndex = duplicateTransactionsList.value.findIndex(
      item => item.transaction.id === transactionId
    )

    if (clickedIndex >= 0) {
      duplicateModalInitialIndex.value = clickedIndex
      duplicateModalOpen.value = true
    }
  }

  const buildDuplicateTransactionsList = () => {
    duplicateTransactionsList.value = []

    transactionViewModels.value.forEach(transaction => {
      const context = importContext.value.get(transaction.id)
      if (context?.is_duplicate && context.duplicate_info) {
        const duplicateMatches = Array.isArray(context.duplicate_info)
          ? context.duplicate_info
          : [context.duplicate_info]

        duplicateTransactionsList.value.push({
          transaction,
          duplicateMatches
        })
      }
    })
  }

  const handleCloseDuplicateModal = () => {
    duplicateModalOpen.value = false
    // Clear the list when modal closes
    duplicateTransactionsList.value = []
  }

  const handleKeepTransaction = (transactionId: string) => {
    // Transaction stays in table, just rebuild the list
    buildDuplicateTransactionsList()

    // If no more duplicates, close modal
    if (duplicateTransactionsList.value.length === 0) {
      duplicateModalOpen.value = false
    }
  }

  const handleRemoveDuplicate = (transactionId: string) => {
    // Remove transaction from table and context
    transactionViewModels.value = transactionViewModels.value.filter(t => t.id !== transactionId)
    importContext.value.delete(transactionId)

    // Rebuild the duplicates list
    buildDuplicateTransactionsList()

    // If no more duplicates, close modal
    if (duplicateTransactionsList.value.length === 0) {
      duplicateModalOpen.value = false
    }
  }
</script>
