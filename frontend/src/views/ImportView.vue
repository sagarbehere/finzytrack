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
        :editable="true"
        :show-search="false"
        :show-column-filters="false"
        :show-summary="true"
        @transactions-updated="handleTransactionsUpdated"
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
  </div>
</template>

<script setup lang="ts">
  import { ref, nextTick } from 'vue'
  import OFXFilePicker from '@/components/import/OFXFilePicker.vue'
  import TransactionTable from '@/components/common/TransactionTable.vue'
  import { v4 as uuidv4 } from 'uuid'
  import type { TransactionViewModel, PostingViewModel } from '@/types/transactions'
  import type { OFXTransaction, OfxFileDetails } from '@/types/ofx'

  // Tab state
  const activeTab = ref<string>('ofx')

  // Transaction table state
  const showTransactionTable = ref<boolean>(false)
  const rawTransactions = ref<OFXTransaction[]>([])
  const transactionViewModels = ref<TransactionViewModel[]>([])
  const sourceAccount = ref<string>('')
  const sourceCurrency = ref<string>('')
  const transactionTableRef = ref<InstanceType<typeof TransactionTable> | null>(null)

  // Event handlers
  const handleFileCleared = () => {
    showTransactionTable.value = false
    transactionViewModels.value = []
  }

  // Handle the Proceed button click from OFXFilePicker
  const handleProceedWithImport = (payload: { file: File, details: OfxFileDetails, account: string, currency: string }) => {
    // Set the source account and currency
    sourceAccount.value = payload.account
    sourceCurrency.value = payload.currency

    // Convert raw OFX transactions to TransactionViewModel format
    rawTransactions.value = payload.details.rawTransactions
    transactionViewModels.value = convertRawTransactionsToViewModels(payload.details.rawTransactions, payload.account, payload.currency)
    
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
  const convertRawTransactionsToViewModels = (rawTransactions: OFXTransaction[], sourceAccount: string, currency: string): TransactionViewModel[] => {
    return rawTransactions.map(tx => {
      // Extract payee and memo from the raw transaction
      const payee = tx.NAME || tx.PAYEE || 'Unknown Payee'
      const memo = tx.MEMO || tx.CHECKNUM
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

      // Create payee with memo if memo exists
      const fullPayee = memo ? `${payee} | ${memo}` : payee

      return {
        id: uuidv4(), // Generate a unique ID for the transaction
        date: date,
        flag: '*',
        payee: fullPayee,
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
        },
        import_details: {
          is_duplicate: false // No duplicate check yet
          // confidence field omitted - will be added by autocategorization
          // duplicate_info omitted - will be added if duplicates are found
        }
      }
    })
  }

  // Handle updates to transactions in the table
  const handleTransactionsUpdated = (updatedTransactions: TransactionViewModel[]) => {
    transactionViewModels.value = updatedTransactions
  }

  // Reset the table to original raw transactions
  const resetTable = () => {
    transactionViewModels.value = convertRawTransactionsToViewModels(rawTransactions.value, sourceAccount.value, sourceCurrency.value)
  }

  // Autocategorize function (to be implemented later)
  const autocategorize = () => {
    alert('Autocategorization is not yet implemented. This will send transactions to the backend for processing.')
  }

  // Register transactions function (to be implemented later)
  const registerTransactions = () => {
    alert('Register Transactions is not yet implemented. This will send transactions to the backend for committing to the ledger.')
  }
</script>
