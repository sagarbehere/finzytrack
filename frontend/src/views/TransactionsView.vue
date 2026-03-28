<template>
  <div class="transactions-view">
    <!-- Header -->
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Transactions</h1>
      <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
        Query and edit transactions from your ledger
      </p>
    </div>

    <!-- Transaction Filter UI -->
    <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 p-6 mb-6 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <TransactionFilterPanel
        :loading="isQuerying"
        :initial-filters="initialFilters"
        @filter-changed="handleFilterChanged"
      />
    </div>

    <!-- Loading State -->
    <div v-if="isQuerying" class="flex justify-center items-center py-12">
      <div class="animate-spin rounded-full h-8 w-8 border-2 border-gray-300 border-t-indigo-600 dark:border-white/10 dark:border-t-indigo-400"></div>
    </div>

    <!-- Transaction Table -->
    <div ref="transactionTableContainer" v-if="showTable && transactions.length > 0" class="scroll-mt-32">
      <!-- Warning if limit is reached -->
      <div
        v-if="totalCount !== null && transactions.length < totalCount"
        class="mb-4 rounded-md bg-yellow-50 p-4 dark:bg-yellow-500/10 dark:outline dark:outline-yellow-500/15"
      >
        <div class="flex items-start">
          <svg class="w-5 h-5 text-yellow-600 dark:text-yellow-400 mt-0.5 mr-3" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
          </svg>
          <div>
            <h3 class="text-sm font-medium text-yellow-800 dark:text-yellow-300">
              Showing {{ transactions.length.toLocaleString() }} of {{ totalCount.toLocaleString() }} transactions
            </h3>
            <p class="mt-1 text-sm text-yellow-700 dark:text-yellow-400">
              Increase Max Results or refine filters to see more.
            </p>
          </div>
        </div>
      </div>

      <TransactionTable
        ref="transactionTableRef"
        :transactions="transactions"
        :editable="!isSaving"
        :show-search="true"
        :show-column-filters="false"
        :show-summary="true"
        :column-control-align="'right'"
        @transactions-updated="handleTransactionsUpdated"
        @transaction-deleted="handleTransactionDeleted"
      />

      <!-- Action Buttons -->
      <div class="flex justify-between items-center mt-6 px-4">
        <button
          @click="handleReset"
          :disabled="isSaving || !hasModifications"
          class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        >
          Reset
        </button>

        <button
          @click="handleSaveChanges"
          :disabled="isSaving || !hasModifications"
          class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
        >
          {{ isSaving ? 'Saving...' : `Save Changes (${modifiedCount})` }}
        </button>
      </div>
    </div>

    <!-- Empty State -->
    <div v-else class="text-center py-12 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <svg class="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      <h3 class="mt-2 text-sm font-medium text-gray-900 dark:text-white">No transactions found</h3>
      <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
        Try adjusting your filters or select a different date range.
      </p>
    </div>

    <!-- Unsaved changes confirmation dialog -->
    <ConfirmDialog
      :is-open="unsavedConfirm.isOpen.value"
      :title="unsavedConfirm.dialogOptions.value.title"
      :message="unsavedConfirm.dialogOptions.value.message"
      :confirm-text="unsavedConfirm.dialogOptions.value.confirmText"
      :cancel-text="unsavedConfirm.dialogOptions.value.cancelText"
      :variant="unsavedConfirm.dialogOptions.value.variant"
      @confirm="unsavedConfirm.handleConfirm"
      @cancel="unsavedConfirm.handleCancel"
      @close="unsavedConfirm.handleClose"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, onBeforeRouteLeave } from 'vue-router'
import TransactionTable from '@/components/common/TransactionTable.vue'
import TransactionFilterPanel from '@/components/transactions/TransactionFilterPanel.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import type { TransactionViewModel } from '@/types/transactions'
import type { TransactionFilters } from '@/types/filters'
import { useTransactionQuery } from '@/composables/useTransactionQuery'
import { useTransactionUpdater } from '@/composables/useTransactionUpdater'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useLedgerHealth } from '@/composables/useLedgerHealth'
import { useToast } from '@/composables/useNotifications'

const route = useRoute()

// Refs
const transactionTableRef = ref<InstanceType<typeof TransactionTable> | null>(null)
const transactionTableContainer = ref<HTMLDivElement | null>(null)
const transactions = ref<TransactionViewModel[]>([])
const totalCount = ref<number | null>(null)
const isQuerying = ref(false)
const isSaving = ref(false)
const showTable = ref(false)
const currentFilters = ref<TransactionFilters | null>(null)
const currentLimit = ref<number>(1000)

// Parse URL query parameters into initial filters
const initialFilters = computed<TransactionFilters | undefined>(() => {
  const query = route.query
  if (Object.keys(query).length === 0) return undefined

  const filters: TransactionFilters = {}

  // String filters
  if (query.dateFrom) filters.dateFrom = String(query.dateFrom)
  if (query.dateTo) filters.dateTo = String(query.dateTo)
  if (query.search) filters.search = String(query.search)
  if (query.payeeContains) filters.payeeContains = String(query.payeeContains)
  if (query.narrationContains) filters.narrationContains = String(query.narrationContains)
  if (query.accountContains) filters.accountContains = String(query.accountContains)
  if (query.tagsContain) filters.tagsContain = String(query.tagsContain)
  if (query.linksContain) filters.linksContain = String(query.linksContain)
  if (query.currency) filters.currency = String(query.currency)
  if (query.flag) filters.flag = String(query.flag)
  if (query.accountType) filters.accountType = String(query.accountType)

  // Numeric filters
  if (query.amountGreaterThan) filters.amountGreaterThan = Number(query.amountGreaterThan)
  if (query.amountLessThan) filters.amountLessThan = Number(query.amountLessThan)
  if (query.year) filters.year = Number(query.year)
  if (query.quarter) filters.quarter = Number(query.quarter)

  return Object.keys(filters).length > 0 ? filters : undefined
})

// Composables
const { queryTransactions } = useTransactionQuery()
const { updateTransactions } = useTransactionUpdater()
const unsavedConfirm = useConfirmDialog()
const { checkErrors: checkLedgerErrors } = useLedgerHealth()
const toast = useToast()

// Computed
const hasModifications = computed(() => {
  return transactions.value.some(t => t.internal.isModified)
})

const modifiedCount = computed(() => {
  return transactions.value.filter(t => t.internal.isModified).length
})

// Handlers
async function handleFilterChanged(filters: TransactionFilters, limit: number) {
  isQuerying.value = true
  currentFilters.value = filters
  currentLimit.value = limit

  try {
    const result = await queryTransactions(filters, limit)
    transactions.value = result.transactions
    totalCount.value = result.totalCount

    // Reset modification flags on fresh query
    transactions.value.forEach(t => {
      t.internal.isModified = false
    })

    // Reinitialize baselines so edit detection works with the new transactions
    await nextTick()
    transactionTableRef.value?.reinitializeBaselines()

    // Show table and scroll to it
    if (transactions.value.length > 0) {
      showTable.value = true

      nextTick(() => {
        if (transactionTableContainer.value) {
          transactionTableContainer.value.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      })
    }
  } catch (error: any) {
    console.error('Failed to query transactions:', error)
    toast.error(
      'Query Failed',
      error.message || 'Failed to query transactions. Please try again.'
    )
  } finally {
    isQuerying.value = false
  }
}

function handleTransactionsUpdated(updatedTransactions: TransactionViewModel[]) {
  transactions.value = updatedTransactions
}

function handleTransactionDeleted(_transactionId: string) {
  // Decrement totalCount when a transaction is deleted
  if (totalCount.value !== null && totalCount.value > 0) {
    totalCount.value--
  }
}

async function handleReset() {
  if (!currentFilters.value) return

  // Re-query using current filters to reload from database
  await handleFilterChanged(currentFilters.value, currentLimit.value)
}

async function handleSaveChanges() {
  isSaving.value = true

  try {
    const modifiedTransactions = transactions.value.filter(t => t.internal.isModified)

    if (modifiedTransactions.length === 0) {
      toast.warning('No Changes', 'No modified transactions to save.')
      return
    }

    // Call update API
    const result = await updateTransactions(modifiedTransactions)

    if (result.success) {
      // Mark all as saved (reset isModified flags)
      transactions.value.forEach(t => {
        t.internal.isModified = false
      })

      // Update baseline in table
      if (transactionTableRef.value && typeof transactionTableRef.value.setNewEditBaseline === 'function') {
        transactionTableRef.value.setNewEditBaseline()
      }

      // Show success notification
      toast.success(
        'Changes Saved',
        `Successfully updated ${result.updated_count} transaction${result.updated_count > 1 ? 's' : ''}.`
      )

      // Check for ledger errors introduced by the edit
      checkLedgerErrors()
    }
  } catch (error: any) {
    console.error('Failed to save changes:', error)
    toast.error(
      'Save Failed',
      error.message || 'Failed to save changes. Please try again.'
    )
  } finally {
    isSaving.value = false
  }
}

// Warn before navigating away with unsaved edits (styled dialog)
onBeforeRouteLeave(async () => {
  if (hasModifications.value) {
    const confirmed = await unsavedConfirm.showConfirm({
      title: 'Unsaved Changes',
      message: `You have ${modifiedCount.value} unsaved edit${modifiedCount.value > 1 ? 's' : ''} that will be lost if you leave this page.`,
      confirmText: 'Leave',
      cancelText: 'Stay',
      variant: 'warning'
    })
    return confirmed
  }
})

// Warn before browser refresh/close with unsaved edits (native dialog — browser limitation)
function beforeUnloadHandler(e: BeforeUnloadEvent) {
  if (hasModifications.value) {
    e.preventDefault()
  }
}

onMounted(() => {
  window.addEventListener('beforeunload', beforeUnloadHandler)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnloadHandler)
})

// Initialize on mount - filter panel handles the initial query
// If URL has query params, those will be used; otherwise filter panel uses defaults
onMounted(() => {
  // Only run default query if no URL query params provided
  // (filter panel will handle its own initialization when initialFilters is provided)
  if (!initialFilters.value) {
    const defaultFilters: TransactionFilters = {
      dateFrom: getDate90DaysAgo(),
      dateTo: getTodayDate(),
    }
    handleFilterChanged(defaultFilters, 1000)
  }
})

// Helper functions
function getDate90DaysAgo(): string {
  const date = new Date()
  date.setDate(date.getDate() - 90)
  return date.toISOString().split('T')[0]
}

function getTodayDate(): string {
  return new Date().toISOString().split('T')[0]
}
</script>
