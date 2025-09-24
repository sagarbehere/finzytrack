<template>
  <div class="transaction-table-container">
    <!-- Global search bar (when enabled) -->
    <div v-if="showSearch" class="mb-4">
      <input
        v-model="globalFilter"
        type="text"
        placeholder="Search all transactions..."
        class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
      />
    </div>

    <!-- Main table -->
    <div class="overflow-x-auto border border-gray-300 rounded-lg dark:border-gray-600">
      <table class="min-w-full table-auto">
        <thead class="bg-gray-100 border-b-2 border-gray-300 dark:bg-gray-800 dark:border-gray-600">
          <tr>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-8">#</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-24">Date</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-12">Flag</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-32">Payee</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-48">Narration</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-32">Tags/Links</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600">Account</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-24 text-right">Amount</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 w-16">Currency</th>
            <th class="px-2 py-2 text-left text-xs font-bold text-gray-700 dark:text-gray-300 w-16">Actions</th>
          </tr>
        </thead>
        <tbody class="bg-white">
          <template v-for="(transaction, transactionIndex) in displayedTransactions" :key="transaction.id">
            <template v-for="(posting, postingIndex) in transaction.postings" :key="`posting-${transaction.id}-${postingIndex}`">
              <tr 
                :class="[
                  'transaction-row',
                  `transaction-${transaction.id}`,
                  postingIndex === 0 ? 'border-t-2 border-blue-300 dark:border-blue-700' : 'border-t border-gray-200 dark:border-gray-700',
                  postingIndex === transaction.postings.length - 1 ? 'border-b-2 border-blue-300 dark:border-blue-700' : 'border-b border-gray-200 dark:border-gray-700',
                  {
                    'bg-red-100/30 dark:bg-red-900/20': !isTransactionBalanced(transaction),
                    'bg-gray-50 dark:bg-gray-800/50': transaction.import_details?.is_duplicate
                  }
                ]"
                @mouseenter="highlightTransaction(transaction.id, true)"
                @mouseleave="highlightTransaction(transaction.id, false)"
              >
                <!-- Row Number - only on first posting row -->
                <td v-if="postingIndex === 0" :rowspan="transaction.postings.length" class="px-2 py-2 text-xs text-gray-500 border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700 align-top">
                  {{ getRowNumber(transactionIndex) }}
                </td>

                <!-- Date - only on first posting row -->
                <td v-if="postingIndex === 0" :rowspan="transaction.postings.length" class="px-2 py-2 border-r border-gray-200 dark:border-gray-700 align-top">
                  <input
                    v-if="editable"
                    type="date"
                    :value="transaction.date"
                    @input="updateTransactionDate(transaction, ($event.target as HTMLInputElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm"
                  />
                  <span v-else class="text-gray-900 dark:text-white text-sm">{{ transaction.date }}</span>
                </td>

                <!-- Flag - only on first posting row -->
                <td v-if="postingIndex === 0" :rowspan="transaction.postings.length" class="px-2 py-2 border-r border-gray-200 dark:border-gray-700 align-top">
                  <select
                    v-if="editable"
                    :value="transaction.flag"
                    @change="updateTransactionFlag(transaction, ($event.target as HTMLSelectElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm"
                  >
                    <option value="*">*</option>
                    <option value="!">!</option>
                  </select>
                  <span v-else class="text-gray-900 dark:text-white text-sm">{{ transaction.flag }}</span>
                </td>

                <!-- Payee - only on first posting row -->
                <td v-if="postingIndex === 0" :rowspan="transaction.postings.length" class="px-2 py-2 border-r border-gray-200 dark:border-gray-700 align-top">
                  <input
                    v-if="editable"
                    type="text"
                    :value="transaction.payee"
                    @input="updateTransactionPayee(transaction, ($event.target as HTMLInputElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm"
                    placeholder="Payee"
                  />
                  <span v-else class="text-gray-900 dark:text-white text-sm">{{ transaction.payee }}</span>
                </td>

                <!-- Narration - only on first posting row -->
                <td v-if="postingIndex === 0" :rowspan="transaction.postings.length" class="px-2 py-2 border-r border-gray-200 dark:border-gray-700 align-top">
                  <input
                    v-if="editable"
                    type="text"
                    :value="transaction.narration"
                    @input="updateTransactionNarration(transaction, ($event.target as HTMLInputElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm"
                    placeholder="Description"
                  />
                  <span v-else class="text-gray-900 dark:text-white text-sm">{{ transaction.narration }}</span>
                </td>

                <!-- Tags/Links - only on first posting row -->
                <td v-if="postingIndex === 0" :rowspan="transaction.postings.length" class="px-2 py-2 border-r border-gray-200 dark:border-gray-700 align-top">
                  <input
                    v-if="editable"
                    type="text"
                    :value="[...transaction.tags, ...transaction.links.map(l => `^${l}`)].join(' ')"
                    @input="updateTransactionTagsLinks(transaction, ($event.target as HTMLInputElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-700 dark:text-white text-sm"
                    placeholder="#tag ^link"
                  />
                  <span v-else class="text-gray-900 dark:text-white text-sm">{{ [...transaction.tags, ...transaction.links.map(l => `^${l}`)].join(' ') }}</span>
                </td>

                <!-- Account - one row per posting -->
                <td class="px-2 py-2 border-r border-gray-200 dark:border-gray-700">
                  <select
                    v-if="editable"
                    :value="posting.account"
                    @change="updatePostingAccount(transaction, postingIndex, ($event.target as HTMLSelectElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-800 dark:text-white text-sm"
                  >
                    <option v-for="account in availableAccounts" :key="account" :value="account">
                      {{ account }}
                    </option>
                  </select>
                  <span v-else class="text-gray-900 dark:text-white text-sm">{{ posting.account }}</span>
                </td>

                <!-- Amount - one row per posting -->
                <td class="px-2 py-2 border-r border-gray-200 dark:border-gray-700 text-right">
                  <input
                    v-if="editable"
                    type="number"
                    step="0.01"
                    :value="posting.amount || ''"
                    @input="updatePostingAmount(transaction, postingIndex, ($event.target as HTMLInputElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 text-right dark:bg-gray-800 dark:text-white text-sm"
                    :class="posting.amount && posting.amount > 0 ? 'text-green-700 bg-green-50 dark:bg-green-900/20' : posting.amount && posting.amount < 0 ? 'text-red-700 bg-red-50 dark:bg-red-900/20' : ''"
                  />
                  <span v-else class="text-gray-900 dark:text-white text-sm" :class="posting.amount && posting.amount > 0 ? 'text-green-700' : posting.amount && posting.amount < 0 ? 'text-red-700' : ''">{{ posting.amount }}</span>
                </td>

                <!-- Currency - one row per posting -->
                <td class="px-2 py-2 border-r border-gray-200 dark:border-gray-700">
                  <select
                    v-if="editable"
                    :value="posting.currency"
                    @change="updatePostingCurrency(transaction, postingIndex, ($event.target as HTMLSelectElement).value)"
                    class="w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-800 dark:text-white text-sm"
                  >
                    <option v-for="currency in availableCurrencies" :key="currency" :value="currency">
                      {{ currency }}
                    </option>
                  </select>
                  <span v-else class="text-gray-900 dark:text-white text-sm">{{ posting.currency }}</span>
                </td>

                <!-- Actions - one row per posting -->
                <td class="px-2 py-2 text-center">
                  <div v-if="editable" class="flex gap-1 justify-center text-sm">
                    <button 
                      v-if="postingIndex === 0" 
                      @click="removePosting(transaction, 0)"
                      class="text-red-600 hover:text-red-800 text-xs px-1 dark:text-red-400 dark:hover:text-red-300"
                      title="Remove posting"
                    >
                      ×
                    </button>
                    <button 
                      v-if="postingIndex === 0" 
                      @click="addPosting(transaction)"
                      class="text-green-600 hover:text-green-800 text-xs px-1 dark:text-green-400 dark:hover:text-green-300"
                      title="Add posting"
                    >
                      +
                    </button>
                    <button 
                      v-else
                      @click="removePosting(transaction, postingIndex)"
                      class="text-red-600 hover:text-red-800 text-xs px-1 dark:text-red-400 dark:hover:text-red-300"
                      title="Remove posting"
                    >
                      ×
                    </button>
                  </div>
                </td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Pagination controls -->
    <div v-if="pageSize > 0" class="flex items-center justify-between mt-4">
      <div class="text-sm text-gray-700 dark:text-gray-300">
        Showing {{ displayedTransactions.length }} of {{ props.transactions.length }} entries
      </div>
      <div class="flex space-x-2">
        <button
          @click="prevPage"
          :disabled="currentPage === 1"
          class="px-3 py-1 text-sm bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
        >
          Previous
        </button>
        <button
          @click="nextPage"
          :disabled="currentPage === totalPages"
          class="px-3 py-1 text-sm bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
        >
          Next
        </button>
      </div>
    </div>

    <!-- Summary section (when enabled) -->
    <div v-if="showSummary" class="mt-4 p-4 bg-gray-50 rounded-lg border dark:bg-gray-800 dark:border-gray-700">
      <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-2">Transaction Summary</h3>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div class="bg-white p-4 rounded border dark:bg-gray-700 dark:border-gray-600">
          <p class="text-sm text-gray-500 dark:text-gray-400">Total Transactions</p>
          <p class="text-2xl font-semibold text-gray-900 dark:text-white">{{ props.transactions.length }}</p>
        </div>
        <div class="bg-white p-4 rounded border dark:bg-gray-700 dark:border-gray-600">
          <p class="text-sm text-gray-500 dark:text-gray-400">Total Amount</p>
          <p class="text-2xl font-semibold text-gray-900 dark:text-white">{{ totalAmount }}</p>
        </div>
        <div class="bg-white p-4 rounded border dark:bg-gray-700 dark:border-gray-600">
          <p class="text-sm text-gray-500 dark:text-gray-400">Unbalanced</p>
          <p class="text-2xl font-semibold text-gray-900 dark:text-white">{{ unbalancedCount }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import type { TransactionViewModel, PostingViewModel } from '@/types/transactions'

// Define props
interface Props {
  transactions: TransactionViewModel[]
  showSearch?: boolean
  showColumnFilters?: boolean
  showTransactionGrouping?: boolean
  showRunningBalance?: boolean
  showSummary?: boolean
  editable?: boolean
  pageSize?: number
}

const props = withDefaults(defineProps<Props>(), {
  showSearch: false,
  showColumnFilters: false,
  showTransactionGrouping: true,
  showRunningBalance: false,
  showSummary: false,
  editable: true,
  pageSize: 25
})

// Define emits
const emit = defineEmits<{
  (e: 'transactionsUpdated', transactions: TransactionViewModel[]): void
}>()

// State
const originalTransactions = ref<TransactionViewModel[]>([])
const globalFilter = ref('')
const currentPage = ref(1)
const availableAccounts = ref<string[]>([])
const availableCurrencies = ref<string[]>([])

// Calculate row number taking pagination into account
const getRowNumber = (transactionIndex: number) => {
  return (currentPage.value - 1) * props.pageSize + transactionIndex + 1
}

// Pagination
const totalPages = computed(() => {
  if (props.pageSize <= 0) return 1
  return Math.ceil(props.transactions.length / props.pageSize)
})

const displayedTransactions = computed(() => {
  if (props.pageSize <= 0) return filteredTransactions.value
  
  const start = (currentPage.value - 1) * props.pageSize
  const end = start + props.pageSize
  return filteredTransactions.value.slice(start, end)
})

// Filtering
const filteredTransactions = computed(() => {
  if (!globalFilter.value) return props.transactions
  
  const filter = globalFilter.value.toLowerCase()
  return props.transactions.filter(transaction => 
    transaction.payee.toLowerCase().includes(filter) ||
    transaction.narration.toLowerCase().includes(filter) ||
    transaction.date.includes(filter) ||
    transaction.postings.some(posting => 
      posting.account.toLowerCase().includes(filter) ||
      (posting.amount !== null && posting.amount.toString().includes(filter))
    )
  )
})

// Initialize with the initial transactions
onMounted(() => {
  originalTransactions.value = JSON.parse(JSON.stringify(props.transactions))
  // For now, just use some default values for accounts and currencies
  // In a real implementation, these would be fetched from the backend
  availableAccounts.value = [
    'Assets:Bank:Checking',
    'Assets:Bank:Savings',
    'Expenses:Groceries',
    'Expenses:Utilities',
    'Expenses:Entertainment',
    'Income:Salary',
    'Liabilities:CreditCard'
  ]
  
  availableCurrencies.value = [
    'USD',
    'EUR',
    'GBP',
    'CAD',
    'AUD'
  ]
})

// Update original transactions when props change
watch(() => props.transactions, (newTransactions) => {
  originalTransactions.value = JSON.parse(JSON.stringify(newTransactions))
  currentPage.value = 1 // Reset to first page when transactions change
}, { deep: true })

// Calculate total amount across all transactions
const totalAmount = computed(() => {
  let total = 0
  props.transactions.forEach(transaction => {
    transaction.postings.forEach(posting => {
      if (posting.amount) {
        total += posting.amount
      }
    })
  })
  return total.toFixed(2)
})

// Calculate number of unbalanced transactions
const unbalancedCount = computed(() => {
  return props.transactions.filter(t => !isTransactionBalanced(t)).length
})

// Check if a transaction is balanced
const isTransactionBalanced = (transaction: TransactionViewModel): boolean => {
  const total = transaction.postings.reduce((sum, posting) => {
    return sum + (posting.amount || 0)
  }, 0)
  
  // Allow for small floating-point errors
  return Math.abs(total) < 0.01
}

// Pagination methods



// Pagination methods
const nextPage = () => {
  if (currentPage.value < totalPages.value) {
    currentPage.value++
  }
}

const prevPage = () => {
  if (currentPage.value > 1) {
    currentPage.value--
  }
}

// Update transaction properties
const updateTransactionDate = (transaction: TransactionViewModel, newDate: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    updatedTransactions[txIndex].date = newDate
    updatedTransactions[txIndex].meta.isModified = true
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updateTransactionFlag = (transaction: TransactionViewModel, newFlag: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    updatedTransactions[txIndex].flag = newFlag
    updatedTransactions[txIndex].meta.isModified = true
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updateTransactionPayee = (transaction: TransactionViewModel, newPayee: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    updatedTransactions[txIndex].payee = newPayee
    updatedTransactions[txIndex].meta.isModified = true
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updateTransactionNarration = (transaction: TransactionViewModel, newNarration: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    updatedTransactions[txIndex].narration = newNarration
    updatedTransactions[txIndex].meta.isModified = true
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updateTransactionTagsLinks = (transaction: TransactionViewModel, newValue: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    const parts = newValue.split(/\s+/).filter(p => p)
    const tags = parts.filter(p => p.startsWith('#')).map(p => p.substring(1))
    const links = parts.filter(p => p.startsWith('^')).map(p => p.substring(1))
    
    updatedTransactions[txIndex].tags = tags
    updatedTransactions[txIndex].links = links
    updatedTransactions[txIndex].meta.isModified = true
    emit('transactionsUpdated', updatedTransactions)
  }
}

// Update posting properties
const updatePostingAccount = (transaction: TransactionViewModel, postingIndex: number, newAccount: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex] = {
      ...updatedTransactions[txIndex].postings[postingIndex],
      account: newAccount
    }
    
    // Mark transaction as modified
    updatedTransactions[txIndex].meta.isModified = true
    
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updatePostingAmount = (transaction: TransactionViewModel, postingIndex: number, newAmountStr: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    const newAmount = newAmountStr ? parseFloat(newAmountStr) : null
    updatedTransactions[txIndex].postings[postingIndex] = {
      ...updatedTransactions[txIndex].postings[postingIndex],
      amount: newAmount
    }
    
    // Mark transaction as modified
    updatedTransactions[txIndex].meta.isModified = true
    
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updatePostingCurrency = (transaction: TransactionViewModel, postingIndex: number, newCurrency: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex] = {
      ...updatedTransactions[txIndex].postings[postingIndex],
      currency: newCurrency
    }
    
    // Mark transaction as modified
    updatedTransactions[txIndex].meta.isModified = true
    
    emit('transactionsUpdated', updatedTransactions)
  }
}

// Add a new posting to a transaction
const addPosting = (transaction: TransactionViewModel) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings.push({
      account: '',
      amount: null,
      currency: 'USD' // Default currency
    })
    
    // Mark transaction as modified
    updatedTransactions[txIndex].meta.isModified = true
    
    emit('transactionsUpdated', updatedTransactions)
  }
}

// Remove a posting from a transaction
const removePosting = (transaction: TransactionViewModel, postingIndex: number) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1 && updatedTransactions[txIndex].postings.length > 1) {
    updatedTransactions[txIndex].postings.splice(postingIndex, 1)
    
    // Mark transaction as modified
    updatedTransactions[txIndex].meta.isModified = true
    
    emit('transactionsUpdated', updatedTransactions)
  }
}

// Remove an entire transaction
const removeTransaction = (transaction: TransactionViewModel) => {
  const updatedTransactions = props.transactions.filter(t => t.id !== transaction.id)
  emit('transactionsUpdated', updatedTransactions)
}

// Reset to original transactions
const resetToOriginal = () => {
  emit('transactionsUpdated', JSON.parse(JSON.stringify(originalTransactions.value)))
}

// Expose methods for parent components
defineExpose({
  resetToOriginal,
  clearState: () => {
    originalTransactions.value = []
    currentPage.value = 1
    emit('transactionsUpdated', [])
  }
})
</script>

<style>
/* Hover effect for entire transaction rows */
.transaction-table-container tr:hover {
  background-color: rgba(219, 234, 254, 0.5) !important; /* Light blue for light mode */
}

.transaction-table-container .dark tr:hover {
  background-color: rgba(55, 65, 81, 0.5) !important; /* Gray-700 equivalent for dark mode */
}

/* Ensure text colors remain readable when row is highlighted */
.transaction-table-container tr:hover td,
.transaction-table-container tr:hover th {
  /* Colors will be controlled by text-gray-900/dark:text-white classes */
}
</style>