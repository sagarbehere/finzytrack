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
    <div class="bg-white dark:bg-gray-800 shadow rounded-lg border border-gray-200 dark:border-gray-700 p-6 mb-6">
      <TransactionFilterPanel
        :loading="isQuerying"
        @filter-changed="handleFilterChanged"
      />
    </div>

    <!-- Loading State -->
    <div v-if="isQuerying" class="flex justify-center items-center py-12">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
    </div>

    <!-- Transaction Table -->
    <div v-else-if="transactions.length > 0">
      <TransactionTable
        ref="transactionTableRef"
        :transactions="transactions"
        :editable="!isSaving"
        :show-search="true"
        :show-column-filters="false"
        :show-summary="true"
        @transactions-updated="handleTransactionsUpdated"
      />

      <!-- Action Buttons -->
      <div class="flex justify-between items-center mt-6 px-4">
        <button
          @click="handleReset"
          :disabled="isSaving || !hasModifications"
          class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-gray-700 dark:text-gray-200 dark:border-gray-600 dark:hover:bg-gray-600"
        >
          Reset
        </button>

        <button
          @click="handleSaveChanges"
          :disabled="isSaving || !hasModifications"
          class="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ isSaving ? 'Saving...' : `Save Changes (${modifiedCount})` }}
        </button>
      </div>
    </div>

    <!-- Empty State -->
    <div v-else class="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
      <svg class="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      <h3 class="mt-2 text-sm font-medium text-gray-900 dark:text-white">No transactions found</h3>
      <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
        Try adjusting your filters or select a different date range.
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import TransactionTable from '@/components/common/TransactionTable.vue'
import TransactionFilterPanel from '@/components/transactions/TransactionFilterPanel.vue'
import type { TransactionViewModel } from '@/types/transactions'
import type { TransactionFilters } from '@/types/filters'
import { useTransactionQuery } from '@/composables/useTransactionQuery'
import { useTransactionUpdater } from '@/composables/useTransactionUpdater'
import { useToast } from '@/composables/useNotifications'

// Refs
const transactionTableRef = ref<InstanceType<typeof TransactionTable> | null>(null)
const transactions = ref<TransactionViewModel[]>([])
const isQuerying = ref(false)
const isSaving = ref(false)
const currentFilters = ref<TransactionFilters | null>(null)
const currentDbType = ref<'duckdb' | 'sqlite'>('sqlite')

// Composables
const { queryTransactions } = useTransactionQuery()
const { updateTransactions } = useTransactionUpdater()
const toast = useToast()

// Computed
const hasModifications = computed(() => {
  return transactions.value.some(t => t.internal.isModified)
})

const modifiedCount = computed(() => {
  return transactions.value.filter(t => t.internal.isModified).length
})

// Handlers
async function handleFilterChanged(filters: TransactionFilters, dbType: 'duckdb' | 'sqlite') {
  isQuerying.value = true
  currentFilters.value = filters
  currentDbType.value = dbType

  try {
    // Query transactions based on filters
    transactions.value = await queryTransactions(filters, dbType)

    // Reset modification flags on fresh query
    transactions.value.forEach(t => {
      t.internal.isModified = false
    })
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

async function handleReset() {
  if (!currentFilters.value) return

  // Re-query using current filters to reload from database
  await handleFilterChanged(currentFilters.value, currentDbType.value)
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

// Initialize with last 90 days on mount
onMounted(() => {
  const defaultFilters: TransactionFilters = {
    dateFrom: getDate90DaysAgo(),
    dateTo: getTodayDate(),
  }
  handleFilterChanged(defaultFilters, 'sqlite')
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
