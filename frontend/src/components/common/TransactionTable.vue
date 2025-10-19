<template>
  <div class="transaction-table-container">
    <!-- Delete Confirm Dialog -->
    <DeleteConfirmDialog
      :is-open="deleteConfirmDialog.isOpen.value"
      :title="deleteConfirmDialog.dialogOptions.value.title"
      :message="deleteConfirmDialog.dialogOptions.value.message"
      :confirm-text="deleteConfirmDialog.dialogOptions.value.confirmText"
      :cancel-text="deleteConfirmDialog.dialogOptions.value.cancelText"
      :variant="deleteConfirmDialog.dialogOptions.value.variant"
      @confirm="deleteConfirmDialog.handleConfirm"
      @cancel="deleteConfirmDialog.handleCancel"
      @close="deleteConfirmDialog.handleClose"
    />


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
    <div class="card">
      <div class="table-scroll-container">
        <table class="w-full table-auto">
          <!-- Table Header -->
          <thead class="bg-gray-50 dark:bg-gray-800/50">
            <tr v-for="headerGroup in table.getHeaderGroups()" :key="headerGroup.id">
              <th
                v-for="header in headerGroup.headers"
                :key="header.id"
                :data-column-id="header.id"
                :style="{ width: `${header.getSize()}px` }"
                class="relative px-3 py-3 text-left text-xs font-semibold text-gray-900 dark:text-white border-r border-b border-gray-200 dark:border-gray-700 last:border-r-0"
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
    <div v-if="showSummary" class="mt-4">
      <div class="card">
        <div class="px-4 py-3">
          <div class="flex items-center justify-around gap-6 text-sm">
            <div v-if="importContext" class="flex items-center gap-2">
              <span class="text-gray-600 dark:text-gray-400">Net Flow:</span>
              <span class="font-semibold text-gray-900 dark:text-white">
                <template v-if="Object.keys(netFlowByCurrency).length > 0">
                  <span v-for="(amount, currency, index) in netFlowByCurrency" :key="currency">
                    {{ amount }} {{ currency }}<span v-if="index < Object.keys(netFlowByCurrency).length - 1">, </span>
                  </span>
                </template>
                <template v-else>—</template>
              </span>
            </div>
            <div v-if="importContext" class="flex items-center gap-2">
              <span class="text-gray-600 dark:text-gray-400">Potential duplicates:</span>
              <button
                @click="handleDuplicateSummaryClick"
                :class="[
                  'font-semibold',
                  duplicateCount > 0
                    ? 'text-orange-600 dark:text-orange-400 hover:underline cursor-pointer'
                    : 'text-gray-900 dark:text-white cursor-default'
                ]"
                :disabled="duplicateCount === 0"
              >
                {{ duplicateCount }}
              </button>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-gray-600 dark:text-gray-400">Edited:</span>
              <span class="font-semibold text-gray-900 dark:text-white">{{ editedCount }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-gray-600 dark:text-gray-400">Unbalanced:</span>
              <span class="font-semibold" :class="unbalancedCount > 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-white'">{{ unbalancedCount }}</span>
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
import PriceTypeDropdown from '@/components/common/PriceTypeDropdown.vue'
import TransactionStatusIndicator from '@/components/common/TransactionStatusIndicator.vue'
import ColumnVisibilityControl from '@/components/common/ColumnVisibilityControl.vue'
import DeleteConfirmDialog from '@/components/common/DeleteConfirmDialog.vue'
import { useTableColumns } from '@/composables/useTableColumns'
import { useTableKeyboardNavigation } from '@/composables/useTableKeyboardNavigation'
import { useDeleteConfirmation } from '@/composables/useDeleteConfirmation'
import { useTransactionDeleter } from '@/composables/useTransactionDeleter'
import { useToast } from '@/composables/useNotifications'
import type { TransactionViewModel, ImportContext, LedgerContext, PostingViewModel } from '@/types/transactions'
import type { Cell } from '@tanstack/vue-table'

// Define the flattened row type used by the table
interface TableRowData extends PostingViewModel {
  transaction: TransactionViewModel
  postingIndex: number
  isFirstPosting: boolean
  isLastPosting: boolean
  transactionIndex: number
}

// Define props
interface Props {
  transactions: TransactionViewModel[]

  // Context-specific metadata (optional)
  importContext?: Map<string, ImportContext>
  ledgerContext?: Map<string, LedgerContext>

  showSearch?: boolean
  showColumnFilters?: boolean
  showTransactionGrouping?: boolean
  showSummary?: boolean
  editable?: boolean
  pageSize?: number
}

const props = withDefaults(defineProps<Props>(), {
  showSearch: false,
  showColumnFilters: false,
  showTransactionGrouping: true,
  showSummary: true,
  editable: true,
  pageSize: 25,
})

// Define emits
const emit = defineEmits<{
  (e: 'transactionsUpdated', transactions: TransactionViewModel[]): void
  (e: 'duplicateClick', transactionId: string): void
  (e: 'transactionDeleted', transactionId: string): void
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
  setCellFocus,
  handleKeyNavigation
} = useTableKeyboardNavigation()

const deleteConfirmDialog = useDeleteConfirmation()
const { deleteTransactions } = useTransactionDeleter()
const toast = useToast()

// State
// Two independent baselines for different purposes:
// 1. ofxOriginalTransactions: For Reset button (always returns to raw OFX data)
// 2. editBaselineTransactions: For ✏️ icon (updates after major operations like autocategorization)
const ofxOriginalTransactions = ref<TransactionViewModel[]>([])
const editBaselineTransactions = ref<TransactionViewModel[]>([])
const globalFilter = ref('')

// Helper functions to get context for a transaction
const getImportContext = (transactionId: string): ImportContext | undefined => {
  return props.importContext?.get(transactionId)
}

const getLedgerContext = (transactionId: string): LedgerContext | undefined => {
  return props.ledgerContext?.get(transactionId)
}

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

const spannedColumnIds = ['status', 'index', 'date', 'flag', 'payee', 'memo', 'narration', 'tags_links']

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
        transaction: row.original.transaction,
        importContext: getImportContext(row.original.transaction.id),
        ledgerContext: getLedgerContext(row.original.transaction.id),
        onDuplicateClick: (transactionId: string) => emit('duplicateClick', transactionId)
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
            class: getEditableInputClasses(),
            autocomplete: 'off'
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
            textContent: getValue() || '',
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
    columnHelper.accessor(row => row.transaction.memo, {
      id: 'memo',
      header: 'Memo',
      cell: ({ row, getValue }) => props.editable
        ? h('div', {
            contenteditable: true,
            textContent: getValue() || '',
            onInput: (e: any) => updateTransactionMemo(row.original.transaction, e.target.textContent),
            class: `${getEditableInputClasses()} min-h-[2.5rem] overflow-y-auto`,
            style: { minHeight: '2.5rem', maxHeight: '6rem' },
            'data-placeholder': 'Memo'
          })
        : h('div', {
            class: `${getDisplayClasses()} break-words overflow-hidden`
          }, getValue()),
      size: getColumnConfig('memo')?.defaultWidth || 150,
      minSize: getColumnConfig('memo')?.minWidth || 100,
      enableResizing: getColumnConfig('memo')?.resizable ?? true,
    }),
    columnHelper.accessor(row => row.transaction.narration, {
      id: 'narration',
      header: 'Narration',
      cell: ({ row, getValue }) => props.editable
        ? h('div', {
            contenteditable: true,
            textContent: getValue() || '',
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
            placeholder: '#tag ^link',
            autocomplete: 'off'
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
              class: `${getEditableInputClasses('text-right')} ${amountColorClass}`,
              autocomplete: 'off'
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
    // Cost Amount column
    columnHelper.accessor(
      (row: TableRowData) => row.cost?.amount,
      {
        id: 'cost_amount',
        header: 'Cost Amount',
        cell: ({ row, getValue }) => {
          const value = getValue()
          return props.editable
            ? h('input', {
                type: 'number',
                step: '0.01',
                value: value ?? '',
                onInput: (e: any) => updatePostingCostAmount(row.original.transaction, row.original.postingIndex, e.target.value),
                class: getEditableInputClasses('text-right'),
                autocomplete: 'off'
              })
            : h('span', {
                class: `${getDisplayClasses()} font-mono text-right block`
              }, value !== undefined && value !== null ? value.toFixed(2) : '')
        },
        size: getColumnConfig('cost_amount')?.defaultWidth || 120,
        minSize: getColumnConfig('cost_amount')?.minWidth || 80,
        enableResizing: getColumnConfig('cost_amount')?.resizable ?? true,
      }
    ),
    // Cost Currency column
    columnHelper.accessor(
      (row: TableRowData) => row.cost?.currency,
      {
        id: 'cost_currency',
        header: 'Cost Currency',
        cell: ({ row, getValue }) => props.editable
          ? h(CommodityDropdown, {
              modelValue: getValue() || '',
              'onUpdate:modelValue': (value: string) => updatePostingCostCurrency(row.original.transaction, row.original.postingIndex, value),
              'custom-class': '!text-sm !py-0.5 !px-1.5 !pr-8 !outline-0',
              'allow-custom': false,
              'show-details': false,
              placeholder: 'CURR'
            })
          : h('span', { class: getDisplayClasses() }, getValue() || ''),
        size: getColumnConfig('cost_currency')?.defaultWidth || 100,
        minSize: getColumnConfig('cost_currency')?.minWidth || 60,
        enableResizing: getColumnConfig('cost_currency')?.resizable ?? true,
      }
    ),
    // Cost Date column
    columnHelper.accessor(
      (row: TableRowData) => row.cost?.date,
      {
        id: 'cost_date',
        header: 'Cost Date',
        cell: ({ row, getValue }) => props.editable
          ? h('input', {
              type: 'date',
              value: getValue() || '',
              onInput: (e: any) => updatePostingCostDate(row.original.transaction, row.original.postingIndex, e.target.value),
              class: getEditableInputClasses(),
              autocomplete: 'off'
            })
          : h('span', { class: getDisplayClasses() }, getValue() || ''),
        size: getColumnConfig('cost_date')?.defaultWidth || 120,
        minSize: getColumnConfig('cost_date')?.minWidth || 100,
        enableResizing: getColumnConfig('cost_date')?.resizable ?? true,
      }
    ),
    // Price Amount column
    columnHelper.accessor(
      (row: TableRowData) => row.price?.amount,
      {
        id: 'price_amount',
        header: 'Price Amount',
        cell: ({ row, getValue }) => {
          const value = getValue()
          return props.editable
            ? h('input', {
                type: 'number',
                step: '0.01',
                value: value ?? '',
                onInput: (e: any) => updatePostingPriceAmount(row.original.transaction, row.original.postingIndex, e.target.value),
                class: getEditableInputClasses('text-right'),
                autocomplete: 'off'
              })
            : h('span', {
                class: `${getDisplayClasses()} font-mono text-right block`
              }, value !== undefined && value !== null ? value.toFixed(2) : '')
        },
        size: getColumnConfig('price_amount')?.defaultWidth || 120,
        minSize: getColumnConfig('price_amount')?.minWidth || 80,
        enableResizing: getColumnConfig('price_amount')?.resizable ?? true,
      }
    ),
    // Price Currency column
    columnHelper.accessor(
      (row: TableRowData) => row.price?.currency,
      {
        id: 'price_currency',
        header: 'Price Currency',
        cell: ({ row, getValue }) => props.editable
          ? h(CommodityDropdown, {
              modelValue: getValue() || '',
              'onUpdate:modelValue': (value: string) => updatePostingPriceCurrency(row.original.transaction, row.original.postingIndex, value),
              'custom-class': '!text-sm !py-0.5 !px-1.5 !pr-8 !outline-0',
              'allow-custom': false,
              'show-details': false,
              placeholder: 'CURR'
            })
          : h('span', { class: getDisplayClasses() }, getValue() || ''),
        size: getColumnConfig('price_currency')?.defaultWidth || 100,
        minSize: getColumnConfig('price_currency')?.minWidth || 60,
        enableResizing: getColumnConfig('price_currency')?.resizable ?? true,
      }
    ),
    // Price Type column
    columnHelper.accessor(
      (row: TableRowData) => row.price?.type,
      {
        id: 'price_type',
        header: 'Price Type',
        cell: ({ row, getValue }) => props.editable
          ? h(PriceTypeDropdown, {
              modelValue: getValue() || '',
              'onUpdate:modelValue': (value: string) => updatePostingPriceType(row.original.transaction, row.original.postingIndex, value),
              'custom-class': '!text-sm !py-0.5 !px-1.5 !pr-8 !outline-0',
              placeholder: 'Type'
            })
          : h('span', { class: getDisplayClasses() }, getValue() || ''),
        size: getColumnConfig('price_type')?.defaultWidth || 90,
        minSize: getColumnConfig('price_type')?.minWidth || 70,
        enableResizing: getColumnConfig('price_type')?.resizable ?? true,
      }
    ),
    columnHelper.display({
      id: 'balance',
      header: 'Balance',
      cell: ({ row }) => {
        // Balance column - currently not implemented
        // When implemented, this will show running balance from ledgerContext
        const ledgerInfo = getLedgerContext(row.original.transaction.id)
        const balance = ledgerInfo?.balance

        if (balance !== undefined) {
          return h('span', {
            class: `${getDisplayClasses()} font-mono text-right block`
          }, balance.toFixed(2))
        }
        return h('span', { class: 'text-gray-400 text-sm' }, '—')
      },
      size: getColumnConfig('balance')?.defaultWidth || 120,
      minSize: getColumnConfig('balance')?.minWidth || 100,
      enableResizing: getColumnConfig('balance')?.resizable ?? true,
    }),
    columnHelper.display({
      id: 'actions',
      header: 'Actions',
      cell: ({ row }) => {
        if (!props.editable) return null

        const buttons = []

        if (row.original.isFirstPosting) {
          // First posting: × (remove posting) + (add posting) − (remove transaction)
          buttons.push(
            h('button', {
              onClick: () => removePosting(row.original.transaction, row.original.postingIndex),
              class: 'inline-flex items-center justify-center w-6 h-6 text-red-600 hover:text-red-800 hover:bg-red-50 rounded text-sm dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20',
              title: 'Remove posting'
            }, '×')
          )
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
        } else {
          // Subsequent postings: only × (remove posting) in first position to align with first row
          buttons.push(
            h('button', {
              onClick: () => removePosting(row.original.transaction, row.original.postingIndex),
              class: 'inline-flex items-center justify-center w-6 h-6 text-red-600 hover:text-red-800 hover:bg-red-50 rounded text-sm dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20',
              title: 'Remove posting'
            }, '×')
          )
          // Add spacer elements to maintain alignment with first row
          buttons.push(
            h('div', { class: 'w-6 h-6' }) // Spacer for + button position
          )
          buttons.push(
            h('div', { class: 'w-6 h-6' }) // Spacer for − button position
          )
        }

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
    'border-r', 'border-b', 'border-gray-200', 'dark:border-gray-700',
    'last:border-r-0'
  ]
  
  // Alignment
  if (['amount', 'cost_amount', 'price_amount'].includes(cell.column.id)) {
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

  // Handle pagination with PageUp/PageDown
  // This needs to be handled here because dropdown components may prevent bubbling
  if (event.key === 'PageUp') {
    event.preventDefault()
    event.stopPropagation()
    if (currentPageIndex.value > 0) {
      // Save current position before changing page
      const postingColumns = ['account', 'amount', 'currency', 'cost_amount', 'cost_currency', 'cost_date', 'price_amount', 'price_currency', 'price_type', 'actions']
      const currentPosition = {
        // Position within the current page (0-based)
        rowIndex: rowData.transactionIndex - 1 - (firstEntryNumber.value - 1),
        columnId: cell.column.id,
        postingIndex: postingColumns.includes(cell.column.id) ? rowData.postingIndex : undefined
      }

      goToPreviousPage()

      // Restore focus after page changes
      nextTick(() => {
        const newPageTransactionCount = currentPageTransactionCount.value
        const targetRow = Math.min(currentPosition.rowIndex, newPageTransactionCount - 1)
        setCellFocus({
          rowIndex: firstEntryNumber.value - 1 + targetRow,
          columnId: currentPosition.columnId,
          postingIndex: currentPosition.postingIndex
        })
      })
    }
    return
  }

  if (event.key === 'PageDown') {
    event.preventDefault()
    event.stopPropagation()
    if (currentPageIndex.value < totalPages.value - 1) {
      // Save current position before changing page
      const postingColumns = ['account', 'amount', 'currency', 'cost_amount', 'cost_currency', 'cost_date', 'price_amount', 'price_currency', 'price_type', 'actions']
      const currentPosition = {
        // Position within the current page (0-based)
        rowIndex: rowData.transactionIndex - 1 - (firstEntryNumber.value - 1),
        columnId: cell.column.id,
        postingIndex: postingColumns.includes(cell.column.id) ? rowData.postingIndex : undefined
      }

      goToNextPage()

      // Restore focus after page changes
      nextTick(() => {
        const newPageTransactionCount = currentPageTransactionCount.value
        const targetRow = Math.min(currentPosition.rowIndex, newPageTransactionCount - 1)
        setCellFocus({
          rowIndex: firstEntryNumber.value - 1 + targetRow,
          columnId: currentPosition.columnId,
          postingIndex: currentPosition.postingIndex
        })
      })
    }
    return
  }

  // For dropdown columns (account, currency, cost_currency, price_currency, price_type), check if options list is visible
  const isDropdownColumn = ['account', 'currency', 'cost_currency', 'price_currency', 'price_type'].includes(cell.column.id)

  if (isDropdownColumn) {
    // Find the table cell
    const tableCell = target.closest('td')
    if (tableCell) {
      // Check if there's a ComboboxOptions (ul element) visible in this cell or nearby
      // The ul is rendered as a sibling to the input's container div
      const optionsList = tableCell.querySelector('ul')
      const isDropdownOpen = optionsList !== null

      // If dropdown is open, let it handle Up/Down/Enter/Escape (but not Alt+Arrow)
      if (isDropdownOpen && ['ArrowUp', 'ArrowDown', 'Enter', 'Escape'].includes(event.key) && !event.altKey) {
        return // Let dropdown handle these keys
      }
    }
  }

  // Handle arrow keys for navigation
  // Vertical: ArrowUp/ArrowDown (plain)
  // Horizontal: ArrowLeft/ArrowRight (with Alt modifier)
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    // For horizontal navigation, only handle if Alt is pressed
    if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && !event.altKey) {
      return // Let the browser handle cursor movement in text fields
    }

    // All posting-level columns need postingIndex
    const postingColumns = ['account', 'amount', 'currency', 'cost_amount', 'cost_currency', 'cost_date', 'price_amount', 'price_currency', 'price_type', 'actions']
    const position = {
      rowIndex: rowData.transactionIndex - 1,
      columnId: cell.column.id,
      postingIndex: postingColumns.includes(cell.column.id) ? rowData.postingIndex : undefined
    }

    // Get list of currently visible columns
    const visibleColumns = Object.keys(columnVisibility.value).filter(
      col => columnVisibility.value[col] === true
    )

    handleKeyNavigation(
      event,
      position,
      filteredTransactions.value.length,
      (rowIndex: number) => {
        const transaction = filteredTransactions.value[rowIndex]
        return transaction ? transaction.postings.length : 0
      },
      visibleColumns
    )
  }
}

const handleCellClick = (event: Event, cell: any, rowData: any) => {
  // Only handle clicks on editable cells
  if (!isEditableColumn(cell.column.id)) {
    return
  }

  // Don't prevent default for input elements that need browser behavior (e.g., date picker)
  const target = event.target as HTMLElement
  if (target.tagName === 'INPUT' && target.getAttribute('type') === 'date') {
    // Let the browser's native date picker open
    return
  }

  event.preventDefault()

  // Set focus to the clicked cell
  const postingColumns = ['account', 'amount', 'currency', 'cost_amount', 'cost_currency', 'cost_date', 'price_amount', 'price_currency', 'price_type', 'actions']
  const position = {
    rowIndex: rowData.transactionIndex - 1,
    columnId: cell.column.id,
    postingIndex: postingColumns.includes(cell.column.id) ? rowData.postingIndex : undefined
  }

  setCellFocus(position)
}

const isEditableColumn = (columnId: string) => {
  const editableColumns = ['date', 'flag', 'payee', 'narration', 'tags_links', 'account', 'amount', 'currency', 'actions']
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

    // Update sticky column positions after resize
    updateStickyColumnPositions()
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

// Update sticky columns when column visibility changes
watch(columnVisibility, () => {
  updateStickyColumnPositions()
}, { deep: true })

// Update sticky columns when page changes (DOM re-renders)
watch(currentPageIndex, () => {
  updateStickyColumnPositions()
})

// Update sticky column positions based on actual rendered widths
const updateStickyColumnPositions = () => {
  nextTick(() => {
    const statusCell = document.querySelector('th[data-column-id="status"]') as HTMLElement
    if (statusCell) {
      const statusWidth = statusCell.offsetWidth
      const indexCells = document.querySelectorAll('[data-column-id="index"]') as NodeListOf<HTMLElement>
      indexCells.forEach(cell => {
        cell.style.left = `${statusWidth}px`
      })
    }
  })
}

onMounted(() => {
  currentPageIndex.value = 0

  // Initialize both baselines with the initial props data
  ofxOriginalTransactions.value = JSON.parse(JSON.stringify(props.transactions))
  editBaselineTransactions.value = JSON.parse(JSON.stringify(props.transactions))

  // Set sticky column positions after initial render
  updateStickyColumnPositions()

  // Add global keyboard listener for table navigation initialization and pagination
  const handleGlobalKeydown = (event: KeyboardEvent) => {
    const target = event.target as Element

    // Only handle if we're inside the transaction table
    if (target?.closest('.transaction-table-container')) {
      // Handle pagination with Page Up/Page Down
      if (event.key === 'PageUp') {
        event.preventDefault()
        if (currentPageIndex.value > 0) {
          goToPreviousPage()
        }
        return
      }

      if (event.key === 'PageDown') {
        event.preventDefault()
        if (currentPageIndex.value < totalPages.value - 1) {
          goToNextPage()
        }
        return
      }
    }

    // Start navigation with F2
    if (event.key === 'F2') {
      if (filteredTransactions.value.length > 0) {
        // Initialize navigation at the first visible editable cell
        const firstTransaction = filteredTransactions.value[0]
        const visibleEditableColumns = ['date', 'flag', 'payee', 'narration', 'tags_links', 'account', 'amount', 'currency', 'actions']
          .filter(col => columnVisibility.value[col] === true)

        if (firstTransaction && visibleEditableColumns.length > 0) {
          const firstColumn = visibleEditableColumns[0]
          const initialPosition = {
            rowIndex: 0,
            columnId: firstColumn,
            postingIndex: ['account', 'amount', 'currency', 'actions'].includes(firstColumn) ? 0 : undefined
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

watch(() => props.transactions, () => {
  // Preserve the current page if possible when transactions change
  const preservedPageIndex = currentPageIndex.value

  // DO NOT update baselines here - they should only be updated:
  // - ofxOriginalTransactions: On mount (when OFX is first loaded)
  // - editBaselineTransactions: On mount, after Reset, or when parent calls setNewEditBaseline()

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

const netFlowByCurrency = computed(() => {
  const flows: Record<string, number> = {}

  filteredTransactions.value.forEach(transaction => {
    // Check if this transaction has source account metadata
    const sourceAccount = transaction.meta['source_account']

    if (sourceAccount) {
      // Find the posting for the source account
      const sourcePosting = transaction.postings.find(p => p.account === sourceAccount)
      if (sourcePosting && sourcePosting.amount !== null) {
        // Use the actual currency from the posting, not from metadata
        // This ensures net flow updates when user changes posting currency
        const currency = sourcePosting.currency
        if (!flows[currency]) {
          flows[currency] = 0
        }
        flows[currency] += sourcePosting.amount
      }
    }
  })

  // Format each flow to 2 decimal places
  const formatted: Record<string, string> = {}
  Object.keys(flows).forEach(currency => {
    formatted[currency] = flows[currency].toFixed(2)
  })
  return formatted
})

const editedCount = computed(() => {
  return filteredTransactions.value.filter(t => t.internal.isModified).length
})

const unbalancedCount = computed(() => {
  return filteredTransactions.value.filter(t => !isTransactionBalanced(t)).length
})

const duplicateCount = computed(() => {
  return filteredTransactions.value.filter(t => {
    const context = getImportContext(t.id)
    return context?.is_duplicate === true
  }).length
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

const handleDuplicateSummaryClick = () => {
  if (duplicateCount.value === 0) return

  // Find the first transaction with duplicates
  const firstDuplicateTransaction = filteredTransactions.value.find(t => {
    const context = getImportContext(t.id)
    return context?.is_duplicate === true
  })

  if (firstDuplicateTransaction) {
    emit('duplicateClick', firstDuplicateTransaction.id)
  }
}

const findTransactionIndex = (transaction: TransactionViewModel) => {
  return props.transactions.findIndex(t => t.id === transaction.id)
}

const emitUpdate = (updatedTransactions: TransactionViewModel[]) => {
  emit('transactionsUpdated', updatedTransactions)
}

// Helper function to check if a value is "empty" (undefined, null, empty string, or 0)
const isEmpty = (value: any): boolean => {
  return value === undefined || value === null || value === '' || value === 0
}

// Helper function to check if a cost object is empty
// A cost is empty if the amount is empty, regardless of currency/date
// (because a cost without an amount is meaningless)
const isCostEmpty = (cost: any): boolean => {
  if (!cost) return true
  return isEmpty(cost.amount)
}

// Helper function to check if a price object is empty
// A price is empty if the amount is empty, regardless of currency/type
// (because a price without an amount is meaningless)
const isPriceEmpty = (price: any): boolean => {
  if (!price) return true
  return isEmpty(price.amount)
}

// Helper function to check if metadata object is empty
const isMetaEmpty = (meta: any): boolean => {
  if (!meta) return true
  return Object.keys(meta).length === 0
}

// Check if a transaction has been modified compared to its edit baseline
// Note: This compares against editBaselineTransactions (which updates after autocategorization),
// not ofxOriginalTransactions (which is only used for the Reset button)
const checkIfModified = (transaction: TransactionViewModel): boolean => {
  const baseline = editBaselineTransactions.value.find(t => t.id === transaction.id)
  if (!baseline) {
    // If no baseline found, transaction is likely new or baseline hasn't been initialized yet
    return false
  }

  // Compare all editable fields
  if (transaction.date !== baseline.date) return true
  if (transaction.flag !== baseline.flag) return true
  if (transaction.payee !== baseline.payee) return true
  if (transaction.memo !== baseline.memo) return true
  if (transaction.narration !== baseline.narration) return true

  // Compare tags and links (arrays)
  if (transaction.tags.length !== baseline.tags.length) return true
  if (transaction.tags.some((tag, i) => tag !== baseline.tags[i])) return true
  if (transaction.links.length !== baseline.links.length) return true
  if (transaction.links.some((link, i) => link !== baseline.links[i])) return true

  // Compare postings
  if (transaction.postings.length !== baseline.postings.length) return true
  for (let i = 0; i < transaction.postings.length; i++) {
    const posting = transaction.postings[i]
    const baselinePosting = baseline.postings[i]
    if (posting.account !== baselinePosting.account) return true
    if (posting.amount !== baselinePosting.amount) return true
    if (posting.currency !== baselinePosting.currency) return true

    // Compare cost fields (treat empty cost as equivalent to undefined)
    const costEmpty = isCostEmpty(posting.cost)
    const baselineCostEmpty = isCostEmpty(baselinePosting.cost)

    if (!costEmpty || !baselineCostEmpty) {
      // At least one has a non-empty cost, so compare them
      if (costEmpty !== baselineCostEmpty) return true
      if (!costEmpty && !baselineCostEmpty) {
        if (posting.cost!.amount !== baselinePosting.cost!.amount) return true
        if (posting.cost!.currency !== baselinePosting.cost!.currency) return true
        if (posting.cost!.date !== baselinePosting.cost!.date) return true
      }
    }

    // Compare price fields (treat empty price as equivalent to undefined)
    const priceEmpty = isPriceEmpty(posting.price)
    const baselinePriceEmpty = isPriceEmpty(baselinePosting.price)
    if (!priceEmpty || !baselinePriceEmpty) {
      // At least one has a non-empty price, so compare them
      if (priceEmpty !== baselinePriceEmpty) return true
      if (!priceEmpty && !baselinePriceEmpty) {
        if (posting.price!.amount !== baselinePosting.price!.amount) return true
        if (posting.price!.currency !== baselinePosting.price!.currency) return true
        if (posting.price!.type !== baselinePosting.price!.type) return true
      }
    }

    // Compare posting metadata (treat empty meta as equivalent to undefined)
    const metaEmpty = isMetaEmpty(posting.meta)
    const baselineMetaEmpty = isMetaEmpty(baselinePosting.meta)
    if (!metaEmpty || !baselineMetaEmpty) {
      // At least one has non-empty metadata, so compare them
      if (metaEmpty !== baselineMetaEmpty) return true
      if (!metaEmpty && !baselineMetaEmpty) {
        const metaKeys = Object.keys(posting.meta!)
        const baselineMetaKeys = Object.keys(baselinePosting.meta!)
        if (metaKeys.length !== baselineMetaKeys.length) return true
        for (const key of metaKeys) {
          if (posting.meta![key] !== baselinePosting.meta![key]) return true
        }
      }
    }
  }

  return false // No modifications detected
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
      updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page
    preserveCurrentPage()
  }
}

const updateTransactionMemo = (transaction: TransactionViewModel, newMemo: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].memo = newMemo || undefined
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page
    preserveCurrentPage()
  }
}

const updatePostingCostAmount = (transaction: TransactionViewModel, postingIndex: number, newAmountStr: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    const posting = updatedTransactions[txIndex].postings[postingIndex]
    if (!posting.cost) {
      posting.cost = {}
    }
    posting.cost.amount = newAmountStr ? parseFloat(newAmountStr) : undefined
    // Default cost date to transaction date when user starts entering cost
    if (posting.cost.amount !== undefined && !posting.cost.date) {
      posting.cost.date = updatedTransactions[txIndex].date
    }
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    preserveCurrentPage()
  }
}

const updatePostingCostCurrency = (transaction: TransactionViewModel, postingIndex: number, newCurrency: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    const posting = updatedTransactions[txIndex].postings[postingIndex]
    if (!posting.cost) {
      posting.cost = {}
    }
    posting.cost.currency = newCurrency || undefined
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    preserveCurrentPage()
  }
}

const updatePostingCostDate = (transaction: TransactionViewModel, postingIndex: number, newDate: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    const posting = updatedTransactions[txIndex].postings[postingIndex]
    if (!posting.cost) {
      posting.cost = {}
    }
    posting.cost.date = newDate || undefined
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    preserveCurrentPage()
  }
}

const updatePostingPriceAmount = (transaction: TransactionViewModel, postingIndex: number, newAmountStr: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    const posting = updatedTransactions[txIndex].postings[postingIndex]
    if (!posting.price) {
      posting.price = {}
    }
    posting.price.amount = newAmountStr ? parseFloat(newAmountStr) : undefined
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    preserveCurrentPage()
  }
}

const updatePostingPriceCurrency = (transaction: TransactionViewModel, postingIndex: number, newCurrency: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    const posting = updatedTransactions[txIndex].postings[postingIndex]
    if (!posting.price) {
      posting.price = {}
    }
    posting.price.currency = newCurrency || undefined
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    preserveCurrentPage()
  }
}

const updatePostingPriceType = (transaction: TransactionViewModel, postingIndex: number, newType: string) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    const posting = updatedTransactions[txIndex].postings[postingIndex]
    if (!posting.price) {
      posting.price = {}
    }
    posting.price.type = (newType as '@' | '@@') || undefined
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    preserveCurrentPage()
  }
}

const addPosting = (transaction: TransactionViewModel) => {
  const updatedTransactions = [...props.transactions]
  const txIndex = findTransactionIndex(transaction)
  if (txIndex !== -1) {
    updatedTransactions[txIndex].postings.push({
      account: '',
      amount: null,
      currency: 'USD',
      cost: undefined,
      price: undefined,
      meta: undefined
    })
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
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
    updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
    emitUpdate(updatedTransactions)
    // After update, try to maintain the same page
    preserveCurrentPage()
  }
}

const removeTransaction = async (transaction: TransactionViewModel) => {
  // Check if we're in import context (transactions not yet in ledger)
  const isImportContext = props.importContext !== undefined

  // Build confirmation message based on context
  const message = isImportContext
    ? `Are you sure you want to remove this transaction from the import?

Date: ${transaction.date}
Payee: ${transaction.payee}
Narration: ${transaction.narration}

This will only remove it from the current import list.`
    : `Are you sure you want to delete this transaction?

Date: ${transaction.date}
Payee: ${transaction.payee}
Narration: ${transaction.narration}

This action will immediately update the ledger and cannot be undone.`

  // Show confirmation dialog
  const confirmed = await deleteConfirmDialog.showConfirm({
    title: isImportContext ? 'Remove Transaction?' : 'Delete Transaction?',
    message: message,
    confirmText: isImportContext ? 'Remove' : 'Delete',
    cancelText: 'Cancel',
    variant: 'danger'
  })

  if (!confirmed) return

  try {
    // If in ledger context, call backend to delete from ledger
    if (!isImportContext) {
      await deleteTransactions([transaction.id])
    }

    // Remove from frontend array
    const updatedTransactions = props.transactions.filter(t => t.id !== transaction.id)
    emitUpdate(updatedTransactions)

    // After update, try to maintain the same page
    preserveCurrentPage()

    // Show success toast
    if (isImportContext) {
      toast.success(
        'Transaction Removed',
        'Transaction has been removed from the import list'
      )
    } else {
      toast.success(
        'Transaction Deleted',
        'Transaction has been removed from the ledger'
      )
      // Emit deletion event so parent can update totalCount (only for ledger context)
      emit('transactionDeleted', transaction.id)
    }
  } catch (error: any) {
    // Show error toast
    toast.error(
      isImportContext ? 'Remove Failed' : 'Delete Failed',
      error.message || 'Failed to remove transaction. Please try again.'
    )
  }
}

const resetToOriginal = () => {
  // Reset to OFX original data (not to autocategorized data)
  const ofxCopy = JSON.parse(JSON.stringify(ofxOriginalTransactions.value))
  emitUpdate(ofxCopy)

  // After reset, the edit baseline also goes back to OFX original
  // (so any subsequent edits will be compared against OFX data)
  editBaselineTransactions.value = JSON.parse(JSON.stringify(ofxOriginalTransactions.value))
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

  // Called by parent after major operations (e.g., autocategorization) to establish new edit baseline
  // This prevents autocategorized transactions from showing the ✏️ icon
  setNewEditBaseline: () => {
    editBaselineTransactions.value = JSON.parse(JSON.stringify(props.transactions))
  },

  // Called by parent when it regenerates transactions (e.g., parent's resetTable)
  // This reinitializes BOTH baselines with the new transaction objects/IDs
  reinitializeBaselines: () => {
    ofxOriginalTransactions.value = JSON.parse(JSON.stringify(props.transactions))
    editBaselineTransactions.value = JSON.parse(JSON.stringify(props.transactions))
  },

  clearState: () => {
    ofxOriginalTransactions.value = []
    editBaselineTransactions.value = []
    currentPageIndex.value = 0
    emitUpdate([])
  }
})
</script>

<style scoped>
/* Scrolling container - fixed height with internal scrolling */
.table-scroll-container {
  overflow-x: auto;
  overflow-y: auto;
  max-height: 600px; /* Fixed viewport height - adjust as needed */
}

/* Let the browser's native scrollbar work naturally */
.table-scroll-container::-webkit-scrollbar {
  height: 12px;
  width: 12px;
}

/* Basic, natural scrollbar styling */
.table-scroll-container::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 6px;
}

.table-scroll-container::-webkit-scrollbar-thumb {
  background: #888;
  border-radius: 6px;
}

/* Webkit browsers scrollbar with dark mode support */
.table-scroll-container::-webkit-scrollbar {
  height: 12px;
  -webkit-appearance: none;
}

/* Clean scrollbar styling that works in both light and dark modes */
.table-scroll-container::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 10px;
  border: 1px solid #e5e7eb;
}

.table-scroll-container::-webkit-scrollbar-thumb {
  background: #6b7280;
  border-radius: 10px;
  border: 2px solid #f1f1f1;
}

.table-scroll-container::-webkit-scrollbar-thumb:hover {
  background: #4b5563;
}

/* Dark mode scrollbar styling - makes them darker and more integrated */
.dark .table-scroll-container::-webkit-scrollbar-track {
  background: #1f2937 !important; /* Tailwind gray-900 */
  border-color: #374151 !important; /* Tailwind gray-800 */
}

.dark .table-scroll-container::-webkit-scrollbar-thumb {
  background: #4b5563 !important; /* Tailwind gray-600 */
  border-color: #374151 !important; /* Tailwind gray-800 */
}

.dark .table-scroll-container::-webkit-scrollbar-thumb:hover {
  background: #6b7280 !important; /* Tailwind gray-500 */
}

/* Ensure table doesn't compress columns too much */
.table-scroll-container table {
  min-width: 100%;
  width: max-content; /* Allow table to grow wider than container */
}

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
  table-layout: auto;
  border-collapse: separate;
  border-spacing: 0; /* Remove gaps between cells */
}

/* Sticky header - stays visible when scrolling within table container */
thead {
  position: sticky;
  top: 0; /* Stick to top of scroll container */
  z-index: 20; /* Above table content, below dropdowns */
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


/* Sticky Status column (leftmost) */
th[data-column-id="status"],
td[data-column-id="status"] {
  position: sticky;
  left: 0;
  z-index: 10;
}

th[data-column-id="status"] {
  background-color: rgb(249 250 251); /* bg-gray-50 */
}

td[data-column-id="status"] {
  background-color: white;
}

.dark th[data-column-id="status"] {
  background-color: rgba(31, 41, 55, 0.5); /* dark:bg-gray-800/50 */
}

.dark td[data-column-id="status"] {
  background-color: #111827; /* dark:bg-gray-900 */
}

/* Sticky Index (#) column (second from left) */
th[data-column-id="index"],
td[data-column-id="index"] {
  position: sticky;
  left: 0; /* Will be set dynamically by JavaScript based on Status column width */
  z-index: 10;
}

/* Add padding to index content to match input field alignment */
td[data-column-id="index"] > * {
  display: inline-block;
  padding-top: 0.375rem; /* py-1.5 to match input fields */
  padding-bottom: 0.375rem;
}

th[data-column-id="index"] {
  background-color: rgb(249 250 251); /* bg-gray-50 */
}

td[data-column-id="index"] {
  background-color: white;
}

.dark th[data-column-id="index"] {
  background-color: rgba(31, 41, 55, 0.5); /* dark:bg-gray-800/50 */
}

.dark td[data-column-id="index"] {
  background-color: #111827; /* dark:bg-gray-900 */
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
