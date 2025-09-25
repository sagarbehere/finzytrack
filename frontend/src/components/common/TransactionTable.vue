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
      <table class="w-full table-fixed" style="table-layout: fixed; width: 100%;">
        <thead class="bg-gray-100 border-b-2 border-gray-300 dark:bg-gray-800 dark:border-gray-600">
          <tr v-for="headerGroup in table.getHeaderGroups()" :key="headerGroup.id">
            <th
              v-for="header in headerGroup.headers"
              :key="header.id"
              :style="{ width: `${header.getSize()}px` }"
              class="px-2 py-2 text-left text-xs font-bold text-gray-700 border-r border-gray-300 dark:text-gray-300 dark:border-gray-600 relative"
            >
              <FlexRender
                :render="header.column.columnDef.header"
                :props="header.getContext()"
              />
              <div
                v-if="header.column.getCanResize()"
                class="resize-handle"
                :class="{ 'resizing': header.column.getIsResizing() }"
                @mousedown="(e) => header.getResizeHandler()(e)"
                @touchstart="(e) => header.getResizeHandler()(e)"
              ></div>
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
                  :data-column-id="cell.column.id"
                  :class="[
                    'px-2 py-2 border-r border-gray-200 dark:border-gray-700 text-sm',
                    { 'align-top': getRowSpan(cell) > 1 },
                    { 'text-right': ['amount'].includes(cell.column.id) },
                    { 'text-center': ['actions'].includes(cell.column.id) },
                    { 'bg-gray-50 dark:bg-gray-700': cell.column.id === '#' && getRowSpan(cell) > 1 },
                    { 'align-top': ['payee', 'narration'].includes(cell.column.id) }
                  ]"
                  :style="['payee', 'narration'].includes(cell.column.id) ? { 
                    'white-space': 'normal', 
                    'word-wrap': 'break-word', 
                    'overflow-wrap': 'break-word',
                    'word-break': 'break-word',
                    'min-width': '0',
                    'max-width': '100%'
                  } : {}"
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
        <template v-if="currentPageTransactionCount === 1">
          Showing transaction {{ firstEntryNumber }} of {{ filteredTransactions.length }}
        </template>
        <template v-else>
          Showing transactions {{ firstEntryNumber }} to {{ lastEntryNumber }} of {{ filteredTransactions.length }}
        </template>
      </div>
      <div class="flex space-x-2">
        <button
          @click="goToPreviousPage"
          :disabled="currentPageIndex === 0"
          class="px-3 py-1 text-sm bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
        >
          Previous
        </button>
        <button
          @click="goToNextPage"
          :disabled="currentPageIndex >= totalPages - 1"
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
import { ref, computed, onMounted, watch, h, nextTick } from 'vue'
import {
  useVueTable,
  createColumnHelper,
  getCoreRowModel,
  getFilteredRowModel,
  FlexRender,
} from '@tanstack/vue-table'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import type { TransactionViewModel } from '@/types/transactions'
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
  (e: 'tableDisplayed'): void
}>()

// State
const originalTransactions = ref<TransactionViewModel[]>([])
const globalFilter = ref('')
const columnSizing = ref({})

// Filtered transactions
const filteredTransactions = computed(() => {
  if (!globalFilter.value) return props.transactions
  
  const filterValue = globalFilter.value.toLowerCase()
  return props.transactions.filter(transaction => {
    // Check if the filter value matches any field in the transaction or its postings
    const transactionMatch = 
      transaction.date.toLowerCase().includes(filterValue) ||
      transaction.payee.toLowerCase().includes(filterValue) ||
      transaction.narration.toLowerCase().includes(filterValue) ||
      transaction.tags.some(tag => tag.toLowerCase().includes(filterValue)) ||
      transaction.links.some(link => link.toLowerCase().includes(filterValue))
    
    if (transactionMatch) return true
    
    // Check if any posting matches
    return transaction.postings.some(posting => 
      posting.account.toLowerCase().includes(filterValue) ||
      posting.currency.toLowerCase().includes(filterValue) ||
      (posting.amount !== null && posting.amount.toString().toLowerCase().includes(filterValue))
    )
  })
})



// Custom pagination implementation to keep transaction groups together
const currentPageIndex = ref(0)

// Calculate pages that keep entire transactions together
const pages = computed(() => {
  if (props.pageSize <= 0) return []
  
  const result = []
  let currentPage = []
  let currentSize = 0
  
  let transactionCounter = 0
  
  for (const transaction of filteredTransactions.value) {
    transactionCounter++
    const postings = transaction.postings
    
    // If adding this transaction would exceed page size and page isn't empty, start new page
    if (currentPage.length > 0 && currentSize + postings.length > props.pageSize) {
      result.push(currentPage)
      currentPage = []
      currentSize = 0
    }
    
    // Add all postings for this transaction to current page
    const transactionRows = postings.map((posting, index) => ({
      ...posting,
      transaction,
      postingIndex: index,  // Store the original posting index
      isFirstPosting: index === 0,
      isLastPosting: index === postings.length - 1,
      transactionIndex: transactionCounter,
    }))
    
    currentPage.push(...transactionRows)
    currentSize += postings.length
  }
  
  // Add the last page if it has content
  if (currentPage.length > 0) {
    result.push(currentPage)
  }
  
  return result
})

const totalPages = computed(() => pages.value.length)

const currentPageTransactions = computed(() => {
  if (pages.value[currentPageIndex.value]) {
    return pages.value[currentPageIndex.value]
  }
  return []
})

const currentPageTransactionCount = computed(() => {
  // Count unique transactions in the current page (not the individual posting rows)
  const currentRows = currentPageTransactions.value
  if (currentRows.length === 0) return 0
  
  // Count how many unique transactions are on this page
  const uniqueTransactionIds = new Set()
  currentRows.forEach(row => {
    uniqueTransactionIds.add(row.transaction.id)
  })
  return uniqueTransactionIds.size
})

// Calculate the first and last transaction numbers for the pagination display
const firstEntryNumber = computed(() => {
  if (filteredTransactions.value.length === 0 || currentPageIndex.value >= totalPages.value) {
    return 0
  }
  
  // Calculate how many transactions appear on pages before the current page
  let previousTransactionCount = 0
  for (let i = 0; i < currentPageIndex.value; i++) {
    const page = pages.value[i] || []
    const uniqueTransactionIds = new Set()
    page.forEach(row => {
      uniqueTransactionIds.add(row.transaction.id)
    })
    previousTransactionCount += uniqueTransactionIds.size
  }
  
  // The first entry number is the count of transactions on previous pages + 1
  return previousTransactionCount + 1
})

const lastEntryNumber = computed(() => {
  if (filteredTransactions.value.length === 0 || currentPageIndex.value >= totalPages.value) {
    return 0
  }
  
  return firstEntryNumber.value + currentPageTransactionCount.value - 1
})

const goToPreviousPage = () => {
  if (currentPageIndex.value > 0) {
    currentPageIndex.value--
  }
}

const goToNextPage = () => {
  if (currentPageIndex.value < totalPages.value - 1) {
    currentPageIndex.value++
  }
}

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
    minSize: 20,
    enableResizing: true,
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
    minSize: 60,
    enableResizing: true,
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
    minSize: 30,
    enableResizing: true,
  }),
  columnHelper.accessor(row => row.transaction.payee, {
    id: 'payee',
    header: 'Payee',
    cell: ({ row, getValue }) => props.editable
      ? h('textarea', {
          value: getValue(),
          onInput: (e: any) => updateTransactionPayee(row.original.transaction, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm resize-none overflow-hidden',
          placeholder: 'Payee',
          rows: 1,
          style: 'min-height: 2rem; field-sizing: content;'
        })
      : h('div', { 
          style: 'width: 80px; border: 1px solid red; white-space: normal; word-wrap: break-word; background: yellow;'
        }, 'This is a very long text that should definitely wrap within this narrow container to test if wrapping works at all'),
    size: 100,
    minSize: 60,
    enableResizing: true,
  }),
  columnHelper.accessor(row => row.transaction.narration, {
    id: 'narration',
    header: 'Narration',
    cell: ({ row, getValue }) => props.editable
      ? h('textarea', {
          value: getValue(),
          onInput: (e: any) => updateTransactionNarration(row.original.transaction, e.target.value),
          class: 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 bg-yellow-50 dark:bg-gray-700 dark:text-white text-sm resize-none overflow-hidden',
          placeholder: 'Description',
          rows: 1,
          style: 'min-height: 2rem; field-sizing: content;'
        })
      : h('div', { 
          style: 'white-space: normal; word-wrap: break-word; overflow-wrap: break-word;',
          class: 'text-gray-900 dark:text-white text-sm'
        }, getValue()),
    size: 120,
    minSize: 80,
    enableResizing: true,
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
    minSize: 80,
    enableResizing: true,
  }),
  columnHelper.accessor('account', {
    header: 'Account',
    cell: ({ row, getValue }) => props.editable
      ? h(AccountDropdown, {
          modelValue: getValue(),
          'onUpdate:modelValue': (value: string) => updatePostingAccount(row.original.transaction, row.index, value),
          'custom-class': 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-800 dark:text-white text-sm',
          'allow-custom': false, // Don't allow creating new accounts from the table
          placeholder: 'Select account...'
        })
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 150,
    minSize: 100,
    enableResizing: true,
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
    minSize: 60,
    enableResizing: true,
  }),
  columnHelper.accessor('currency', {
    header: 'Currency',
    cell: ({ row, getValue }) => props.editable
      ? h(CommodityDropdown, {
          modelValue: getValue(),
          'onUpdate:modelValue': (value: string) => updatePostingCurrency(row.original.transaction, row.index, value),
          'custom-class': 'w-full border-0 focus:ring-1 focus:ring-blue-500 rounded px-1 py-1 dark:bg-gray-800 dark:text-white text-sm',
          'allow-custom': false, // Don't allow creating new commodities from the table
          placeholder: 'Select commodity...'
        })
      : h('span', { class: 'text-gray-900 dark:text-white text-sm' }, getValue()),
    size: 80,
    minSize: 50,
    enableResizing: true,
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
    minSize: 50,
    enableResizing: true,
  }),
]

const table = useVueTable({
  get data() { return currentPageTransactions.value },
  columns,
  state: {
    get globalFilter() { return globalFilter.value },
    get columnSizing() { return columnSizing.value },
  },
  onGlobalFilterChange: (filter: string) => {
    globalFilter.value = filter
  },
  onColumnSizingChange: (updater) => {
    columnSizing.value = typeof updater === 'function' ? updater(columnSizing.value) : updater
  },
  enableColumnResizing: true,
  columnResizeMode: 'onChange', // or 'onEnd' for performance
  getCoreRowModel: getCoreRowModel(),
  getFilteredRowModel: getFilteredRowModel(),
})

const onGlobalFilterChange = (e: Event) => {
  globalFilter.value = (e.target as HTMLInputElement).value
  currentPageIndex.value = 0  // Reset to first page when filter changes
}

watch(() => props.pageSize, (_size) => {
  currentPageIndex.value = 0  // Reset to first page when page size changes
})

onMounted(() => {
  currentPageIndex.value = 0
  originalTransactions.value = JSON.parse(JSON.stringify(props.transactions))
})

watch(() => props.transactions, (newTransactions) => {
  // Preserve the current page if possible when transactions change
  const preservedPageIndex = currentPageIndex.value
  originalTransactions.value = JSON.parse(JSON.stringify(newTransactions))
  
  // After the new pages are calculated, try to maintain the same page
  // Use nextTick to ensure computed properties are updated first
  nextTick(() => {
    if (preservedPageIndex < totalPages.value) {
      currentPageIndex.value = preservedPageIndex
    } else if (totalPages.value > 0) {
      // If the preserved page is no longer valid, go to the last valid page
      currentPageIndex.value = Math.max(0, totalPages.value - 1)
    } else {
      // If there are no pages, go to the first (0) page
      currentPageIndex.value = 0
    }
  })
}, { deep: true })

const totalAmount = computed(() => {
  let total = 0
  filteredTransactions.value.forEach(transaction => {
    transaction.postings.forEach(posting => {
      if (posting.amount) {
        total += posting.amount
      }
    })
  })
  return total.toFixed(2)
})

const unbalancedCount = computed(() => {
  return filteredTransactions.value.filter(t => !isTransactionBalanced(t)).length
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

// Utility function to preserve the current page after data changes
const preserveCurrentPage = () => {
  const preservedPageIndex = currentPageIndex.value
  nextTick(() => {
    if (preservedPageIndex < totalPages.value) {
      currentPageIndex.value = preservedPageIndex
    } else if (totalPages.value > 0) {
      currentPageIndex.value = Math.max(0, totalPages.value - 1)
    }
  })
}

  const updateTransactionDate = (transaction: TransactionViewModel, newDate: string) => {
    const updatedTransactions = [...props.transactions]
    const txIndex = findTransactionIndex(transaction)
    if (txIndex !== -1) {
      updatedTransactions[txIndex].date = newDate
      updatedTransactions[txIndex].meta.isModified = true
      emitUpdate(updatedTransactions)
      // After update, try to maintain the same page 
      preserveCurrentPage()
    }
  }

const updateTransactionFlag = (transaction: TransactionViewModel, newFlag: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].flag = newFlag
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const updateTransactionPayee = (transaction: TransactionViewModel, newPayee: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].payee = newPayee
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const updateTransactionNarration = (transaction: TransactionViewModel, newNarration: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].narration = newNarration
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
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
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const updatePostingAccount = (transaction: TransactionViewModel, postingIndex: number, newAccount: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex].account = newAccount
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const updatePostingAmount = (transaction: TransactionViewModel, postingIndex: number, newAmountStr: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex].amount = newAmountStr ? parseFloat(newAmountStr) : null
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const updatePostingCurrency = (transaction: TransactionViewModel, postingIndex: number, newCurrency: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings[postingIndex].currency = newCurrency
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const addPosting = (transaction: TransactionViewModel) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings.push({ account: '', amount: null, currency: 'USD' })
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const removePosting = (transaction: TransactionViewModel, postingIndex: number) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1 && updatedTransactions[txIndex].postings.length > 1) {
    updatedTransactions[txIndex].postings.splice(postingIndex, 1)
    updatedTransactions[txIndex].meta.isModified = true
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page 
    preserveCurrentPage()
  }
}

const removeTransaction = (transaction: TransactionViewModel) => {
  const updatedTransactions = props.transactions.filter(t => t.id !== transaction.id)
  emitUpdate(updatedTransactions)
  // After update, try to maintain the same page 
  preserveCurrentPage()
}

const resetToOriginal = () => {
  emitUpdate(JSON.parse(JSON.stringify(originalTransactions.value)))
}

const scrollToTable = () => {
  nextTick(() => {
    const container = document.querySelector('.transaction-table-container')
    if (container) {
      // Get the position relative to the top of the page
      const rect = container.getBoundingClientRect()
      const scrollTop = window.pageYOffset || document.documentElement.scrollTop
      const offsetTop = rect.top + scrollTop
      
      // Calculate offset to account for any fixed header (e.g., 80px for top navigation)
      // This can be adjusted based on the actual height of your app's navigation bar
      // Increased the offset to ensure buttons are fully visible below the top bar
      const offset = 130; // Adjust this value based on your app's header height + button height
      
      window.scrollTo({
        top: offsetTop - offset,
        behavior: 'smooth'
      })
    }
  })
}

defineExpose({
  resetToOriginal,
  scrollToTable,
  clearState: () => {
    originalTransactions.value = []
    currentPageIndex.value = 0
    emitUpdate([])
  }
})
</script>

<style>
/* No custom styles needed for hover as it's handled by Tailwind now */
.resize-handle {
  position: absolute;
  right: 0;
  top: 0;
  height: 100%;
  width: 5px;
  cursor: col-resize;
  background-color: transparent;
  border-right: 2px solid transparent;
  user-select: none;
  touch-action: none;
  z-index: 10;
}

.resize-handle:hover,
.resize-handle.resizing {
  border-right: 2px solid #3b82f6; /* Tailwind blue-500 */
  background-color: rgba(59, 130, 246, 0.1);
}

th {
  position: relative;
}

table {
  table-layout: fixed;
}

/* Table cell styles for fixed layout */
table {
  table-layout: fixed;
}

/* Allow dropdowns to overflow cell boundaries */
.transaction-table-container {
  position: relative;
  z-index: 1;
}

/* For table cells, we need to be careful about clipping contexts */
table.table-fixed td {
  /* Don't set overflow: hidden as it will clip dropdowns */
  overflow: visible;
  /* Make sure cells don't create a containing block for positioned elements that clips them */
}

/* Specific styles for payee and narration columns to ensure proper text wrapping */
table.table-fixed td[data-column-id="payee"],
table.table-fixed td[data-column-id="narration"] {
  white-space: normal !important;
  word-wrap: break-word !important;
  overflow-wrap: break-word !important;
  word-break: break-word !important;
  overflow: hidden !important; /* Hide overflow to prevent sliding behind borders */
  vertical-align: top !important;
}

table.table-fixed td[data-column-id="payee"] span,
table.table-fixed td[data-column-id="narration"] span,
table.table-fixed td[data-column-id="payee"] input,
table.table-fixed td[data-column-id="narration"] input {
  white-space: normal !important;
  word-wrap: break-word !important;
  overflow-wrap: break-word !important;
  word-break: break-word !important;
  width: 100% !important;
  box-sizing: border-box !important;
  padding: 2px !important;
  margin: 0 !important;
  display: block !important;
}

/* For input elements, we need different handling */
table.table-fixed td[data-column-id="payee"] input,
table.table-fixed td[data-column-id="narration"] input {
  white-space: nowrap !important; /* inputs should not wrap */
  overflow: hidden !important;
  text-overflow: ellipsis !important;
}

/* Ensure dropdowns render with high z-index */
td .account-dropdown,
td .commodity-dropdown {
  position: relative;
  z-index: 50 !important;
}
</style>
