<template>
  <div class="transaction-table-container">
    <!-- Global search bar (when enabled) -->
    <div v-if="showSearch" class="mb-4">
      <input
        :value="globalFilter"
        @input="onGlobalFilterChange"
        type="text"
        placeholder="Search all transactions..."
        class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
      />
    </div>

    <!-- Main table -->
    <div class="overflow-x-auto border border-gray-300 rounded-lg dark:border-gray-600">
      <table class="min-w-full table-auto">
        <thead class="bg-gray-100 border-b-2 border-gray-300 dark:bg-gray-800 dark:border-gray-600">
          <tr v-for="headerGroup in table.getHeaderGroups()" :key="headerGroup.id">
            <th
              v-for="header in headerGroup.headers"
              :key="header.id"
              :style="{ width: header.getSize() !== 150 ? `${header.getSize()}px` : undefined }"
              class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600"
            >
              <FlexRender
                :render="header.column.columnDef.header"
                :props="header.getContext()"
              />
            </th>
          </tr>
        </thead>
        <tbody class="bg-white dark:bg-gray-900">
          <template v-for="row in table.getRowModel().rows" :key="row.id">
            <tr
              :class="[
                'transaction-row',
                `transaction-${row.original.transaction.id}`,
                row.original.isFirstPosting ? 'border-t-2 border-blue-300 dark:border-blue-700' : 'border-t border-gray-200 dark:border-gray-700',
                row.original.isLastPosting ? 'border-b-2 border-blue-300 dark:border-blue-700' : 'border-b border-gray-200 dark:border-gray-700',
                !isTransactionBalanced(row.original.transaction) ? 'bg-red-100/30 dark:bg-red-900/20' : '',
                row.original.transaction.import_details?.is_duplicate ? 'bg-gray-50 dark:bg-gray-800/50' : ''
              ]"
            >
              <template v-for="cell in row.getVisibleCells()" :key="cell.id">
                <td
                  v-if="!shouldSkipCell(cell)"
                  :rowspan="getRowSpan(cell)"
                  :class="[
                    'px-2 py-2 border-r border-gray-200 dark:border-gray-700 text-sm',
                    { 'align-top': getRowSpan(cell) > 1 },
                    { 'text-right': ['amount'].includes(cell.column.id) },
                    { 'text-center': ['actions'].includes(cell.column.id) },
                    { 'bg-gray-50 dark:bg-gray-700': cell.column.id === '#' && getRowSpan(cell) > 1 }
                  ]"
                >
                  <FlexRender :render="cell.column.columnDef.cell" :props="cell.getContext()" />
                </td>
              </template>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Pagination controls -->
    <div v-if="pageSize > 0" class="flex items-center justify-between mt-4">
      <div class="text-sm text-gray-700 dark:text-gray-300">
        Showing {{ table.getRowModel().rows.length }} of {{ props.transactions.length }} entries
      </div>
      <div class="flex space-x-2">
        <button
          @click="() => table.previousPage()"
          :disabled="!table.getCanPreviousPage()"
          class="px-3 py-1 text-sm bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
        >
          Previous
        </button>
        <button
          @click="() => table.nextPage()"
          :disabled="!table.getCanNextPage()"
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
import { ref, computed, onMounted, watch, h } from 'vue'
import {
  useVueTable,
  createColumnHelper,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  FlexRender,
} from '@tanstack/vue-table'
import type { TransactionViewModel, PostingViewModel } from '@/types/transactions'
import type { Cell } from '@tanstack/vue-table'

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
  pageSize: 25,
})

// Define emits
const emit = defineEmits<{
  (e: 'transactionsUpdated', transactions: TransactionViewModel[]): void
}>()

// State
const originalTransactions = ref<TransactionViewModel[]>([])
const globalFilter = ref('')
const availableAccounts = ref<string[]>([])
const availableCurrencies = ref<string[]>([])

// Data transformation for Tanstack Table
const flatData = computed(() => {
  let transactionCounter = 0;
  return props.transactions.flatMap((transaction) => {
    transactionCounter++;
    return transaction.postings.map((posting, index) => ({
      ...posting,
      transaction,
      isFirstPosting: index === 0,
      isLastPosting: index === transaction.postings.length - 1,
      transactionIndex: transactionCounter,
    }))
  })
})

// Column definitions
const columnHelper = createColumnHelper<any>()

const spannedColumnIds = ['#', 'date', 'flag', 'payee', 'narration', 'tags_links']

const getRowSpan = (cell: Cell<any, any>) => {
  if (spannedColumnIds.includes(cell.column.id) && cell.row.original.isFirstPosting) {
    return cell.row.original.transaction.postings.length
  }
  return 1
}

const shouldSkipCell = (cell: Cell<any, any>) => {
  return spannedColumnIds.includes(cell.column.id) && !cell.row.original.isFirstPosting
}

const columns = [
  columnHelper.accessor('transactionIndex', {
    id: '#',
    header: '#',
    cell: info => info.getValue(),
    size: 32,
  }),
  columnHelper.accessor(row => row.transaction.date, {
    id: 'date',
    header: 'Date',
    cell: ({ row, getValue }) => props.editable
      ? h('input', {
          type: 'date',
          value: getValue(),
          onInput: (e: any) => updateTransactionDate(row.original.transaction, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm'
        })
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 96,
  }),
  columnHelper.accessor(row => row.transaction.flag, {
    id: 'flag',
    header: 'Flag',
    cell: ({ row, getValue }) => props.editable
      ? h('select', {
          value: getValue(),
          onChange: (e: any) => updateTransactionFlag(row.original.transaction, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm'
        }, [h('option', { value: '*' }, '*'), h('option', { value: '!' }, '!')])
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 48,
  }),
  columnHelper.accessor(row => row.transaction.payee, {
    id: 'payee',
    header: 'Payee',
    cell: ({ row, getValue }) => props.editable
      ? h('input', {
          type: 'text',
          value: getValue(),
          onInput: (e: any) => updateTransactionPayee(row.original.transaction, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm',
          placeholder: 'Payee'
        })
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 128,
  }),
  columnHelper.accessor(row => row.transaction.narration, {
    id: 'narration',
    header: 'Narration',
    cell: ({ row, getValue }) => props.editable
      ? h('input', {
          type: 'text',
          value: getValue(),
          onInput: (e: any) => updateTransactionNarration(row.original.transaction, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm',
          placeholder: 'Description'
        })
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 192,
  }),
  columnHelper.accessor(row => [...row.transaction.tags, ...row.transaction.links.map((l: string) => `^${l}`)].join(' '), {
    id: 'tags_links',
    header: 'Tags/Links',
    cell: ({ row, getValue }) => props.editable
      ? h('input', {
          type: 'text',
          value: getValue(),
          onInput: (e: any) => updateTransactionTagsLinks(row.original.transaction, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-700 dark:text-white text-sm',
          placeholder: '#tag ^link'
        })
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 128,
  }),
  columnHelper.accessor('account', {
    header: 'Account',
    cell: ({ row, getValue }) => props.editable
      ? h('select', {
          value: getValue(),
          onChange: (e: any) => updatePostingAccount(row.original.transaction, row.index, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-800 dark:text-white text-sm'
        }, availableAccounts.value.map(acc => h('option', { value: acc }, acc)))
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
  }),
  columnHelper.accessor('amount', {
    header: 'Amount',
    cell: ({ row, getValue }) => {
      const amount = getValue()
      const amountClass = amount > 0 ? 'text-green-700 bg-green-50 dark:bg-green-900/20' : amount < 0 ? 'text-red-700 bg-red-50 dark:bg-red-900/20' : ''
      return props.editable
        ? h('input', {
            type: 'number',
            step: '0.01',
            value: amount || '',
            onInput: (e: any) => updatePostingAmount(row.original.transaction, row.index, e.target.value),
            class: `w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 text-right dark:bg-gray-800 dark:text-white text-sm ${amountClass}`
          })
        : h('span', { class: `text-gray-900 dark:text-white text-sm ${amountClass}` }, amount)
    },
    size: 96,
  }),
  columnHelper.accessor('currency', {
    header: 'Currency',
    cell: ({ row, getValue }) => props.editable
      ? h('select', {
          value: getValue(),
          onChange: (e: any) => updatePostingCurrency(row.original.transaction, row.index, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-800 dark:text-white text-sm'
        }, availableCurrencies.value.map(curr => h('option', { value: curr }, curr)))
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 64,
  }),
  columnHelper.display({
    id: 'actions',
    header: 'Actions',
    cell: ({ row }) => {
      if (!props.editable) return null
      const buttons = [
        h('button', {
          onClick: () => removePosting(row.original.transaction, row.index),
          class: 'text-red-600 hover:text-red-800 text-xs px-1 dark:text-red-400 dark:hover:text-red-300',
          title: 'Remove posting'
        }, '×'),
      ]
      if (row.original.isFirstPosting) {
        buttons.push(h('button', {
          onClick: () => addPosting(row.original.transaction),
          class: 'text-green-600 hover:text-green-800 text-xs px-1 dark:text-green-400 dark:hover:text-green-300',
          title: 'Add posting'
        }, '+'))
        buttons.push(h('button', {
          onClick: () => removeTransaction(row.original.transaction),
          class: 'text-red-600 hover:text-red-800 text-xs px-1 dark:text-red-400 dark:hover:text-red-300',
          title: 'Remove transaction'
        }, '−'))
      } else {
        buttons.push(h('span', { class: 'text-xs px-1 invisible' }, '+'))
        buttons.push(h('span', { class: 'text-xs px-1 invisible' }, '−'))
      }
      return h('div', { class: 'flex items-center justify-center gap-1 text-sm' }, buttons)
    },
    size: 64,
  }),
]

const table = useVueTable({
  get data() { return flatData.value },
  columns,
  state: {
    get globalFilter() { return globalFilter.value },
  },
  onGlobalFilterChange: (filter: string) => {
    globalFilter.value = filter
  },
  getCoreRowModel: getCoreRowModel(),
  getFilteredRowModel: getFilteredRowModel(),
  getPaginationRowModel: getPaginationRowModel(),
})

const onGlobalFilterChange = (e: Event) => {
  globalFilter.value = (e.target as HTMLInputElement).value
}

watch(() => props.pageSize, (size) => {
  table.setPageSize(size)
})

onMounted(() => {
  table.setPageSize(props.pageSize)
  originalTransactions.value = JSON.parse(JSON.stringify(props.transactions))
  availableAccounts.value = ['Assets:Bank:Checking', 'Assets:Bank:Savings', 'Expenses:Groceries', 'Expenses:Utilities', 'Expenses:Entertainment', 'Income:Salary', 'Liabilities:CreditCard']
  availableCurrencies.value = ['USD', 'EUR', 'GBP', 'CAD', 'AUD']
})

watch(() => props.transactions, (newTransactions) => {
  originalTransactions.value = JSON.parse(JSON.stringify(newTransactions))
  table.setPageIndex(0)
}, { deep: true })

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

const unbalancedCount = computed(() => {
  return props.transactions.filter(t => !isTransactionBalanced(t)).length
})

const isTransactionBalanced = (transaction: TransactionViewModel): boolean => {
  const total = transaction.postings.reduce((sum, posting) => sum + (posting.amount || 0), 0)
  return Math.abs(total) < 0.01
}

const findTransactionIndex = (transaction: TransactionViewModel) => {
  return props.transactions.findIndex(t => t.id === transaction.id)
}

const emitUpdate = (updatedTransactions: TransactionViewModel[]) => {
  emit('transactionsUpdated', updatedTransactions)
}

const updateTransactionDate = (transaction: TransactionViewModel, newDate: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].date = newDate
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const updateTransactionFlag = (transaction: TransactionViewModel, newFlag: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].flag = newFlag
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const updateTransactionPayee = (transaction: TransactionViewModel, newPayee: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].payee = newPayee
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const updateTransactionNarration = (transaction: TransactionViewModel, newNarration: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].narration = newNarration
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const updateTransactionTagsLinks = (transaction: TransactionViewModel, newValue: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    const parts = newValue.split(/\s+/).filter(p => p)
    updatedTransactions[txIndex].tags = parts.filter(p => p.startsWith('#')).map(p => p.substring(1))
    updatedTransactions[txIndex].links = parts.filter(p => p.startsWith('^')).map(p => p.substring(1))
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const updatePostingAccount = (transaction: TransactionViewModel, postingIndex: number, newAccount: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex].account = newAccount
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const updatePostingAmount = (transaction: TransactionViewModel, postingIndex: number, newAmountStr: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex].amount = newAmountStr ? parseFloat(newAmountStr) : null
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const updatePostingCurrency = (transaction: TransactionViewModel, postingIndex: number, newCurrency: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex].currency = newCurrency
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const addPosting = (transaction: TransactionViewModel) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings.push({ account: '', amount: null, currency: 'USD' })
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const removePosting = (transaction: TransactionViewModel, postingIndex: number) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1 && updatedTransactions[txIndex].postings.length > 1) {
    updatedTransactions[txIndex].postings.splice(postingIndex, 1)
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
  }
}

const removeTransaction = (transaction: TransactionViewModel) => {
  const updatedTransactions = props.transactions.filter(t => t.id !== transaction.id)
  emitUpdate(updatedTransactions)
}

const resetToOriginal = () => {
  emitUpdate(JSON.parse(JSON.stringify(originalTransactions.value)))
}

defineExpose({
  resetToOriginal,
  clearState: () => {
    originalTransactions.value = []
    table.setPageIndex(0)
    emitUpdate([])
  }
})
</script>

<style>
/* No custom styles needed for hover as it's handled by Tailwind now */
</style>
