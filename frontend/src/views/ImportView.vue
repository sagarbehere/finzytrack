<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Import Financial Data</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        Import transactions from account statements in various configured formats, or use AI for quick import
      </p>
    </div>

    <!-- Import tabs -->
    <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <div class="border-b border-gray-200 dark:border-white/10">
        <nav class="-mb-px flex space-x-8 px-6" aria-label="Tabs">
          <button
            @click="activeTab = 'ofx'"
            :class="[
              activeTab === 'ofx'
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            OFX
          </button>
          <button
            @click="activeTab = 'csv'"
            :class="[
              activeTab === 'csv'
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            CSV
          </button>
          <button
            @click="activeTab = 'xls'"
            :class="[
              activeTab === 'xls'
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            XLS
          </button>
          <button
            @click="activeTab = 'email'"
            :class="[
              activeTab === 'email'
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            Email
          </button>
          <button
            @click="activeTab = 'manual'"
            :class="[
              activeTab === 'manual'
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            Manual
          </button>
          <button
            @click="activeTab = 'ai'"
            :class="[
              activeTab === 'ai'
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            AI
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

        <!-- XLS Import Tab -->
        <div v-else-if="activeTab === 'xls'">
          <XLSFilePicker
            :key="importerKey"
            @fileCleared="handleFileCleared"
            @proceedWithImport="handleCsvProceedWithImport"
          />
        </div>

        <!-- Manual Entry Tab -->
        <div v-else-if="activeTab === 'manual'">
          <ManualEntryPanel @addTransaction="handleManualAddTransaction" />
        </div>

        <!-- Email Import Tab -->
        <div v-else-if="activeTab === 'email'">
          <EmailImportPanel
            :key="importerKey"
            @proceedWithImport="handleEmailProceedWithImport"
          />
        </div>

        <!-- AI Import Tab -->
        <div v-else-if="activeTab === 'ai'">
          <AIImportPanel
            :key="importerKey"
            @fileCleared="handleFileCleared"
            @proceedWithImport="handleCsvProceedWithImport"
          />
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
          class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Reset
        </button>
        <button
          @click="autocategorize($event)"
          :disabled="isLoading"
          class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
        >
          <svg v-if="isLoading" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{{ isLoading ? 'Categorizing...' : 'Autocategorize' }}</span>
        </button>
      </div>

      <!-- Categorization warnings panel -->
      <div v-if="categorizationWarnings.length > 0" class="mb-4 rounded-lg bg-yellow-50 p-4 ring-1 ring-yellow-200 dark:bg-yellow-900/20 dark:ring-yellow-500/30">
        <div class="flex items-start gap-3">
          <div class="shrink-0 text-yellow-600 dark:text-yellow-400">
            <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 6zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" /></svg>
          </div>
          <div class="flex-1">
            <p class="text-sm font-medium text-yellow-800 dark:text-yellow-300">Categorization warnings</p>
            <ul class="mt-1 list-disc list-inside text-sm text-yellow-700 dark:text-yellow-400">
              <li v-for="(warning, idx) in categorizationWarnings" :key="idx">{{ warning }}</li>
            </ul>
          </div>
          <button @click="categorizationWarnings = []" class="shrink-0 text-yellow-600 hover:text-yellow-800 dark:text-yellow-400 dark:hover:text-yellow-300">
            <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" /></svg>
          </button>
        </div>
      </div>

      <!-- Transaction table component with loading overlay -->
      <div class="relative">
        <!-- Loading overlay -->
        <div v-if="isLoading" class="absolute inset-0 bg-white/50 dark:bg-gray-900/50 z-10 flex items-center justify-center backdrop-blur-sm">
          <div class="bg-white dark:bg-gray-800 rounded-lg shadow-lg p-6 flex items-center gap-3">
            <svg class="animate-spin h-6 w-6 text-indigo-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
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
          class="flex items-center gap-2 rounded-md bg-green-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-green-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-green-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-green-500 dark:shadow-none dark:hover:bg-green-400"
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

    <!-- Register Transactions Confirmation Modal -->
    <ConfirmDialog
      :is-open="registerConfirmOpen"
      title="Issues Detected"
      :message="registerConfirmMessage"
      confirm-text="Register Anyway"
      cancel-text="Go Back"
      variant="warning"
      @confirm="doRegisterTransactions"
      @cancel="registerConfirmOpen = false"
      @close="registerConfirmOpen = false"
    />
  </div>
</template>

<script setup lang="ts">
  import { ref, computed, nextTick } from 'vue'
  import OFXFilePicker from '@/components/import/OFXFilePicker.vue'
  import CSVFilePicker from '@/components/import/CSVFilePicker.vue'
  import XLSFilePicker from '@/components/import/XLSFilePicker.vue'
  import ManualEntryPanel from '@/components/import/ManualEntryPanel.vue'
  import EmailImportPanel from '@/components/import/EmailImportPanel.vue'
  import AIImportPanel from '@/components/import/AIImportPanel.vue'
  import type { EmailParsedTransaction } from '@/composables/useEmailImporter'
  import TransactionTable from '@/components/common/TransactionTable.vue'
  import DuplicateComparisonModal from '@/components/import/DuplicateComparisonModal.vue'
  import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
  import { v7 as uuidv7 } from 'uuid'
  import type { TransactionViewModel, PostingViewModel, ImportContext, TransactionImportBundle } from '@/types/transactions'
  import type { OFXTransaction, OfxFileDetails } from '@/types/ofx'
  import type { CsvParsedTransaction, CsvFileDetails } from '@/types/csv'
  import type { DuplicateInfo } from '@/services/generated-api'
  import type { ParsedTransaction } from '@/services/nlParser'
  import { useTransactionImporter } from '@/composables/useTransactionImporter'
  import { useLedgerHealth } from '@/composables/useLedgerHealth'
  import { useToast } from '@/composables/useNotifications'
  import { isTransactionBalanced } from '@/utils/transactions'

  defineOptions({ name: 'ImportView' })

  const { performCategorization, performCommit, isLoading, categorizeError, commitError } = useTransactionImporter()
  const { checkErrors: checkLedgerErrors } = useLedgerHealth()
  const { success: showSuccessToast, error: showErrorToast } = useToast()

  // Tab state
  const activeTab = ref<string>('ofx')

  // Importer reset key - increment to reset all importer components
  const importerKey = ref<number>(0)

  // Transaction table state
  const showTransactionTable = ref<boolean>(false)
  const rawTransactions = ref<OFXTransaction[]>([])
  const rawCsvTransactions = ref<CsvParsedTransaction[]>([])
  const importSource = ref<'ofx' | 'csv' | 'xls' | 'manual' | 'email' | 'ai'>('ofx')
  const transactionViewModels = ref<TransactionViewModel[]>([])
  const importContext = ref<Map<string, ImportContext>>(new Map())
  const sourceAccount = ref<string>('')
  const sourceCurrency = ref<string>('')
  const transactionTableRef = ref<InstanceType<typeof TransactionTable> | null>(null)

  // Duplicate modal state
  const duplicateModalOpen = ref(false)
  const duplicateModalInitialIndex = ref(0)
  const duplicateTransactionsList = ref<Array<{ transaction: TransactionViewModel; duplicateMatches: DuplicateInfo[] }>>([])

  // Register confirmation modal state
  const registerConfirmOpen = ref(false)

  // Categorization warnings state
  const categorizationWarnings = ref<string[]>([])

  const pendingDuplicateCount = computed(() =>
    transactionViewModels.value.filter(t => importContext.value.get(t.id)?.is_duplicate === true).length
  )

  const pendingUnbalancedCount = computed(() =>
    transactionViewModels.value.filter(t => !isTransactionBalanced(t)).length
  )

  const registerConfirmMessage = computed(() => {
    const lines: string[] = ['The following issues were detected:']
    if (pendingDuplicateCount.value > 0) {
      const n = pendingDuplicateCount.value
      lines.push(`• ${n} potential duplicate transaction${n !== 1 ? 's' : ''}`)
    }
    if (pendingUnbalancedCount.value > 0) {
      const n = pendingUnbalancedCount.value
      lines.push(`• ${n} unbalanced transaction${n !== 1 ? 's' : ''}`)
    }
    lines.push('\nDo you want to proceed with registering all transactions anyway?')
    return lines.join('\n')
  })

  // Event handlers
  const handleFileCleared = () => {
    showTransactionTable.value = false
    transactionViewModels.value = []
    importContext.value.clear()
    rawCsvTransactions.value = []
    categorizationWarnings.value = []
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
    transactionViewModels.value = [...transactionViewModels.value, ...bundle.transactions]
    bundle.importContext.forEach((v, k) => importContext.value.set(k, v))

    // Show the transaction table
    showTransactionTable.value = true

    // Scroll to the transaction table after it's rendered
    nextTick(() => {
      if (transactionTableRef.value) {
        // Reinitialize baselines to include newly appended transactions, then scroll
        transactionTableRef.value.reinitializeBaselines()
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

      // Add external_id if FITID is available
      const fitId = tx.FITID || null
      if (fitId) {
        meta['external_id'] = fitId
        meta['external_id_type'] = 'OFX'
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

  // Handle the Proceed button click from CSVFilePicker or XLSFilePicker
  const handleCsvProceedWithImport = (payload: { file: File, details: CsvFileDetails, account: string, currency: string }) => {
    sourceAccount.value = payload.account
    sourceCurrency.value = payload.currency
    importSource.value = activeTab.value === 'xls' ? 'xls' : activeTab.value === 'ai' ? 'ai' : 'csv'

    rawCsvTransactions.value = payload.details.rawTransactions
    const bundle = convertCsvTransactionsToViewModels(payload.details.rawTransactions, payload.account, payload.currency)
    transactionViewModels.value = [...transactionViewModels.value, ...bundle.transactions]
    bundle.importContext.forEach((v, k) => importContext.value.set(k, v))

    showTransactionTable.value = true

    nextTick(() => {
      if (transactionTableRef.value) {
        transactionTableRef.value.reinitializeBaselines()
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

  // Handle the Proceed event from EmailImportPanel
  // beancount_account and currency come from the profile — not from separate dropdowns
  const handleEmailProceedWithImport = (payload: {
    transactions: EmailParsedTransaction[]
    account: string      // profile.beancount_account
    currency: string     // profile.default_currency (possibly overridden by user)
  }) => {
    sourceAccount.value = payload.account
    sourceCurrency.value = payload.currency
    importSource.value = 'email'

    const bundle = convertEmailTransactionsToViewModels(
      payload.transactions, payload.account, payload.currency
    )
    transactionViewModels.value = [...transactionViewModels.value, ...bundle.transactions]
    bundle.importContext.forEach((v, k) => importContext.value.set(k, v))

    showTransactionTable.value = true

    nextTick(() => {
      if (transactionTableRef.value) {
        transactionTableRef.value.reinitializeBaselines()
        transactionTableRef.value.scrollToTable()
      }
    })
  }

  // Convert email parsed transactions to TransactionViewModel format
  const convertEmailTransactionsToViewModels = (
    emailTransactions: EmailParsedTransaction[],
    account: string,
    currency: string
  ): TransactionImportBundle => {
    const transactions: TransactionViewModel[] = []
    const context = new Map<string, ImportContext>()

    emailTransactions.forEach(tx => {
      const transactionId = uuidv7()
      const amount = Number(tx.amount)  // already signed

      const postings: PostingViewModel[] = [
        {
          account,
          amount,
          currency,
          cost: undefined, price: undefined, meta: undefined
        },
        {
          account: 'Expenses:Unknown',
          amount: -amount,
          currency,
          cost: undefined, price: undefined, meta: undefined
        }
      ]

      const meta: Record<string, string> = {
        source_account: account,
        source_rule: tx.source_rule,
      }
      if (tx.external_id) {
        meta['external_id'] = tx.external_id
        meta['external_id_type'] = tx.external_id_type || 'EMAIL_MESSAGE_ID'
      }

      const transaction: TransactionViewModel = {
        id: transactionId,
        date: typeof tx.date === 'string'
          ? tx.date
          : (tx.date as Date).toISOString().split('T')[0],
        flag: '*',
        payee: tx.payee || '',
        narration: '',
        tags: [],
        links: [],
        postings,
        meta,
        internal: { isNew: true, isModified: false, source_currency: currency }
      }

      transactions.push(transaction)
      context.set(transactionId, { is_duplicate: false })
    })

    return { transactions, importContext: context }
  }

  // Handle the "Add Transaction" click from ManualEntryPanel
  const handleManualAddTransaction = (payload: { account: string; currency: string; parsed?: ParsedTransaction; scrollToResult: boolean }) => {
    sourceAccount.value = payload.account || sourceAccount.value
    sourceCurrency.value = payload.currency || sourceCurrency.value
    importSource.value = 'manual'

    const transactionId = uuidv7()
    const today = new Date().toISOString().split('T')[0]
    const parsed = payload.parsed

    const firstAccount = parsed?.postings?.[0]?.account || payload.account
    const firstCurrency = parsed?.postings?.[0]?.currency || payload.currency
    const secondPosting = parsed?.postings?.[1]

    const transaction: TransactionViewModel = {
      id: transactionId,
      date: parsed?.date ?? today,
      flag: (parsed?.flag as '*' | '!') ?? '*',
      payee: parsed?.payee ?? '',
      narration: parsed?.narration ?? '',
      tags: parsed?.tags ?? [],
      links: parsed?.links ?? [],
      postings: [
        {
          account: firstAccount,
          amount: parsed?.postings?.[0]?.amount ?? null,
          currency: firstCurrency,
          cost: undefined,
          price: undefined,
          meta: undefined
        },
        {
          account: secondPosting?.account ?? 'Expenses:Unknown',
          amount: secondPosting?.amount ?? null,
          currency: secondPosting?.currency ?? firstCurrency,
          cost: undefined,
          price: undefined,
          meta: undefined
        }
      ],
      meta: {
        source_account: firstAccount
      },
      internal: {
        isNew: true,
        isModified: false,
        source_currency: firstCurrency
      }
    }

    transactionViewModels.value = [...transactionViewModels.value, transaction]

    importContext.value.set(transactionId, {
      is_duplicate: false
    })

    showTransactionTable.value = true

    if (payload.scrollToResult) {
      nextTick(() => {
        if (transactionTableRef.value) {
          transactionTableRef.value.scrollToTable()
        }
      })
    }
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
    if (importSource.value === 'manual' || importSource.value === 'email') {
      // No raw source data to re-derive from — clear everything
      transactionViewModels.value = []
      importContext.value.clear()
      showTransactionTable.value = false
      if (importSource.value === 'email') importerKey.value++
    } else {
      // Use the table's built-in reset which emits original data through the normal update cycle
      transactionTableRef.value?.resetToOriginal()

      // Reset import context to match the restored original transactions
      const newContext = new Map<string, ImportContext>()
      transactionViewModels.value.forEach(tx => {
        newContext.set(tx.id, { is_duplicate: false })
      })
      importContext.value = newContext
    }

    // Clear categorization state
    categorizationWarnings.value = []

    // Remove focus from the button to hide the focus ring
    if (event?.target) {
      (event.target as HTMLElement).blur()
    }
  }

  // Shared function to apply categorization results to the transaction table
  const applyCategorizationResults = (results: import('@/services/generated-api').CategorizedTransactionResult[]) => {
    const resultMap = new Map(results.map(r => [r.id, r]))

    // Verify all transactions have corresponding results
    transactionViewModels.value.forEach(tx => {
      if (!resultMap.has(tx.id)) {
        console.error('Missing categorization result for transaction:', tx)
        throw new Error(`No categorization result found for transaction ${tx.id}`)
      }
    })

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

    // Clear previous warnings and fallback prompt
    categorizationWarnings.value = []

    try {
      const { results, stats } = await performCategorization(
        transactionViewModels.value,
        sourceAccount.value,
        sourceCurrency.value
      )

      // Show warnings if any
      if (stats.warnings && stats.warnings.length > 0) {
        categorizationWarnings.value = stats.warnings
      }

      applyCategorizationResults(results)

      // Show timing summary
      const engine = stats.engine_used === 'ai' ? 'AI' : stats.engine_used === 'classifier' ? 'classifier' : 'default'
      showSuccessToast('Autocategorize complete', `${stats.total_count} transactions in ${stats.duration_secs}s (${engine})`)

    } catch (_error) {
      // Error already displayed via errorHandler.display() in composable
    }
  }

  // Register transactions function
  const registerTransactions = () => {
    if (!transactionViewModels.value.length) return

    if (pendingDuplicateCount.value > 0 || pendingUnbalancedCount.value > 0) {
      registerConfirmOpen.value = true
      return
    }

    doRegisterTransactions()
  }

  const doRegisterTransactions = async () => {
    registerConfirmOpen.value = false

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

        // Check for ledger errors introduced by the import
        checkLedgerErrors()
      }
    } catch (_error) {
      // Error already displayed via errorHandler.display() in composable
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

  const handleKeepTransaction = (_transactionId: string) => {
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
