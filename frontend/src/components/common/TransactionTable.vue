<template>
  <div class="transaction-table-container">
    <!-- Table Controls -->
    <div class="flex items-center justify-between mb-4 gap-4">
      <!-- Global search bar (when enabled) -->
      <div v-if="showSearch" class="flex-1 max-w-md">
        <div class="relative">
          <MagnifyingGlassIcon 
            class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" 
            aria-hidden="true" 
          />
          <input
            :value="globalFilter"
            @input="onGlobalFilterChange"
            type="text"
            placeholder="Search all transactions..."
            class="block w-full rounded-md border-0 py-1.5 pl-10 pr-3 text-gray-900 ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-blue-600 sm:text-sm dark:bg-white/5 dark:text-white dark:ring-white/10 dark:placeholder:text-gray-500 dark:focus:ring-blue-500"
          />
        </div>
      </div>

      <!-- Column visibility controls -->
      <ColumnVisibilityControl
        :column-visibility="columnVisibility"
        :all-columns="allColumns"
        :toggle-column-visibility="toggleColumnVisibility"
        :reset-to-defaults="resetToDefaults"
      />
    </div>

    <!-- Main table -->
    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full table-fixed">
          <!-- Table Header -->
          <thead class="bg-gray-50 dark:bg-gray-800/50">
            <tr v-for="headerGroup in table.getHeaderGroups()" :key="headerGroup.id">
              <th
                v-for="header in headerGroup.headers"
                :key="header.id"
                :style="{ width: `${header.getSize()}px` }"
                class="relative px-3 py-3 text-left text-xs font-semibold text-gray-900 dark:text-white border-r border-gray-200 dark:border-gray-700 last:border-r-0"
              >
                <FlexRender
                  :render="header.column.columnDef.header"
                  :props="header.getContext()"
                />
                <!-- Resize handle -->
                <div
                  v-if="header.column.getCanResize()"
                  class="resize-handle"
                  :class="{ 'resizing': header.column.getIsResizing() }"
                  @mousedown="(e) => header.getResizeHandler()(e)"
                  @touchstart="(e) => header.getResizeHandler()(e)"
                />
              </th>
            </tr>
          </thead>

          <!-- Table Body -->
          <tbody class="bg-white dark:bg-gray-900 divide-y divide-gray-200 dark:divide-gray-800">
            <template v-for="row in table.getRowModel().rows" :key="row.id">
              <tr
                :data-row="row.original.transactionIndex"
                :class="[
                  'transaction-row',
                  `transaction-${row.original.transaction.id}`,
                  getTransactionRowClasses(row.original)
                ]"
              >
                <template v-for="cell in row.getVisibleCells()" :key="cell.id">
                  <td
                    v-if="!shouldSkipCell(cell)"
                    :rowspan="getRowSpan(cell)"
                    :data-column-id="cell.column.id"
                    :data-row="row.original.transactionIndex"
                    :data-posting="row.original.postingIndex"
                    :class="getCellClasses(cell)"
                    @keydown.capture="(e) => handleCellKeydown(e, cell, row.original)"
                    @click="(e) => handleCellClick(e, cell, row.original)"
                  >
                    <FlexRender :render="cell.column.columnDef.cell" :props="cell.getContext()" />
                  </td>
                </template>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pagination controls -->
    <div v-if="pageSize > 0" class="flex items-center justify-between mt-6">
      <div class="text-sm text-gray-700 dark:text-gray-300">
        <template v-if="currentPageTransactionCount === 1">
          Showing transaction {{ firstEntryNumber }} of {{ filteredTransactions.length }}
        </template>
        <template v-else>
          Showing transactions {{ firstEntryNumber }} to {{ lastEntryNumber }} of {{ filteredTransactions.length }}
        </template>
      </div>
      <div class="flex items-center gap-2">
        <button
          @click="goToPreviousPage"
          :disabled="currentPageIndex === 0"
          class="inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-gray-400 dark:disabled:bg-gray-600 transition-colors"
        >
          <ChevronLeftIcon class="h-4 w-4 mr-1 flex-shrink-0" />
          <span class="whitespace-nowrap">Previous</span>
        </button>
        <span class="text-sm text-gray-700 dark:text-gray-300 px-3 whitespace-nowrap">
          Page {{ currentPageIndex + 1 }} of {{ totalPages }}
        </span>
        <button
          @click="goToNextPage"
          :disabled="currentPageIndex >= totalPages - 1"
          class="inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-gray-400 dark:disabled:bg-gray-600 transition-colors"
        >
          <span class="whitespace-nowrap">Next</span>
          <ChevronRightIcon class="h-4 w-4 ml-1 flex-shrink-0" />
        </button>
      </div>
    </div>
    
    <!-- Summary section (when enabled) -->
    <div v-if="showSummary" class="mt-6">
      <div class="card">
        <div class="p-6">
          <h3 class="text-lg font-semibold text-gray-900 dark:text-white mb-4">Transaction Summary</h3>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="text-center">
              <div class="text-3xl font-bold text-gray-900 dark:text-white">{{ props.transactions.length }}</div>
              <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">Total Transactions</div>
            </div>
            <div class="text-center">
              <div class="text-3xl font-bold text-gray-900 dark:text-white">{{ totalAmount }}</div>
              <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">Total Amount</div>
            </div>
            <div class="text-center">
              <div class="text-3xl font-bold text-red-600 dark:text-red-400">{{ unbalancedCount }}</div>
              <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">Unbalanced</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, h, nextTick } from 'vue'
import {
  useVueTable,
  createColumnHelper,
  getCoreRowModel,
  getFilteredRowModel,
  FlexRender,
} from '@tanstack/vue-table'
import { MagnifyingGlassIcon, ChevronLeftIcon, ChevronRightIcon } from '@heroicons/vue/20/solid'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import TransactionStatusIndicator from '@/components/common/TransactionStatusIndicator.vue'
import ColumnVisibilityControl from '@/components/common/ColumnVisibilityControl.vue'
import { useTableColumns } from '@/composables/useTableColumns'
import { useTableKeyboardNavigation } from '@/composables/useTableKeyboardNavigation'
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

// Composables
const {
  columnVisibility,
  columnSizing,
  allColumns,
  toggleColumnVisibility,
  resetToDefaults,
  setColumnWidth
} = useTableColumns()

const {
  currentCell,
  isNavigating,
  setCellFocus,
  handleKeyNavigation,
  initializeNavigation,
  getNextCell
} = useTableKeyboardNavigation()

// State
const originalTransactions = ref<TransactionViewModel[]>([])
const globalFilter = ref('')

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
interface TableRowData {
  transaction: TransactionViewModel
  transactionIndex: number
  postingIndex: number
  isFirstPosting: boolean
  isLastPosting: boolean
  account: string
  amount: number | null
  currency: string
}

const columnHelper = createColumnHelper<TableRowData>()

const spannedColumnIds = ['status', 'index', 'date', 'flag', 'payee', 'narration', 'tags_links']

const getRowSpan = (cell: Cell<any, any>) => {
  if (spannedColumnIds.includes(cell.column.id) && cell.row.original.isFirstPosting) {
    return cell.row.original.transaction.postings.length
  }
  return 1
}

const shouldSkipCell = (cell: Cell<any, any>) => {
  return spannedColumnIds.includes(cell.column.id) && !cell.row.original.isFirstPosting
}

// Helper functions for cell styling
const getEditableInputClasses = (extraClasses = '') => {
  return `w-full min-w-0 rounded-md border-0 py-1.5 px-3 bg-white text-gray-900 ring-0 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-blue-600 text-sm dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 dark:focus:ring-blue-500 ${extraClasses}`
}

const getDisplayClasses = () => {
  return 'text-gray-900 dark:text-white text-sm w-full min-w-0'
}

const columns = computed(() => {
  const getColumnConfig = (columnId: string) => {
    return allColumns.value.find(col => col.id === columnId)
  }

  const baseColumns = [
    columnHelper.display({
      id: 'status',
      header: 'Status',
      cell: ({ row }) => h(TransactionStatusIndicator, {
        transaction: row.original.transaction
      }),
      size: getColumnConfig('status')?.defaultWidth || 60,
      minSize: getColumnConfig('status')?.minWidth || 60,
      enableResizing: getColumnConfig('status')?.resizable || false,
    }),
    columnHelper.accessor('transactionIndex', {
      id: 'index',
      header: '#',
      cell: info => h('span', { class: getDisplayClasses() }, info.getValue()),
      size: getColumnConfig('index')?.defaultWidth || 50,
      minSize: getColumnConfig('index')?.minWidth || 40,
      enableResizing: getColumnConfig('index')?.resizable ?? true,
    }),
    columnHelper.accessor(row => row.transaction.date, {
      id: 'date',
      header: 'Date',
      cell: ({ row, getValue }) => props.editable
        ? h('input', {
            type: 'date',
            value: getValue(),
            onInput: (e: any) => updateTransactionDate(row.original.transaction, e.target.value),
            class: getEditableInputClasses()
          })
        : h('span', { class: getDisplayClasses() }, getValue()),
      size: getColumnConfig('date')?.defaultWidth || 120,
      minSize: getColumnConfig('date')?.minWidth || 100,
      enableResizing: getColumnConfig('date')?.resizable ?? true,
    }),
    columnHelper.accessor(row => row.transaction.flag, {
      id: 'flag',
      header: 'Flag',
      cell: ({ row, getValue }) => props.editable
        ? h('select', {
            value: getValue(),
            onChange: (e: any) => updateTransactionFlag(row.original.transaction, e.target.value),
            class: getEditableInputClasses()
          }, [
            h('option', { value: '*' }, '*'),
            h('option', { value: '!' }, '!')
          ])
        : h('span', { class: getDisplayClasses() }, getValue()),
      size: getColumnConfig('flag')?.defaultWidth || 60,
      minSize: getColumnConfig('flag')?.minWidth || 50,
      enableResizing: getColumnConfig('flag')?.resizable ?? true,
    }),
    columnHelper.accessor(row => row.transaction.payee, {
      id: 'payee',
      header: 'Payee',
      cell: ({ row, getValue }) => props.editable
        ? h('div', {
            contenteditable: true,
            innerHTML: getValue() || '',
            onInput: (e: any) => updateTransactionPayee(row.original.transaction, e.target.textContent),
            class: `${getEditableInputClasses()} min-h-[2.5rem] overflow-y-auto`,
            style: { minHeight: '2.5rem', maxHeight: '6rem' },
            'data-placeholder': 'Payee'
          })
        : h('div', {
            class: `${getDisplayClasses()} break-words overflow-hidden`
          }, getValue()),
      size: getColumnConfig('payee')?.defaultWidth || 150,
      minSize: getColumnConfig('payee')?.minWidth || 100,
      enableResizing: getColumnConfig('payee')?.resizable ?? true,
    }),
    columnHelper.accessor(row => row.transaction.narration, {
      id: 'narration',
      header: 'Narration',
      cell: ({ row, getValue }) => props.editable
        ? h('div', {
            contenteditable: true,
            innerHTML: getValue() || '',
            onInput: (e: any) => updateTransactionNarration(row.original.transaction, e.target.textContent),
            class: `${getEditableInputClasses()} h-full min-h-[2.5rem] overflow-y-auto`,
            style: { minHeight: '2.5rem' },
            'data-placeholder': 'Description'
          })
        : h('div', {
            class: `${getDisplayClasses()} break-words overflow-hidden`
          }, getValue()),
      size: getColumnConfig('narration')?.defaultWidth || 200,
      minSize: getColumnConfig('narration')?.minWidth || 120,
      enableResizing: getColumnConfig('narration')?.resizable ?? true,
    }),
    columnHelper.accessor(row => [...row.transaction.tags, ...row.transaction.links.map((l: string) => `^${l}`)].join(' '), {
      id: 'tags_links',
      header: 'Tags/Links',
      cell: ({ row, getValue }) => props.editable
        ? h('input', {
            type: 'text',
            value: getValue(),
            onInput: (e: any) => updateTransactionTagsLinks(row.original.transaction, e.target.value),
            class: getEditableInputClasses(),
            placeholder: '#tag ^link'
          })
        : h('span', { class: getDisplayClasses() }, getValue()),
      size: getColumnConfig('tags_links')?.defaultWidth || 150,
      minSize: getColumnConfig('tags_links')?.minWidth || 100,
      enableResizing: getColumnConfig('tags_links')?.resizable ?? true,
    }),
    columnHelper.accessor('account', {
      id: 'account',
      header: 'Account',
      cell: ({ row, getValue }) => props.editable
        ? h(AccountDropdown, {
            modelValue: getValue(),
            'onUpdate:modelValue': (value: string) => updatePostingAccount(row.original.transaction, row.original.postingIndex, value),
            'custom-class': '!text-sm !py-0.5 !px-1.5 !pr-8 !outline-0',
            'allow-custom': false,
            placeholder: 'Account...'
          })
        : h('span', { class: getDisplayClasses() }, getValue()),
      size: getColumnConfig('account')?.defaultWidth || 180,
      minSize: getColumnConfig('account')?.minWidth || 120,
      enableResizing: getColumnConfig('account')?.resizable ?? true,
    }),
    columnHelper.accessor('amount', {
      id: 'amount',
      header: 'Amount',
      cell: ({ row, getValue }) => {
        const amount = getValue()
        const amountColorClass = (amount ?? 0) > 0
          ? 'text-green-700 dark:text-green-400'
          : (amount ?? 0) < 0
          ? 'text-red-700 dark:text-red-400'
          : 'text-gray-700 dark:text-gray-300'

        return props.editable
          ? h('input', {
              type: 'number',
              step: '0.01',
              value: amount || '',
              onInput: (e: any) => updatePostingAmount(row.original.transaction, row.original.postingIndex, e.target.value),
              class: `${getEditableInputClasses('text-right')} ${amountColorClass}`
            })
          : h('span', {
              class: `${getDisplayClasses()} font-mono text-right block ${amountColorClass}`
            }, amount?.toFixed(2) || '')
      },
      size: getColumnConfig('amount')?.defaultWidth || 100,
      minSize: getColumnConfig('amount')?.minWidth || 80,
      enableResizing: getColumnConfig('amount')?.resizable ?? true,
    }),
    columnHelper.accessor('currency', {
      id: 'currency',
      header: 'Currency',
      cell: ({ row, getValue }) => props.editable
        ? h(CommodityDropdown, {
            modelValue: getValue(),
            'onUpdate:modelValue': (value: string) => updatePostingCurrency(row.original.transaction, row.original.postingIndex, value),
            'custom-class': '!text-sm !py-0.5 !px-1.5 !pr-8 !outline-0',
            'allow-custom': false,
            'show-details': false,
            placeholder: 'CURR'
          })
        : h('span', { class: getDisplayClasses() }, getValue()),
      size: getColumnConfig('currency')?.defaultWidth || 80,
      minSize: getColumnConfig('currency')?.minWidth || 60,
      enableResizing: getColumnConfig('currency')?.resizable ?? true,
    }),
    columnHelper.display({
      id: 'actions',
      header: 'Actions',
      cell: ({ row }) => {
        if (!props.editable) return null

        const buttons = []

        if (row.original.isFirstPosting) {
          buttons.push(
            h('button', {
              onClick: () => addPosting(row.original.transaction),
              class: 'inline-flex items-center justify-center w-6 h-6 text-green-600 hover:text-green-800 hover:bg-green-50 rounded text-sm dark:text-green-400 dark:hover:text-green-300 dark:hover:bg-green-900/20',
              title: 'Add posting'
            }, '+')
          )
          buttons.push(
            h('button', {
              onClick: () => removeTransaction(row.original.transaction),
              class: 'inline-flex items-center justify-center w-6 h-6 text-red-600 hover:text-red-800 hover:bg-red-50 rounded text-sm dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20',
              title: 'Remove transaction'
            }, '−')
          )
        }

        buttons.push(
          h('button', {
            onClick: () => removePosting(row.original.transaction, row.original.postingIndex),
            class: 'inline-flex items-center justify-center w-6 h-6 text-red-600 hover:text-red-800 hover:bg-red-50 rounded text-sm dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20',
            title: 'Remove posting'
          }, '×')
        )

        return h('div', { class: 'flex items-center justify-center gap-1' }, buttons)
      },
      size: getColumnConfig('actions')?.defaultWidth || 100,
      minSize: getColumnConfig('actions')?.minWidth || 80,
      enableResizing: getColumnConfig('actions')?.resizable || false,
    }),
  ]

  // Filter columns based on visibility settings
  return baseColumns.filter(column => {
    const columnId = column.id
    return columnId && columnVisibility.value[columnId] === true
  })
})

// Helper functions for styling
const getTransactionRowClasses = (rowData: any) => {
  const classes = []

  // Transaction grouping borders
  if (rowData.isFirstPosting) {
    classes.push('border-t-2', 'border-t-blue-200', 'dark:border-t-blue-800')
  }
  if (rowData.isLastPosting) {
    classes.push('border-b-2', 'border-b-blue-200', 'dark:border-b-blue-800')
  }

  return classes
}

const getCellClasses = (cell: Cell<any, any>) => {
  const classes = [
    'px-3', 'py-2', 'text-sm',
    'border-r', 'border-gray-200', 'dark:border-gray-700',
    'last:border-r-0'
  ]
  
  // Alignment
  if (['amount'].includes(cell.column.id)) {
    classes.push('text-right')
  }
  if (['actions'].includes(cell.column.id)) {
    classes.push('text-center')
  }
  
  // Vertical alignment for spanned cells
  if (getRowSpan(cell) > 1) {
    classes.push('align-top')
  }
  
  // Special styling for index column when spanned
  if (cell.column.id === 'index' && getRowSpan(cell) > 1) {
    classes.push('bg-gray-50', 'dark:bg-gray-800/50')
  }
  
  return classes
}

// Keyboard navigation
const handleCellKeydown = (event: KeyboardEvent, cell: any, rowData: any) => {
  const target = event.target as Element

  // Check if we're in a dropdown input (ComboboxInput from Headless UI)
  const isDropdownInput = target?.closest('[role="combobox"]') &&
                         (target.tagName === 'INPUT' || target.hasAttribute('contenteditable'))

  // For dropdown inputs, only handle Tab key for navigation, let everything else work normally
  if (isDropdownInput) {
    if (event.key === 'Tab') {
      // Only navigate with Tab, not other keys
      event.preventDefault()

      const position = {
        rowIndex: rowData.transactionIndex - 1,
        columnId: cell.column.id,
        postingIndex: ['account', 'amount', 'currency'].includes(cell.column.id) ? rowData.postingIndex : undefined
      }

      handleKeyNavigation(
        event,
        position,
        filteredTransactions.value.length,
        (rowIndex: number) => {
          const transaction = filteredTransactions.value[rowIndex]
          return transaction ? transaction.postings.length : 0
        }
      )
    }
    // For all other keys in dropdown inputs, let the dropdown handle them
    return
  }

  // For non-dropdown elements, handle navigation keys
  if (event.key !== 'Tab') {
    return
  }


  // For Tab navigation, prevent default and handle it
  if (event.key === 'Tab') {
    event.preventDefault()
  }

  // Determine current position based on the cell that triggered the event
  const position = {
    rowIndex: rowData.transactionIndex - 1, // Convert to 0-based index
    columnId: cell.column.id,
    postingIndex: ['account', 'amount', 'currency'].includes(cell.column.id) ? rowData.postingIndex : undefined
  }

  handleKeyNavigation(
    event,
    position,
    filteredTransactions.value.length,
    (rowIndex: number) => {
      const transaction = filteredTransactions.value[rowIndex]
      return transaction ? transaction.postings.length : 0
    }
  )
}

const handleCellClick = (event: Event, cell: any, rowData: any) => {
  // Only handle clicks on editable cells
  if (!isEditableColumn(cell.column.id)) {
    return
  }

  event.preventDefault()

  // Set focus to the clicked cell
  const position = {
    rowIndex: rowData.transactionIndex - 1,
    columnId: cell.column.id,
    postingIndex: ['account', 'amount', 'currency'].includes(cell.column.id) ? rowData.postingIndex : undefined
  }

  setCellFocus(position)
}

const isEditableColumn = (columnId: string) => {
  const editableColumns = ['date', 'flag', 'payee', 'narration', 'tags_links', 'account', 'amount', 'currency']
  return editableColumns.includes(columnId) && columnVisibility.value[columnId] === true
}

const table = useVueTable({
  get data() { return currentPageTransactions.value },
  get columns() { return columns.value },
  state: {
    get globalFilter() { return globalFilter.value },
    get columnSizing() { return columnSizing.value },
  },
  onGlobalFilterChange: (filter: string) => {
    globalFilter.value = filter
  },
  onColumnSizingChange: (updater) => {
    const newSizing = typeof updater === 'function' ? updater(columnSizing.value) : updater
    
    // Update our column widths store
    Object.keys(newSizing).forEach(columnId => {
      setColumnWidth(columnId, newSizing[columnId])
    })
  },
  enableColumnResizing: true,
  columnResizeMode: 'onChange',
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

  // Add global keyboard listener for table navigation initialization and pagination
  const handleGlobalKeydown = (event: KeyboardEvent) => {
    const target = event.target as Element

    // Only handle if we're inside the transaction table
    if (target?.closest('.transaction-table-container')) {
      // Handle pagination with Page Up/Page Down
      if (event.key === 'PageUp' && currentPageIndex.value > 0) {
        event.preventDefault()
        goToPreviousPage()
        return
      }

      if (event.key === 'PageDown' && currentPageIndex.value < totalPages.value - 1) {
        event.preventDefault()
        goToNextPage()
        return
      }
    }

    // Start navigation with F2 or when Tab is pressed on the table container
    if (event.key === 'F2' || (event.key === 'Tab' && target?.closest('.transaction-table-container'))) {
      if (filteredTransactions.value.length > 0 && !currentCell.value) {
        // Initialize navigation at the first visible editable cell
        const firstTransaction = filteredTransactions.value[0]
        const visibleEditableColumns = ['date', 'flag', 'payee', 'narration', 'tags_links', 'account', 'amount', 'currency']
          .filter(col => columnVisibility.value[col] === true)

        if (firstTransaction && visibleEditableColumns.length > 0) {
          const firstColumn = visibleEditableColumns[0]
          const initialPosition = {
            rowIndex: 0,
            columnId: firstColumn,
            postingIndex: ['account', 'amount', 'currency'].includes(firstColumn) ? 0 : undefined
          }
          setCellFocus(initialPosition)
          event.preventDefault()
        }
      }
    }
  }

  document.addEventListener('keydown', handleGlobalKeydown)

  // Cleanup on unmount
  onUnmounted(() => {
    document.removeEventListener('keydown', handleGlobalKeydown)
  })
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
  const totalInCents = transaction.postings.reduce((sum, posting) => {
    const amount = posting.amount || 0
    // Convert to cents to avoid floating point precision issues
    return sum + Math.round(amount * 100)
  }, 0)
  // Check if the total in cents is within 1 cent (the 0.01 threshold)
  return Math.abs(totalInCents) < 1
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

<style scoped>
/* Resize handle for column resizing */
.resize-handle {
  position: absolute;
  right: 0;
  top: 0;
  height: 100%;
  width: 4px;
  cursor: col-resize;
  background-color: transparent;
  user-select: none;
  touch-action: none;
  z-index: 10;
  border-right: 2px solid transparent;
  transition: all 0.2s ease;
}

.resize-handle:hover,
.resize-handle.resizing {
  border-right: 2px solid theme('colors.blue.500');
  background-color: theme('colors.blue.500 / 0.1');
}

/* Table styling */
.transaction-table-container {
  position: relative;
}

table {
  table-layout: fixed;
}

th {
  position: relative;
}

/* Text wrapping for content columns */
td[data-column-id="payee"],
td[data-column-id="narration"] {
  word-wrap: break-word;
  overflow-wrap: break-word;
  vertical-align: top;
  overflow: hidden;
}

/* Text content should fill the cell */
td[data-column-id="payee"] > *,
td[data-column-id="narration"] > * {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

/* Ensure dropdowns can escape table cell boundaries */
td[data-column-id="account"],
td[data-column-id="currency"] {
  overflow: visible;
}

/* Remove focus outline from table rows */
.transaction-row:focus {
  outline: none;
}

/* Enhanced focus indicators for input elements within cells */
td input:focus,
td select:focus,
td button:focus,
td [contenteditable]:focus {
  outline: 2px solid #3b82f6 !important;
  outline-offset: -1px;
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2);
}

.dark td input:focus,
.dark td select:focus,
.dark td button:focus,
.dark td [contenteditable]:focus {
  outline-color: #60a5fa !important;
  box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.2);
}

/* Ensure focused elements are visible above dropdowns */
td input:focus,
td select:focus,
td button:focus,
td [contenteditable]:focus {
  position: relative;
  z-index: 10;
}

/* Improved button hover states */
button:focus {
  outline: none;
  box-shadow: 0 0 0 2px #3b82f6, 0 0 0 4px rgba(59, 130, 246, 0.2);
}


/* Enhanced status column - removed for now as :has() has limited support */
td[data-column-id="status"] {
  position: sticky;
  left: 0;
  background-color: white;
  z-index: 10;
}

.dark td[data-column-id="status"] {
  background-color: #111827;
}

/* Contenteditable placeholder styling */
[contenteditable]:empty:before {
  content: attr(data-placeholder);
  color: #9ca3af;
  pointer-events: none;
}

.dark [contenteditable]:empty:before {
  color: #6b7280;
}

</style>
