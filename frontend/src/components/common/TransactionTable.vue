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
    <div class="overflow-x-auto border border-gray-200 rounded-lg dark:border-gray-700">
      <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
        <thead class="bg-gray-50 dark:bg-gray-800">
          <tr>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400 w-12">#</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400 w-24">Date</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400 w-16">Flag</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">Payee</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">Narration</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">Tags/Links</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">Account</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">Amount</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">Currency</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">Actions</th>
          </tr>
        </thead>
        <tbody class="bg-white divide-y divide-gray-200 dark:bg-gray-900 dark:divide-gray-800">
          <template v-for="(transaction, transactionIndex) in displayedTransactions" :key="transaction.id">
            <tr 
              :class="{
                'bg-red-100 dark:bg-red-900/30': !isTransactionBalanced(transaction),
                'bg-gray-50 dark:bg-gray-800': transaction.import_details?.is_duplicate
              }"
            >
              <td class="px-4 py-2 text-sm text-gray-900 dark:text-white">{{ transactionIndex + 1 }}</td>
              <td class="px-4 py-2 text-sm">
                <input
                  v-if="editable"
                  type="date"
                  :value="transaction.date"
                  @input="updateTransactionDate(transaction, ($event.target as HTMLInputElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                />
                <span v-else class="text-gray-900 dark:text-white">{{ transaction.date }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <select
                  v-if="editable"
                  :value="transaction.flag"
                  @change="updateTransactionFlag(transaction, ($event.target as HTMLSelectElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                >
                  <option value="*">*</option>
                  <option value="!">!</option>
                </select>
                <span v-else class="text-gray-900 dark:text-white">{{ transaction.flag }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <input
                  v-if="editable"
                  type="text"
                  :value="transaction.payee"
                  @input="updateTransactionPayee(transaction, ($event.target as HTMLInputElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                />
                <span v-else class="text-gray-900 dark:text-white">{{ transaction.payee }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <input
                  v-if="editable"
                  type="text"
                  :value="transaction.narration"
                  @input="updateTransactionNarration(transaction, ($event.target as HTMLInputElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                />
                <span v-else class="text-gray-900 dark:text-white">{{ transaction.narration }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <input
                  v-if="editable"
                  type="text"
                  :value="[...transaction.tags, ...transaction.links.map(l => `^${l}`)].join(' ')"
                  @input="updateTransactionTagsLinks(transaction, ($event.target as HTMLInputElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                />
                <span v-else class="text-gray-900 dark:text-white">{{ [...transaction.tags, ...transaction.links.map(l => `^${l}`)].join(' ') }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <select
                  v-if="editable"
                  :value="transaction.postings[0]?.account || ''"
                  @change="updateFirstPostingAccount(transaction, ($event.target as HTMLSelectElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                >
                  <option v-for="account in availableAccounts" :key="account" :value="account">
                    {{ account }}
                  </option>
                </select>
                <span v-else class="text-gray-900 dark:text-white">{{ transaction.postings[0]?.account || '' }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <input
                  v-if="editable"
                  type="number"
                  step="0.01"
                  :value="transaction.postings[0]?.amount || ''"
                  @input="updateFirstPostingAmount(transaction, ($event.target as HTMLInputElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                />
                <span v-else class="text-gray-900 dark:text-white">{{ transaction.postings[0]?.amount }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <select
                  v-if="editable"
                  :value="transaction.postings[0]?.currency || 'USD'"
                  @change="updateFirstPostingCurrency(transaction, ($event.target as HTMLSelectElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                >
                  <option v-for="currency in availableCurrencies" :key="currency" :value="currency">
                    {{ currency }}
                  </option>
                </select>
                <span v-else class="text-gray-900 dark:text-white">{{ transaction.postings[0]?.currency || 'USD' }}</span>
              </td>
              <td class="px-4 py-2 text-sm text-gray-400">Actions</td>
            </tr>
            <!-- Posting rows for multi-posting transactions -->
            <tr 
              v-for="(posting, postingIndex) in transaction.postings.slice(1)" 
              :key="`posting-${transaction.id}-${postingIndex}`"
              :class="{
                'bg-red-100 dark:bg-red-900/30': !isTransactionBalanced(transaction),
                'bg-gray-50 dark:bg-gray-800': transaction.import_details?.is_duplicate
              }"
            >
              <td class="px-4 py-2 text-sm text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700"></td>
              <td class="px-4 py-2 text-sm text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700"></td>
              <td class="px-4 py-2 text-sm text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700"></td>
              <td class="px-4 py-2 text-sm text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700"></td>
              <td class="px-4 py-2 text-sm text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700"></td>
              <td class="px-4 py-2 text-sm text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700"></td>
              <td class="px-4 py-2 text-sm">
                <select 
                  v-if="editable"
                  :value="posting.account"
                  @change="updatePostingAccount(transaction, postingIndex + 1, ($event.target as HTMLSelectElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                >
                  <option v-for="account in availableAccounts" :key="account" :value="account">
                    {{ account }}
                  </option>
                </select>
                <span v-else class="text-gray-900 dark:text-white">{{ posting.account }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <input
                  v-if="editable"
                  type="number"
                  step="0.01"
                  :value="posting.amount || ''"
                  @input="updatePostingAmount(transaction, postingIndex + 1, ($event.target as HTMLInputElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                />
                <span v-else class="text-gray-900 dark:text-white">{{ posting.amount }}</span>
              </td>
              <td class="px-4 py-2 text-sm">
                <select
                  v-if="editable"
                  :value="posting.currency"
                  @change="updatePostingCurrency(transaction, postingIndex + 1, ($event.target as HTMLSelectElement).value)"
                  class="w-full px-2 py-1 text-sm border border-gray-300 rounded shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                >
                  <option v-for="currency in availableCurrencies" :key="currency" :value="currency">
                    {{ currency }}
                  </option>
                </select>
                <span v-else class="text-gray-900 dark:text-white">{{ posting.currency }}</span>
              </td>
              <td v-if="editable" class="px-4 py-2 text-sm">
                <div class="flex space-x-1">
                  <button 
                    @click="removePosting(transaction, postingIndex + 1)"
                    class="px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600"
                  >
                    Remove
                  </button>
                </div>
              </td>
              <td v-else class="px-4 py-2 text-sm"></td>
            </tr>
            
            <!-- Action row for first posting -->
            <tr 
              v-if="editable"
              :key="`actions-${transaction.id}`"
              :class="{
                'bg-red-100 dark:bg-red-900/30': !isTransactionBalanced(transaction),
                'bg-gray-50 dark:bg-gray-800': transaction.import_details?.is_duplicate
              }"
            >
              <td :colspan="9" class="px-4 py-2 text-sm border-t border-gray-200 dark:border-gray-700">
                <div class="flex justify-between">
                  <button 
                    @click="removeTransaction(transaction)"
                    class="px-3 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600"
                  >
                    Remove Transaction
                  </button>
                  <div class="flex space-x-2">
                    <button 
                      @click="addPosting(transaction)"
                      class="px-3 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600"
                    >
                      Add Posting
                    </button>
                  </div>
                </div>
              </td>
              <td v-if="editable" class="px-4 py-2 text-sm border-t border-gray-200 dark:border-gray-700">
                <div class="flex justify-end space-x-1">
                  <button 
                    @click="removePosting(transaction, 0)"
                    v-if="transaction.postings.length > 1"
                    class="px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600"
                  >
                    Remove
                  </button>
                </div>
              </td>
            </tr>
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

// Update first posting properties
const updateFirstPostingAccount = (transaction: TransactionViewModel, newAccount: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1 && updatedTransactions[txIndex].postings[0]) {
    updatedTransactions[txIndex].postings[0].account = newAccount
    updatedTransactions[txIndex].meta.isModified = true
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updateFirstPostingAmount = (transaction: TransactionViewModel, newAmount: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1 && updatedTransactions[txIndex].postings[0]) {
    const amount = newAmount ? parseFloat(newAmount) : null
    updatedTransactions[txIndex].postings[0].amount = amount
    updatedTransactions[txIndex].meta.isModified = true
    emit('transactionsUpdated', updatedTransactions)
  }
}

const updateFirstPostingCurrency = (transaction: TransactionViewModel, newCurrency: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = updatedTransactions.findIndex(t => t.id === transaction.id)
  
  if (txIndex !== -1 && updatedTransactions[txIndex].postings[0]) {
    updatedTransactions[txIndex].postings[0].currency = newCurrency
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