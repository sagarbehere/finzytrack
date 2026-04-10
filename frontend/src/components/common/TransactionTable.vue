<template>
  <div class="transaction-table-container px-4 md:px-0">
    <!-- Confirm Dialog -->
    <ConfirmDialog
      :is-open="confirmDialog.isOpen.value"
      :title="confirmDialog.dialogOptions.value.title"
      :message="confirmDialog.dialogOptions.value.message"
      :confirm-text="confirmDialog.dialogOptions.value.confirmText"
      :cancel-text="confirmDialog.dialogOptions.value.cancelText"
      :variant="confirmDialog.dialogOptions.value.variant"
      @confirm="confirmDialog.handleConfirm"
      @cancel="confirmDialog.handleCancel"
      @close="confirmDialog.handleClose"
    />


    <!-- Table Controls -->
    <div class="flex flex-col gap-3 mb-4 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
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

    <!-- Desktop: Table layout (md and above) -->
    <div v-if="isMd" class="overflow-hidden rounded-lg ring-1 ring-gray-200 dark:ring-white/10">
      <div class="table-scroll-container">
        <table class="w-full table-fixed">
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
                    :style="{ width: `${cell.column.getSize()}px` }"
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

    <!-- Mobile: Card layout (below md) -->
    <TransactionCardList
      v-else
      :transactions="filteredTransactions"
      :column-visibility="columnVisibility"
      :editable="editable"
      :import-context="importContext"
      :ledger-context="ledgerContext"
      @update-field="handleUpdateField"
      @add-posting="handleAddPosting"
      @remove-posting="handleRemovePosting"
      @remove-transaction="removeTransaction"
      @duplicate-click="(id) => emit('duplicateClick', id)"
    />

    <!-- Summary section (when enabled) -->
    <TransactionTableSummary
      v-if="showSummary"
      :transactions="filteredTransactions"
      :import-context="importContext"
      @duplicate-click="(id) => emit('duplicateClick', id)"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, h, toRef, nextTick } from 'vue'
import {
  useVueTable,
  createColumnHelper,
  getCoreRowModel,
  FlexRender,
} from '@tanstack/vue-table'
import { MagnifyingGlassIcon } from '@heroicons/vue/20/solid'
import { useBreakpoint } from '@/composables/useBreakpoint'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import PriceTypeDropdown from '@/components/common/PriceTypeDropdown.vue'
import TransactionStatusIndicator from '@/components/common/TransactionStatusIndicator.vue'
import ColumnVisibilityControl from '@/components/common/ColumnVisibilityControl.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import TransactionTableSummary from '@/components/common/TransactionTableSummary.vue'
import TransactionCardList from '@/components/common/TransactionCardList.vue'
import { useTableColumns } from '@/composables/useTableColumns'
import { useTableKeyboardNavigation } from '@/composables/useTableKeyboardNavigation'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useTransactionDeleter } from '@/composables/useTransactionDeleter'
import { useToast } from '@/composables/useNotifications'
import { useTransactionStore } from '@/composables/useTransactionStore'
import { buildTanStackColumns, type TransactionColumnDef, type TableRowData } from '@/composables/useTransactionColumns'
import type { TransactionViewModel, ImportContext, LedgerContext } from '@/types/transactions'
import type { Cell } from '@tanstack/vue-table'

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
const tableColumns = useTableColumns()
const {
  columnVisibility,
  columnSizing,
  allColumns,
  toggleColumnVisibility,
  resetToDefaults,
  setColumnWidth
} = tableColumns

const {
  setCellFocus,
  handleKeyNavigation
} = useTableKeyboardNavigation()

const confirmDialog = useConfirmDialog()
const { isMd } = useBreakpoint()
const { deleteTransactions } = useTransactionDeleter()
const toast = useToast()

// Transaction store — owns data, mutations, baselines
const store = useTransactionStore(toRef(props, 'transactions'))

// Emit helper — mirrors the original emitUpdate() pattern
const emitTransactions = () => {
  emit('transactionsUpdated', store.transactions.value)
}

// Watch for EXTERNAL parent changes (e.g., parent loads new data, not echo-back from our emit).
// Uses a flag to skip the echo: we set it before emitting, clear after the prop watch runs.
let isOwnEmit = false
const emitAndGuard = () => {
  isOwnEmit = true
  emitTransactions()
}

watch(() => props.transactions, (newVal) => {
  if (isOwnEmit) {
    isOwnEmit = false
    return
  }
  // Genuinely new data from parent — sync into the store
  store.replaceTransactions(newVal)
})

// Wrapped mutation methods — call store then emit to parent
const handleUpdateField = (txId: string, path: string, value: unknown) => {
  store.updateField(txId, path, value)
  emitAndGuard()
}

const handleAddPosting = (txId: string) => {
  store.addPosting(txId)
  emitAndGuard()
}

const handleRemovePosting = (txId: string, postingIndex: number) => {
  store.removePosting(txId, postingIndex)
  emitAndGuard()
}

// State
const globalFilter = ref('')

// Track raw input strings for numeric fields to preserve trailing dots/zeros during editing.
const rawAmountStrings = ref<Record<string, string>>({})

const numericInputProps = (
  txId: string, postingIdx: number, field: string,
  currentValue: number | null | undefined,
  updateFn: (raw: string) => void,
  extraClasses: string = ''
): Record<string, unknown> => {
  const key = `${txId}-${postingIdx}-${field}`
  const rawStr = rawAmountStrings.value[key]
  const fallback = (() => {
    if (currentValue === null || currentValue === undefined) return ''
    const s = String(currentValue)
    const decimals = s.includes('.') ? s.split('.')[1].length : 0
    return decimals < 2 ? currentValue.toFixed(2) : s
  })()
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
  if (!globalFilter.value) return store.transactions.value

  const filterValue = globalFilter.value.toLowerCase()
  return store.transactions.value.filter(transaction => {
    const transactionMatch =
      transaction.date.toLowerCase().includes(filterValue) ||
      transaction.payee.toLowerCase().includes(filterValue) ||
      transaction.narration.toLowerCase().includes(filterValue) ||
      transaction.tags.some(tag => tag.toLowerCase().includes(filterValue)) ||
      transaction.links.some(link => link.toLowerCase().includes(filterValue))

    if (transactionMatch) return true

    return transaction.postings.some(posting =>
      posting.account.toLowerCase().includes(filterValue) ||
      posting.currency.toLowerCase().includes(filterValue) ||
      (posting.amount !== null && posting.amount.toString().toLowerCase().includes(filterValue))
    )
  })
})

// Flatten transactions into table rows
const currentPageTransactions = computed(() => {
  const result: TableRowData[] = []
  let transactionCounter = 0

  for (const transaction of filteredTransactions.value) {
    transactionCounter++
    const postings = transaction.postings

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

// Helper functions for cell styling
const getEditableInputClasses = (extraClasses = '') => {
  return `w-full min-w-0 rounded-md border-0 bg-white py-1.5 px-3 text-sm text-gray-900 outline-0 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 dark:focus:outline-indigo-500 ${extraClasses}`
}

const getDisplayClasses = () => {
  return 'text-gray-900 dark:text-white text-sm w-full min-w-0'
}

/** Returns color classes for a monetary amount (green for positive, red for negative, gray for zero/null) */
const getAmountColorClass = (amount: number | null | undefined): string => {
  if ((amount ?? 0) > 0) return 'text-green-700 dark:text-green-400'
  if ((amount ?? 0) < 0) return 'text-red-700 dark:text-red-400'
  return 'text-gray-700 dark:text-gray-300'
}

// Column definitions
const COLUMN_DEFS: TransactionColumnDef[] = [
  { id: 'status', header: 'Status', type: 'component', span: 'transaction', component: TransactionStatusIndicator },
  { id: 'index', header: '#', type: 'display', span: 'transaction', accessor: 'transactionIndex' },
  { id: 'date', header: 'Date', type: 'date', field: 'transaction.date', span: 'transaction' },
  { id: 'flag', header: 'Flag', type: 'text', field: 'transaction.flag', span: 'transaction' },
  { id: 'payee', header: 'Payee', type: 'textarea', field: 'transaction.payee', span: 'transaction', placeholder: 'Payee' },
  { id: 'memo', header: 'Memo', type: 'textarea', field: 'transaction.memo', span: 'transaction', placeholder: 'Memo' },
  { id: 'narration', header: 'Narration', type: 'textarea', field: 'transaction.narration', span: 'transaction', placeholder: 'Description' },
  {
    id: 'tags_links', header: 'Tags/Links', type: 'tags', span: 'transaction', placeholder: '#tag ^link',
    accessor: (row: TableRowData) => [...row.transaction.tags, ...row.transaction.links.map((l: string) => `^${l}`)].join(' '),
  },
  { id: 'account', header: 'Account', type: 'dropdown', field: 'account', span: 'posting', component: AccountDropdown, componentProps: { 'allow-custom': false }, placeholder: 'Account...' },
  { id: 'amount', header: 'Amount', type: 'numeric', field: 'amount', span: 'posting', align: 'right', colorize: true },
  { id: 'currency', header: 'Currency', type: 'dropdown', field: 'currency', span: 'posting', component: CommodityDropdown, componentProps: { 'allow-custom': false, 'show-details': false }, placeholder: 'CURR' },
  { id: 'cost_amount', header: 'Cost Amount', type: 'numeric', field: 'cost.amount', span: 'posting', align: 'right', accessor: (row: TableRowData) => row.cost?.amount },
  { id: 'cost_currency', header: 'Cost Currency', type: 'dropdown', field: 'cost.currency', span: 'posting', component: CommodityDropdown, componentProps: { 'allow-custom': false, 'show-details': false, clearable: true }, placeholder: 'CURR', accessor: (row: TableRowData) => row.cost?.currency },
  { id: 'cost_date', header: 'Cost Date', type: 'date', field: 'cost.date', span: 'posting', accessor: (row: TableRowData) => row.cost?.date },
  { id: 'price_amount', header: 'Price Amount', type: 'numeric', field: 'price.amount', span: 'posting', align: 'right', accessor: (row: TableRowData) => row.price?.amount },
  { id: 'price_currency', header: 'Price Currency', type: 'dropdown', field: 'price.currency', span: 'posting', component: CommodityDropdown, componentProps: { 'allow-custom': false, 'show-details': false, clearable: true }, placeholder: 'CURR', accessor: (row: TableRowData) => row.price?.currency },
  { id: 'price_type', header: 'Price Type', type: 'dropdown', field: 'price.type', span: 'posting', component: PriceTypeDropdown, placeholder: 'Type', accessor: (row: TableRowData) => row.price?.type },
  {
    id: 'balance', header: 'Balance', type: 'display', span: 'posting',
    accessor: (_row: TableRowData) => undefined, // placeholder — actual rendering via component type
  },
]

const spannedColumnIds = COLUMN_DEFS.filter(d => d.span === 'transaction').map(d => d.id)

const getRowSpan = (cell: Cell<any, any>) => {
  if (spannedColumnIds.includes(cell.column.id) && cell.row.original.isFirstPosting) {
    return cell.row.original.transaction.postings.length
  }
  return 1
}

const shouldSkipCell = (cell: Cell<any, any>) => {
  return spannedColumnIds.includes(cell.column.id) && !cell.row.original.isFirstPosting
}

// Build columns from definitions
const columns = computed(() => {
  const factoryColumns = buildTanStackColumns(COLUMN_DEFS, {
    editable: props.editable ?? true,
    updateField: handleUpdateField,
    numericInputProps,
    getImportContext,
    getLedgerContext,
    onDuplicateClick: (id: string) => emit('duplicateClick', id),
    getEditableInputClasses,
    getDisplayClasses,
    getAmountColorClass,
    columnConfig: tableColumns,
  })

  // Override the balance column's cell renderer (it needs getLedgerContext)
  const balanceIdx = factoryColumns.findIndex(c => c.id === 'balance')
  if (balanceIdx !== -1) {
    const columnHelper = createColumnHelper<TableRowData>()
    const colConfig = allColumns.value.find((col: any) => col.id === 'balance')
    factoryColumns[balanceIdx] = columnHelper.display({
      id: 'balance',
      header: 'Balance',
      cell: ({ row }) => {
        const ledgerInfo = getLedgerContext(row.original.transaction.id)
        const balance = ledgerInfo?.balance
        if (balance !== undefined) {
          return h('span', {
            class: `${getDisplayClasses()} font-mono text-right block`
          }, balance.toFixed(2))
        }
        return h('span', { class: 'text-gray-400 text-sm' }, '—')
      },
      size: colConfig?.defaultWidth || 120,
      minSize: colConfig?.minWidth || 100,
      enableResizing: colConfig?.resizable ?? true,
    })
  }

  // Append hand-written actions column
  const columnHelper = createColumnHelper<TableRowData>()
  const actionsConfig = allColumns.value.find((col: any) => col.id === 'actions')
  factoryColumns.push(columnHelper.display({
    id: 'actions',
    header: 'Actions',
    cell: ({ row }) => {
      if (!props.editable) return null

      const buttons = []

      if (row.original.isFirstPosting) {
        buttons.push(
          h('button', {
            onClick: () => handleRemovePosting(row.original.transaction.id, row.original.postingIndex),
            class: 'inline-flex items-center justify-center w-6 h-6 text-red-600 hover:text-red-800 hover:bg-red-50 rounded text-sm dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20',
            title: 'Remove posting'
          }, '×')
        )
        buttons.push(
          h('button', {
            onClick: () => handleAddPosting(row.original.transaction.id),
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
        buttons.push(
          h('button', {
            onClick: () => handleRemovePosting(row.original.transaction.id, row.original.postingIndex),
            class: 'inline-flex items-center justify-center w-6 h-6 text-red-600 hover:text-red-800 hover:bg-red-50 rounded text-sm dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20',
            title: 'Remove posting'
          }, '×')
        )
        buttons.push(h('div', { class: 'w-6 h-6' }))
        buttons.push(h('div', { class: 'w-6 h-6' }))
      }

      return h('div', { class: 'flex items-center justify-center gap-1' }, buttons)
    },
    size: actionsConfig?.defaultWidth || 100,
    minSize: actionsConfig?.minWidth || 80,
    enableResizing: actionsConfig?.resizable || false,
  }))

  // Filter columns based on visibility settings
  return factoryColumns.filter(column => {
    const columnId = column.id
    return columnId && columnVisibility.value[columnId] === true
  })
})

// Styling helpers
const getTransactionRowClasses = (rowData: any) => {
  const classes = []
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
    'px-3', 'py-2', 'text-sm', 'overflow-hidden',
    'border-r', 'border-b', 'border-gray-200', 'dark:border-white/10',
    'last:border-r-0'
  ]

  if (['amount', 'cost_amount', 'price_amount'].includes(cell.column.id)) {
    classes.push('text-right')
  }
  if (['actions'].includes(cell.column.id)) {
    classes.push('text-center')
  }

  if (getRowSpan(cell) > 1) {
    classes.push('align-top')
  }

  if (cell.column.id === 'index' && getRowSpan(cell) > 1) {
    classes.push('bg-gray-50', 'dark:bg-gray-800/50')
  }

  return classes
}

// Keyboard navigation
const handleCellKeydown = (event: KeyboardEvent, cell: any, rowData: any) => {
  const target = event.target as Element

  const isDropdownColumn = ['account', 'currency', 'cost_currency', 'price_currency', 'price_type'].includes(cell.column.id)

  if (isDropdownColumn) {
    const tableCell = target.closest('td')
    if (tableCell) {
      const optionsList = tableCell.querySelector('ul')
      const isDropdownOpen = optionsList !== null

      if (isDropdownOpen && ['ArrowUp', 'ArrowDown', 'Enter', 'Escape'].includes(event.key) && !event.altKey) {
        return
      }
    }
  }

  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && !event.altKey) {
      return
    }

    const postingColumns = ['account', 'amount', 'currency', 'cost_amount', 'cost_currency', 'cost_date', 'price_amount', 'price_currency', 'price_type', 'actions']
    const position = {
      rowIndex: rowData.transactionIndex - 1,
      columnId: cell.column.id,
      postingIndex: postingColumns.includes(cell.column.id) ? rowData.postingIndex : undefined
    }

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
  if (!isEditableColumn(cell.column.id)) {
    return
  }

  const target = event.target as HTMLElement
  if (target.tagName === 'INPUT' && target.getAttribute('type') === 'date') {
    return
  }

  event.preventDefault()

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

// Transaction removal (needs confirm dialog + API call, so stays here)
const removeTransaction = async (transaction: TransactionViewModel) => {
  const isImportContext = props.importContext !== undefined

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

  const confirmed = await confirmDialog.showConfirm({
    title: isImportContext ? 'Remove Transaction?' : 'Delete Transaction?',
    message: message,
    confirmText: isImportContext ? 'Remove' : 'Delete',
    cancelText: 'Cancel',
    variant: 'danger'
  })

  if (!confirmed) return

  try {
    if (!isImportContext) {
      await deleteTransactions([transaction.id])
    }

    store.removeTransaction(transaction.id)
    emitAndGuard()

    if (!isImportContext) {
      toast.success(
        'Transaction Deleted',
        'Transaction has been removed from the ledger'
      )
      emit('transactionDeleted', transaction.id)
    }
  } catch (error: any) {
    toast.error(
      isImportContext ? 'Remove Failed' : 'Delete Failed',
      error.message || 'Failed to remove transaction. Please try again.'
    )
  }
}

const table = useVueTable({
  get data() { return currentPageTransactions.value },
  get columns() { return columns.value },
  state: {
    get columnSizing() { return columnSizing.value },
  },
  onColumnSizingChange: (updater) => {
    const newSizing = typeof updater === 'function' ? updater(columnSizing.value) : updater

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


const scrollToTable = () => {
  nextTick(() => {
    const container = document.querySelector('.transaction-table-container')
    if (container) {
      container.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  })
}

onMounted(() => {
  const handleGlobalKeydown = (event: KeyboardEvent) => {
    if (event.key === 'F2') {
      if (filteredTransactions.value.length > 0) {
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

  onUnmounted(() => {
    document.removeEventListener('keydown', handleGlobalKeydown)
  })
})

defineExpose({
  resetToOriginal: () => {
    store.resetToImported()
    emitAndGuard()
  },
  scrollToTable,
  setNewEditBaseline: store.setEditBaseline,
  reinitializeBaselines: store.reinitializeBaselines,
  addToBaselines: store.addToBaselines,
  clearState: () => {
    store.clearState()
    emitAndGuard()
  },
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
  table-layout: fixed;
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

/* Allow dropdowns to escape table cell boundaries */
td[data-column-id="account"],
td[data-column-id="currency"],
td[data-column-id="cost_currency"],
td[data-column-id="price_currency"],
td[data-column-id="price_type"] {
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
