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
            type="search"
            placeholder="Search all transactions..."
            class="block w-full rounded-md bg-white py-1.5 pl-10 pr-3 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
          />
        </div>
      </div>

      <!-- Right side: transaction count and column visibility controls -->
      <div class="flex items-center gap-4">
        <div class="text-sm text-gray-700 dark:text-gray-300">
          Showing {{ filteredTransactions.length }} {{ filteredTransactions.length === 1 ? 'transaction' : 'transactions' }}
        </div>

        <ColumnVisibilityControl
          :column-visibility="columnVisibility"
          :all-columns="allColumns"
          :toggle-column-visibility="toggleColumnVisibility"
          :reset-to-defaults="resetToDefaults"
          :align="columnControlAlign"
        />
      </div>
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
                class="relative px-3 py-3 text-left text-xs font-semibold text-gray-900 dark:text-white border-r border-b border-gray-200 dark:border-white/10 last:border-r-0"
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

          <!-- Account sums by currency -->
          <div v-if="Object.keys(accountSumsByCurrency).length > 0" class="mt-3 pt-3 border-t border-gray-200 dark:border-white/10">
            <div class="flex flex-wrap justify-center items-center gap-x-6 gap-y-2 text-sm">
              <span class="text-gray-500 dark:text-gray-400 font-medium">Account Totals:</span>
              <div v-for="(currencies, account) in accountSumsByCurrency" :key="account" class="flex items-center gap-2">
                <span class="text-gray-600 dark:text-gray-400">{{ account }}:</span>
                <span class="font-semibold text-gray-900 dark:text-white">
                  <span v-for="(amount, currency, index) in currencies" :key="currency">
                    {{ amount.toFixed(2) }} {{ currency }}<span v-if="index < Object.keys(currencies).length - 1">, </span>
                  </span>
                </span>
              </div>
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
  FlexRender,
} from '@tanstack/vue-table'
import { MagnifyingGlassIcon } from '@heroicons/vue/20/solid'
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
import { isTransactionBalanced } from '@/utils/transactions'
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
  columnControlAlign?: 'left' | 'right'
}

const props = withDefaults(defineProps<Props>(), {
  showSearch: false,
  showColumnFilters: false,
  showTransactionGrouping: true,
  showSummary: true,
  editable: true,
  columnControlAlign: 'left',
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
// 1. importedTransactions: For Reset button (always returns to originally imported data)
// 2. editBaselineTransactions: For ✏️ icon (updates after major operations like autocategorization)
const importedTransactions = ref<TransactionViewModel[]>([])
const editBaselineTransactions = ref<TransactionViewModel[]>([])
const globalFilter = ref('')

// Track raw input strings for numeric fields to preserve trailing dots/zeros during editing.
// Without this, typing "12.0" gets parsed to 12 by parseFloat, Vue re-renders value=12,
// and the user can never type "12.03". The raw string is stored during active editing
// and cleared on blur so the display normalizes.
const rawAmountStrings = ref<Record<string, string>>({})

const numericInputProps = (
  txId: string, postingIdx: number, field: string,
  currentValue: number | null | undefined,
  updateFn: (raw: string) => void,
  extraClasses: string = ''
) => {
  const key = `${txId}-${postingIdx}-${field}`
  const rawStr = rawAmountStrings.value[key]
  const fallback = currentValue !== null && currentValue !== undefined ? String(currentValue) : ''
  return {
    type: 'text',
    inputmode: 'decimal',
    value: rawStr !== undefined ? rawStr : fallback,
    onInput: (e: any) => {
      const raw: string = e.target.value
      if (raw !== '' && !/^-?\d*\.?\d*$/.test(raw)) {
        e.target.value = rawAmountStrings.value[key] ?? fallback
        return
      }
      rawAmountStrings.value[key] = raw
      updateFn(raw)
    },
    onBlur: () => { delete rawAmountStrings.value[key] },
    class: extraClasses,
    autocomplete: 'off'
  }
}

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



// Flatten transactions into table rows
const currentPageTransactions = computed(() => {
  const result = []
  let transactionCounter = 0

  for (const transaction of filteredTransactions.value) {
    transactionCounter++
    const postings = transaction.postings

    // Add all postings for this transaction
    const transactionRows = postings.map((posting, index) => ({
      ...posting,
      transaction,
      postingIndex: index,
      isFirstPosting: index === 0,
      isLastPosting: index === postings.length - 1,
      transactionIndex: transactionCounter,
    }))

    result.push(...transactionRows)
  }

  return result
})

// Column definitions
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
  return `w-full min-w-0 rounded-md bg-white py-1.5 px-3 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500 ${extraClasses}`
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
        ? h('input', {
            type: 'text',
            value: getValue(),
            onChange: (e: any) => updateTransactionFlag(row.original.transaction, e.target.value),
            class: getEditableInputClasses()
          })
        : h('span', { class: getDisplayClasses() }, getValue()),
      size: getColumnConfig('flag')?.defaultWidth || 60,
      minSize: getColumnConfig('flag')?.minWidth || 50,
      enableResizing: getColumnConfig('flag')?.resizable ?? true,
    }),
    columnHelper.accessor(row => row.transaction.payee, {
      id: 'payee',
      header: 'Payee',
      cell: ({ row, getValue }) => props.editable
        ? h('textarea', {
            value: getValue() || '',
            onInput: (e: any) => updateTransactionPayee(row.original.transaction, e.target.value),
            class: `${getEditableInputClasses()} resize-none overflow-y-auto h-full`,
            style: { width: '100%', minHeight: '2.5rem', boxSizing: 'border-box', display: 'block' },
            placeholder: 'Payee'
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
        ? h('textarea', {
            value: getValue() || '',
            onInput: (e: any) => updateTransactionMemo(row.original.transaction, e.target.value),
            class: `${getEditableInputClasses()} resize-none overflow-y-auto h-full`,
            style: { width: '100%', minHeight: '2.5rem', boxSizing: 'border-box', display: 'block' },
            placeholder: 'Memo'
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
        ? h('textarea', {
            value: getValue() || '',
            onInput: (e: any) => updateTransactionNarration(row.original.transaction, e.target.value),
            class: `${getEditableInputClasses()} resize-none overflow-y-auto h-full`,
            style: { width: '100%', minHeight: '2.5rem', boxSizing: 'border-box', display: 'block' },
            placeholder: 'Description'
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

        if (props.editable) {
          return h('input', numericInputProps(
            row.original.transaction.id, row.original.postingIndex, 'amount',
            amount,
            (raw) => updatePostingAmount(row.original.transaction, row.original.postingIndex, raw),
            `${getEditableInputClasses('text-right')} ${amountColorClass}`
          ))
        }
        return h('span', {
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
          if (props.editable) {
            return h('input', numericInputProps(
              row.original.transaction.id, row.original.postingIndex, 'cost_amount',
              value,
              (raw) => updatePostingCostAmount(row.original.transaction, row.original.postingIndex, raw),
              getEditableInputClasses('text-right')
            ))
          }
          return h('span', {
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
          if (props.editable) {
            return h('input', numericInputProps(
              row.original.transaction.id, row.original.postingIndex, 'price_amount',
              value,
              (raw) => updatePostingPriceAmount(row.original.transaction, row.original.postingIndex, raw),
              getEditableInputClasses('text-right')
            ))
          }
          return h('span', {
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
    classes.push('border-t-2', 'border-t-indigo-200', 'dark:border-t-indigo-800')
  }
  if (rowData.isLastPosting) {
    classes.push('border-b-2', 'border-b-indigo-200', 'dark:border-b-indigo-800')
  }

  return classes
}

const getCellClasses = (cell: Cell<any, any>) => {
  const classes = [
    'px-3', 'py-2', 'text-sm',
    'border-r', 'border-b', 'border-gray-200', 'dark:border-white/10',
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
    get columnSizing() { return columnSizing.value },
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
})

const onGlobalFilterChange = (e: Event) => {
  globalFilter.value = (e.target as HTMLInputElement).value
}

onMounted(() => {
  // Initialize both baselines with the initial props data
  importedTransactions.value = JSON.parse(JSON.stringify(props.transactions))
  editBaselineTransactions.value = JSON.parse(JSON.stringify(props.transactions))

  // Add global keyboard listener for table navigation initialization
  const handleGlobalKeydown = (event: KeyboardEvent) => {
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
  // DO NOT update baselines here - they should only be updated:
  // - importedTransactions: On mount (when data is first loaded)
  // - editBaselineTransactions: On mount, after Reset, or when parent calls setNewEditBaseline()
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

// Calculate sums by top-level account and currency
const accountSumsByCurrency = computed(() => {
  // Structure: { "Expenses": { "USD": 1250.00, "EUR": 300.00 }, "Assets": { "USD": -1250.00 } }
  const sums: Record<string, Record<string, number>> = {}

  filteredTransactions.value.forEach(transaction => {
    transaction.postings.forEach(posting => {
      if (posting.amount === null) return

      // Extract top-level account (everything before first ':')
      const topLevelAccount = posting.account.split(':')[0]
      const currency = posting.currency

      if (!sums[topLevelAccount]) {
        sums[topLevelAccount] = {}
      }
      if (!sums[topLevelAccount][currency]) {
        sums[topLevelAccount][currency] = 0
      }

      sums[topLevelAccount][currency] += posting.amount
    })
  })

  return sums
})

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
// not importedTransactions (which is only used for the Reset button)
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

const updateTransactionDate = (transaction: TransactionViewModel, newDate: string) => {
    const updatedTransactions = [...props.transactions]
    const txIndex = findTransactionIndex(transaction)
    if (txIndex !== -1) {
      updatedTransactions[txIndex].date = newDate
      updatedTransactions[txIndex].internal.isModified = checkIfModified(updatedTransactions[txIndex])
      emitUpdate(updatedTransactions)
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

    // Show success toast and emit event (only for ledger context)
    if (!isImportContext) {
      toast.success(
        'Transaction Deleted',
        'Transaction has been removed from the ledger'
      )
      // Emit deletion event so parent can update totalCount
      emit('transactionDeleted', transaction.id)
    }
    // In import context: no toast needed - visual feedback is sufficient
  } catch (error: any) {
    // Show error toast
    toast.error(
      isImportContext ? 'Remove Failed' : 'Delete Failed',
      error.message || 'Failed to remove transaction. Please try again.'
    )
  }
}

const resetToOriginal = () => {
  // Reset to originally imported data (not to autocategorized data)
  const copy = JSON.parse(JSON.stringify(importedTransactions.value))
  emitUpdate(copy)

  // After reset, the edit baseline also goes back to the originally imported data
  // (so any subsequent edits will be compared against the imported data)
  editBaselineTransactions.value = JSON.parse(JSON.stringify(importedTransactions.value))
}

const scrollToTable = () => {
  nextTick(() => {
    const container = document.querySelector('.transaction-table-container')
    if (container) {
      container.scrollIntoView({ behavior: 'smooth', block: 'start' })
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
    importedTransactions.value = JSON.parse(JSON.stringify(props.transactions))
    editBaselineTransactions.value = JSON.parse(JSON.stringify(props.transactions))
  },

  clearState: () => {
    importedTransactions.value = []
    editBaselineTransactions.value = []
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
  will-change: transform; /* Promote to GPU compositing layer for smooth scroll with sticky elements */
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
  transition: border-right 0.2s ease, background-color 0.2s ease;
}

.resize-handle:hover,
.resize-handle.resizing {
  border-right: 2px solid var(--color-indigo-500);
  background-color: color-mix(in srgb, var(--color-indigo-500) 10%, transparent);
}

/* Table styling */
.transaction-table-container {
  position: relative;
  scroll-margin-top: 130px; /* offset for fixed nav + buttons above table */
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
  background-color: rgb(249 250 251); /* Match th bg-gray-50 so no content bleeds through during scroll */
}

.dark thead {
  background-color: rgba(31, 41, 55, 0.5); /* Match dark:bg-gray-800/50 */
}

th {
  position: relative;
}

/* Text wrapping for content columns */
td[data-column-id="payee"],
td[data-column-id="memo"],
td[data-column-id="narration"] {
  word-wrap: break-word;
  overflow-wrap: break-word;
  vertical-align: top;
  overflow: hidden;
  height: 1px; /* Force td to shrink-wrap, making height: 100% work on children */
}

/* Text content should fill the cell */
td[data-column-id="payee"] > *,
td[data-column-id="memo"] > *,
td[data-column-id="narration"] > * {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

/* Textarea should fill cell height */
td[data-column-id="payee"] textarea,
td[data-column-id="memo"] textarea,
td[data-column-id="narration"] textarea {
  height: 100%;
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


/* Sticky Status column (leftmost) — lock to 60px so index column's left: 60px is exact */
th[data-column-id="status"],
td[data-column-id="status"] {
  position: sticky;
  left: 0;
  z-index: 10;
  border-right: none; /* separator provided by index column's box-shadow */
  min-width: 60px;
  max-width: 60px;
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

/* Sticky Index (#) column (second from left, after 60px Status column) */
th[data-column-id="index"],
td[data-column-id="index"] {
  position: sticky;
  left: 60px;
  z-index: 10;
  box-shadow: -1px 0 0 0 #e5e7eb; /* left separator visible when sticking */
}

.dark th[data-column-id="index"],
.dark td[data-column-id="index"] {
  box-shadow: -1px 0 0 0 #374151; /* dark mode separator */
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

</style>
