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

        <!-- CSV Import Tab (placeholder) -->
        <div v-else-if="activeTab === 'csv'" class="text-center py-12">
          <div class="text-gray-400 dark:text-gray-500 text-6xl mb-4">📊</div>
          <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-2">CSV Import</h3>
          <p class="text-gray-600 dark:text-gray-400 mb-4">
            Import transactions from CSV files with flexible column mapping
          </p>
          <div class="text-sm text-gray-500 dark:text-gray-400">
            <p><strong>Coming Soon:</strong></p>
            <ul class="mt-2 text-left max-w-md mx-auto space-y-1">
              <li>• Auto-detect CSV format and encoding</li>
              <li>• Visual column mapping interface</li>
              <li>• Save mapping templates for repeated imports</li>
              <li>• Support for custom date formats</li>
            </ul>
          </div>
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

    <!-- Transaction table for raw OFX transactions (appears below the tabs) -->
    <div v-if="showTransactionTable" class="mt-6">
      <!-- Buttons above the table -->
      <div class="flex justify-between items-center mb-4">
        <button
          @click="resetTable"
          class="px-4 py-2 bg-gray-500 text-white rounded-md hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-gray-500 focus:ring-offset-2"
        >
          Reset
        </button>
        <button
          @click="autocategorize"
          class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          Autocategorize
        </button>
      </div>
      
      <!-- Transaction table component -->
      <TransactionTable
        ref="transactionTableRef"
        :transactions="transactionViewModels"
        :import-context="importContext"
        :editable="true"
        :show-search="false"
        :show-column-filters="false"
        :show-summary="true"
        @transactions-updated="handleTransactionsUpdated"
        @duplicate-click="handleDuplicateIconClick"
      />
      
      <!-- Button below the table -->
      <div class="flex justify-end mt-4">
        <button
          @click="registerTransactions"
          class="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2"
        >
          Register Transactions
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
  import TransactionTable from '@/components/common/TransactionTable.vue'
  import DuplicateComparisonModal from '@/components/import/DuplicateComparisonModal.vue'
  import { v4 as uuidv4 } from 'uuid'
  import type { TransactionViewModel, PostingViewModel, ImportContext, TransactionImportBundle } from '@/types/transactions'
  import type { OFXTransaction, OfxFileDetails } from '@/types/ofx'
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
  }

  // Handle the Proceed button click from OFXFilePicker
  const handleProceedWithImport = (payload: { file: File, details: OfxFileDetails, account: string, currency: string }) => {
    // Set the source account and currency
    sourceAccount.value = payload.account
    sourceCurrency.value = payload.currency

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
          currency: currency
        },
        {
          account: 'Expenses:Unknown', // Default category to be updated later
          amount: -amount, // Opposite sign to balance the transaction
          currency: currency
        }
      ]

      const transactionId = uuidv4() // Generate a unique ID for the transaction

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
        meta: {
          ofx_id: tx.TRNTYPE ? `${tx.TRNTYPE}_${tx.FITID || tx.DTPOSTED || ''}` : undefined,
          isNew: true,
          isModified: false,
          source_account: sourceAccount,
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
  const resetTable = () => {
    const bundle = convertRawTransactionsToViewModels(rawTransactions.value, sourceAccount.value, sourceCurrency.value)
    transactionViewModels.value = bundle.transactions
    importContext.value = bundle.importContext
  }

  // Autocategorize function
  const autocategorize = async () => {
    if (!transactionViewModels.value.length) {
      return
    }

    try {
      const results = await performCategorization(
        transactionViewModels.value,
        sourceAccount.value,
        sourceCurrency.value
      )

      // Verify ordering matches (safety check)
      results.forEach((result, index) => {
        const transaction = transactionViewModels.value[index]
        if (result.date !== transaction.date || parseFloat(result.amount.toString()) !== transaction.postings[0]?.amount) {
          console.error('Response ordering mismatch!', { result, transaction })
          throw new Error('Backend returned results in unexpected order')
        }
      })

      // Update transactions with suggested categories and import context
      transactionViewModels.value = transactionViewModels.value.map((tx, index) => {
        const result = results[index]

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
