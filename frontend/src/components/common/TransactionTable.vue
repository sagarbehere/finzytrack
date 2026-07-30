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


    <!-- Bulk edit: operation summary (staged changes) + action bar (on selection) -->
    <template v-if="enableBulkEdit">
      <OperationSummary :operations="store.operationLog.value" @undo="undoBulkOp" />
      <BulkActionBar
        v-if="selectedCount > 0"
        :selected-count="selectedCount"
        :accounts-in-selection="accountsInSelection"
        :editable-meta-keys-in-selection="editableMetaKeysInSelection"
        :tags-in-selection="tagsInSelection"
        :links-in-selection="linksInSelection"
        :categorizing="isCategorizing"
        @apply="applyBulkOp"
        @autocategorize="autocategorizeSelected"
        @delete="bulkDelete"
        @clear="clearSelection"
      />
    </template>

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
        <button
          v-if="enableBulkEdit && hasModifiedRows"
          type="button"
          @click="showDiff = !showDiff"
          class="rounded-md px-2.5 py-1.5 text-sm font-medium shadow-xs inset-ring inset-ring-gray-300 dark:inset-ring-white/10"
          :class="showDiff
            ? 'bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200'
            : 'bg-white text-gray-900 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:hover:bg-white/20'"
        >
          {{ showDiff ? 'Hide changes' : 'Show changes' }}
        </button>

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
        <table class="w-full table-fixed" :class="{ 'has-select-col': enableBulkEdit }">
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
                  getTransactionRowClasses(row.original),
                ]"
                @dragover="(e) => onRowDragOver(e, row.original.transaction.id)"
                @dragenter.prevent
                @dragleave="onRowDragLeave"
                @drop="(e) => onRowDrop(e, row.original.transaction)"
              >
                <template v-for="cell in row.getVisibleCells()" :key="cell.id">
                  <td
                    v-if="!shouldSkipCell(cell)"
                    :rowspan="getRowSpan(cell)"
                    :data-column-id="cell.column.id"
                    :data-row="row.original.transactionIndex"
                    :data-posting="row.original.postingIndex"
                    :style="{ width: `${cell.column.getSize()}px` }"
                    :class="[
                      getCellClasses(cell),
                      showDiff && cellDiffOld(cell, row.original) ? 'diff-cell' : '',
                      dragOverTransactionId === row.original.transaction.id
                        ? '!bg-indigo-50 dark:!bg-indigo-900/30'
                        : ''
                    ]"
                    @keydown.capture="(e) => handleCellKeydown(e, cell, row.original)"
                    @click="(e) => handleCellClick(e, cell, row.original)"
                  >
                    <div v-if="showDiff && cellDiffOld(cell, row.original)" class="flex flex-col gap-0.5 py-1 text-sm leading-tight">
                      <span class="text-red-600 line-through decoration-red-400 break-words dark:text-red-400">{{ cellDiffOld(cell, row.original) }}</span>
                      <span class="font-medium text-green-700 break-words dark:text-green-400">{{ cellDiffNew(cell, row.original) }}</span>
                    </div>
                    <FlexRender v-else :render="cell.column.columnDef.cell" :props="cell.getContext()" />
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
      :enable-bulk-edit="enableBulkEdit"
      :import-context="importContext"
      :ledger-context="ledgerContext"
      @update-field="handleUpdateField"
      @add-posting="handleAddPosting"
      @remove-posting="handleRemovePosting"
      @remove-transaction="removeTransaction"
      @duplicate-click="(id) => emit('duplicateClick', id)"
      @open-documents="openDocumentsDrawer"
    />

    <!-- Transaction documents drawer (shared by desktop badge + mobile card) -->
    <TransactionDocumentsDrawer
      :open="documentsDrawerOpen"
      :transaction="documentsDrawerTx"
      :show-metadata="enableBulkEdit"
      :baseline-meta="documentsDrawerBaselineMeta"
      @update:open="documentsDrawerOpen = $event"
      @changed="onDocumentsDrawerChanged"
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
import DetailsBadge from '@/components/documents/DetailsBadge.vue'
import TransactionDocumentsDrawer from '@/components/documents/TransactionDocumentsDrawer.vue'
import ColumnVisibilityControl from '@/components/common/ColumnVisibilityControl.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import TransactionTableSummary from '@/components/common/TransactionTableSummary.vue'
import TransactionCardList from '@/components/common/TransactionCardList.vue'
import BulkActionBar from '@/components/transactions/BulkActionBar.vue'
import OperationSummary from '@/components/transactions/OperationSummary.vue'
import { isEditableMetaKey, type BulkOperation } from '@/utils/bulkOperations'
import { autocategorizeTargets } from '@/utils/autocategorize'
import { useCategorizeExisting } from '@/composables/useCategorizeExisting'
import { useConfig } from '@/composables/useConfig'
import { useTableColumns } from '@/composables/useTableColumns'
import { useTableKeyboardNavigation } from '@/composables/useTableKeyboardNavigation'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useTransactionDeleter } from '@/composables/useTransactionDeleter'
import { useToast } from '@/composables/useNotifications'
import { useTransactionStore } from '@/composables/useTransactionStore'
import { useDocuments } from '@/composables/useDocuments'
import { addDocument } from '@/utils/documentMeta'
import { buildTanStackColumns, type TransactionColumnDef, type TableRowData } from '@/composables/useTransactionColumns'
import { flattenTransactionRows } from '@/utils/flattenTransactionRows'
import { sign, toFixed, type Money } from '@/utils/money'
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
  // Enables per-transaction selection, the bulk action bar, and the operation
  // summary. Off by default so the Import flow (which shares this table) is
  // unaffected; the Transactions view opts in.
  enableBulkEdit?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  showSearch: false,
  showColumnFilters: false,
  showTransactionGrouping: true,
  showSummary: true,
  editable: true,
  columnControlAlign: 'left',
  enableBulkEdit: false,
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

// ── Documents ──────────────────────────────────────────────────────────────
const { uploadDocument } = useDocuments()
const documentsDrawerOpen = ref(false)
const documentsDrawerTxId = ref<string | null>(null)
// Resolve the live store transaction by id so the drawer reflects meta updates
// after the parent persists/stages a document change.
const documentsDrawerTx = computed<TransactionViewModel | null>(
  () => store.transactions.value.find(t => t.id === documentsDrawerTxId.value) ?? null
)
// Last-saved meta for the open transaction, so the drawer can show removed /
// renamed / changed metadata as a diff against what's staged.
const documentsDrawerBaselineMeta = computed<Record<string, string> | null>(
  () => store.editBaseline.value.find(t => t.id === documentsDrawerTxId.value)?.meta ?? null
)
const dragOverTransactionId = ref<string | null>(null)

const openDocumentsDrawer = (id: string) => {
  documentsDrawerTxId.value = id
  documentsDrawerOpen.value = true
}

// A document attach/remove is applied to the store like any other edit: the
// badge updates immediately, the row is marked modified, and it persists via
// the normal Save flow (TransactionsView) or import commit (ImportView). No
// separate persistence path — `document*` meta rides the existing update API.
const applyDocumentMeta = (txId: string, meta: Record<string, string>) => {
  // Update the store like any other field edit: the badge/drawer reflect the
  // change instantly, the row is marked modified (document paths participate in
  // modification detection), and the emit syncs the new meta into the parent's
  // array. It then persists via the normal Save flow / import commit.
  store.updateField(txId, 'meta', meta)
  emitAndGuard()
}

const onDocumentsDrawerChanged = (meta: Record<string, string>) => {
  if (documentsDrawerTxId.value) {
    applyDocumentMeta(documentsDrawerTxId.value, meta)
  }
}

// Row-level native file drag-and-drop (desktop accelerator). The browser owns
// the drag; we only handle the drop side — mirrors OFXFilePicker. Every row
// already carries its transaction id, so "which transaction" needs no lookup.
const dragHasFiles = (e: DragEvent): boolean =>
  Array.from(e.dataTransfer?.types ?? []).includes('Files')

const onRowDragOver = (e: DragEvent, txId: string) => {
  if (!props.editable || !dragHasFiles(e)) return
  e.preventDefault()
  dragOverTransactionId.value = txId
}

const onRowDragLeave = (e: DragEvent) => {
  if (e.currentTarget instanceof HTMLElement && !e.currentTarget.contains(e.relatedTarget as Node)) {
    dragOverTransactionId.value = null
  }
}

const onRowDrop = async (e: DragEvent, tx: TransactionViewModel) => {
  dragOverTransactionId.value = null
  if (!props.editable || !dragHasFiles(e)) return
  e.preventDefault()
  const files = Array.from(e.dataTransfer?.files ?? [])
  if (files.length === 0) return
  let meta = { ...tx.meta }
  try {
    for (const file of files) {
      const stored = await uploadDocument(file, { date: tx.date, narration: tx.payee || tx.narration })
      meta = addDocument(meta, stored.path)
    }
    applyDocumentMeta(tx.id, meta)
  } catch {
    // useDocuments already routed the error to errorHandler.
  }
}

watch(() => props.transactions, (newVal) => {
  if (isOwnEmit) {
    isOwnEmit = false
    return
  }
  // Genuinely new data from parent — sync into the store and drop any stale
  // selection (the underlying set changed).
  store.replaceTransactions(newVal)
  clearSelection()
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
  clearRawAmountsForTx(txId)
  emitAndGuard()
}

// State
const globalFilter = ref('')

// Track raw input strings for numeric fields to preserve trailing dots/zeros during editing.
// Keyed by `${txId}-${postingIdx}-${field}`. Posting indices shift when a posting is
// spliced out, so we clear all of a tx's keys whenever its postings change shape or
// the tx is removed — otherwise a stale entry from one posting can latch onto another.
const rawAmountStrings = ref<Record<string, string>>({})

const clearRawAmountsForTx = (txId: string) => {
  const prefix = `${txId}-`
  const next: Record<string, string> = {}
  for (const key in rawAmountStrings.value) {
    if (!key.startsWith(prefix)) next[key] = rawAmountStrings.value[key]
  }
  rawAmountStrings.value = next
}

const numericInputProps = (
  txId: string, postingIdx: number, field: string,
  currentValue: Money | null | undefined,
  updateFn: (raw: string) => void,
  extraClasses: string = ''
): Record<string, unknown> => {
  const key = `${txId}-${postingIdx}-${field}`
  const rawStr = rawAmountStrings.value[key]
  const fallback = (() => {
    if (currentValue === null || currentValue === undefined) return ''
    const s = String(currentValue)
    const decimals = s.includes('.') ? s.split('.')[1].length : 0
    return decimals < 2 ? toFixed(currentValue, 2) : s
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

// Import context merged with any autocategorize confidence, so the status
// indicator shows the same confidence glyph for bulk autocategorize as at import.
const getStatusContext = (transactionId: string): ImportContext | undefined => {
  const base = getImportContext(transactionId)
  const confidence = autocatConfidence.value.get(transactionId)
  if (confidence == null) return base
  return { ...(base ?? { is_duplicate: false }), confidence }
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

// ── Bulk selection & operations (enableBulkEdit only) ────────────────────────
// Selection is per transaction (id), over the currently loaded/filtered set.
const selectedTxIds = ref<Set<string>>(new Set())
const clearSelection = () => {
  selectedTxIds.value = new Set()
  autocatConfidence.value = new Map()
}

const toggleRowSelection = (txId: string) => {
  const next = new Set(selectedTxIds.value)
  next.has(txId) ? next.delete(txId) : next.add(txId)
  selectedTxIds.value = next
}

// Anchor for shift-click range selection (index into visibleIds).
const lastClickedIndex = ref<number | null>(null)
const onSelectClick = (txId: string, e: MouseEvent) => {
  const ids = visibleIds.value
  const idx = ids.indexOf(txId)
  if (e.shiftKey && lastClickedIndex.value !== null && idx !== -1) {
    // Shift-click: select the contiguous range from the anchor to here.
    const lo = Math.min(lastClickedIndex.value, idx)
    const hi = Math.max(lastClickedIndex.value, idx)
    const next = new Set(selectedTxIds.value)
    for (let i = lo; i <= hi; i++) next.add(ids[i])
    selectedTxIds.value = next
  } else {
    // Plain / ctrl / cmd click: toggle this row and move the anchor.
    toggleRowSelection(txId)
    lastClickedIndex.value = idx
  }
}

// Per-row autocategorization confidence (classifier only; AI returns none).
// Feeds the SAME status-indicator confidence glyph the import flow uses, so the
// review affordance is identical. Transient: cleared on save/reset/requery/clear.
const autocatConfidence = ref<Map<string, number>>(new Map())

const visibleIds = computed(() => filteredTransactions.value.map(t => t.id))
const allVisibleSelected = computed(
  () => visibleIds.value.length > 0 && visibleIds.value.every(id => selectedTxIds.value.has(id)),
)
const someVisibleSelected = computed(() => visibleIds.value.some(id => selectedTxIds.value.has(id)))
const toggleSelectAll = () => {
  selectedTxIds.value = allVisibleSelected.value ? new Set() : new Set(visibleIds.value)
}

const selectedTransactions = computed(() =>
  store.transactions.value.filter(t => selectedTxIds.value.has(t.id)),
)
const selectedCount = computed(() => selectedTransactions.value.length)

// Accounts present across the selection — the "from" options for replace-account.
const accountsInSelection = computed(() => {
  const set = new Set<string>()
  for (const tx of selectedTransactions.value) for (const p of tx.postings) set.add(p.account)
  return [...set].sort()
})

// Editable metadata keys present across the selection — for remove/rename.
const editableMetaKeysInSelection = computed(() => {
  const set = new Set<string>()
  for (const tx of selectedTransactions.value) {
    for (const k of Object.keys(tx.meta)) if (isEditableMetaKey(k)) set.add(k)
  }
  return [...set].sort()
})

// Tags / links present across the selection — the options for "remove".
const tagsInSelection = computed(() => {
  const set = new Set<string>()
  for (const tx of selectedTransactions.value) for (const t of tx.tags) set.add(t)
  return [...set].sort()
})
const linksInSelection = computed(() => {
  const set = new Set<string>()
  for (const tx of selectedTransactions.value) for (const l of tx.links) set.add(l)
  return [...set].sort()
})

const applyBulkOp = (op: BulkOperation) => {
  const ids = selectedTransactions.value.map(t => t.id)
  const entry = store.applyBulkOperation(ids, op)
  if (entry) {
    emitAndGuard()
  } else {
    toast.warning('No changes', 'No selected transactions matched that operation.')
  }
}

const undoBulkOp = (id: number) => {
  store.undoBulkOperation(id)
  autocatConfidence.value = new Map() // clear transient confidence glyphs on undo
  emitAndGuard()
}

// ── Autocategorize (resolve Expenses:Unknown on the selection) ───────────────
const { config } = useConfig()
const { categorizeExisting, isCategorizing } = useCategorizeExisting()
const unknownAccount = computed(() => config.value?.accounts?.default_unknown_account || 'Expenses:Unknown')

const autocategorizeSelected = async () => {
  const selected = selectedTransactions.value
  const targets = autocategorizeTargets(selected, unknownAccount.value)
  if (targets.length === 0) {
    toast.warning('Nothing to categorize', `None of the ${selected.length} selected transaction${selected.length === 1 ? '' : 's'} has a single ${unknownAccount.value} posting to resolve.`)
    return
  }

  const outcome = await categorizeExisting(targets.map(t => ({
    id: t.txId, payee: t.payee, memo: t.memo ?? null, narration: t.narration, source_account: t.sourceAccount,
  })))
  if (!outcome) return // error already surfaced by the composable

  const suggestions = new Map<string, string>()
  const confidence = new Map<string, number>()
  for (const r of outcome.results) {
    if (r.suggested_category && r.suggested_category !== unknownAccount.value) {
      suggestions.set(r.id, r.suggested_category)
      // Per-row confidence surfaces via the status indicator (same glyph as
      // import) — classifier only; AI returns null.
      if (r.confidence != null) confidence.set(r.id, r.confidence)
    }
  }

  const entry = store.applyAutocategorization(
    suggestions, unknownAccount.value,
    `Autocategorize ${suggestions.size} transaction${suggestions.size === 1 ? '' : 's'}`,
  )
  if (entry) {
    autocatConfidence.value = confidence
    emitAndGuard()
  }

  for (const w of outcome.stats.warnings || []) toast.warning('Autocategorize', w)

  const categorized = suggestions.size
  const notEligible = selected.length - targets.length // no single unknown posting
  const noText = targets.length - categorized           // empty description → left as-is

  const detail: string[] = []
  if (noText > 0) detail.push(`${noText} had no description to categorize`)
  if (notEligible > 0) detail.push(`${notEligible} skipped (no ${unknownAccount.value} posting)`)
  const suffix = detail.length ? ` — ${detail.join('; ')}` : ''

  if (categorized === 0) {
    toast.warning('Autocategorize', `No transactions were categorized${suffix}.`)
  } else {
    toast.success(
      'Autocategorize complete',
      `Categorized ${categorized} transaction${categorized === 1 ? '' : 's'}${suffix}. Review and Save.`,
    )
  }
}

// Bulk delete is immediate (like the per-row delete), not staged: it writes to
// the ledger straight away after a confirm, then drops the rows and clears the
// selection. In import context there is no ledger, so it only removes locally.
const bulkDelete = async () => {
  const ids = selectedTransactions.value.map(t => t.id)
  if (ids.length === 0) return
  const isImportContext = props.importContext !== undefined

  const confirmed = await confirmDialog.showConfirm({
    title: isImportContext ? 'Remove Transactions?' : 'Delete Transactions?',
    message: isImportContext
      ? `Remove ${ids.length} selected transaction${ids.length === 1 ? '' : 's'} from the import?`
      : `Delete ${ids.length} selected transaction${ids.length === 1 ? '' : 's'}? This immediately updates the ledger and cannot be undone.`,
    confirmText: isImportContext ? 'Remove' : 'Delete',
    cancelText: 'Cancel',
    variant: 'danger',
  })
  if (!confirmed) return

  try {
    if (!isImportContext) await deleteTransactions(ids)
    for (const id of ids) clearRawAmountsForTx(id)
    store.removeTransactions(ids)
    clearSelection()
    emitAndGuard()
    if (!isImportContext) {
      toast.success('Transactions Deleted', `Removed ${ids.length} transaction${ids.length === 1 ? '' : 's'} from the ledger`)
      for (const id of ids) emit('transactionDeleted', id)
    }
  } catch (error: any) {
    toast.error('Delete Failed', error.message || 'Failed to delete transactions. Please try again.')
  }
}

// Flatten transactions into table rows. Cache is keyed by tx identity:
// the store mutates only the changed transaction's reference, so rows for
// untouched transactions are reused — keystrokes only rebuild the edited tx.
const rowCache = new Map<TransactionViewModel, TableRowData[]>()
const currentPageTransactions = computed(() =>
  flattenTransactionRows(filteredTransactions.value, rowCache),
)

// Helper functions for cell styling
const getEditableInputClasses = (extraClasses = '') => {
  return `w-full min-w-0 rounded-md border-0 bg-white py-1.5 px-3 text-sm text-gray-900 outline-0 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 dark:focus:outline-indigo-500 ${extraClasses}`
}

const getDisplayClasses = () => {
  return 'text-gray-900 dark:text-white text-sm w-full min-w-0'
}

/** Returns color classes for a monetary amount (green for positive, red for negative, gray for zero/null) */
const getAmountColorClass = (amount: Money | null | undefined): string => {
  if (amount == null) return 'text-gray-700 dark:text-gray-300'
  const s = sign(amount)
  if (s > 0) return 'text-green-700 dark:text-green-400'
  if (s < 0) return 'text-red-700 dark:text-red-400'
  return 'text-gray-700 dark:text-gray-300'
}

// Column definitions
const COLUMN_DEFS: TransactionColumnDef[] = [
  // Status ("Info") also hosts the document/details paperclip (folded in via the
  // cell override below) so the affordance needs no column of its own.
  { id: 'status', header: 'Info', type: 'component', span: 'transaction', component: TransactionStatusIndicator },
  { id: 'index', header: '#', type: 'display', span: 'transaction', accessor: 'transactionIndex' },
  { id: 'date', header: 'Date', type: 'date', field: 'transaction.date', span: 'transaction' },
  { id: 'flag', header: 'Flag', type: 'text', field: 'transaction.flag', span: 'transaction' },
  { id: 'payee', header: 'Payee', type: 'textarea', field: 'transaction.payee', span: 'transaction', placeholder: 'Payee' },
  { id: 'memo', header: 'Memo', type: 'textarea', field: 'transaction.memo', span: 'transaction', placeholder: 'Memo' },
  { id: 'narration', header: 'Narration', type: 'textarea', field: 'transaction.narration', span: 'transaction', placeholder: 'Description' },
  {
    id: 'tags_links', header: 'Tags/Links', type: 'tags', span: 'transaction', placeholder: '#tag ^link',
    accessor: (row: TableRowData) => [...row.transaction.tags.map((t: string) => `#${t}`), ...row.transaction.links.map((l: string) => `^${l}`)].join(' '),
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

// 'select' is a transaction-level column (one checkbox per transaction), so it
// participates in the same rowspan treatment as the other spanned columns.
const spannedColumnIds = [...COLUMN_DEFS.filter(d => d.span === 'transaction').map(d => d.id), 'select']

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
    editable: () => props.editable ?? true,
    updateField: handleUpdateField,
    numericInputProps,
    getImportContext,
    getLedgerContext,
    onDuplicateClick: (id: string) => emit('duplicateClick', id),
    onDocumentClick: (id: string) => openDocumentsDrawer(id),
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
          }, toFixed(balance, 2))
        }
        return h('span', { class: 'text-gray-400 text-sm' }, '—')
      },
      size: colConfig?.defaultWidth || 120,
      minSize: colConfig?.minWidth || 100,
      enableResizing: colConfig?.resizable ?? true,
    })
  }

  // Fold the document/details paperclip into the Status ("Info") cell — stacked
  // beneath the status glyphs, so it needs no column of its own. detailsMode makes
  // its active state and tooltip reflect metadata too (Transactions view).
  const statusIdx = factoryColumns.findIndex(c => c.id === 'status')
  if (statusIdx !== -1) {
    const statusHelper = createColumnHelper<TableRowData>()
    const statusConfig = allColumns.value.find((col: any) => col.id === 'status')
    factoryColumns[statusIdx] = statusHelper.display({
      id: 'status',
      header: 'Info',
      cell: ({ row }) => {
        if (!row.original.isFirstPosting) return null
        const tx = row.original.transaction
        // Details glyph on top (always present, so it stays top-aligned with the
        // select checkbox and # regardless of status); status glyphs stack below.
        return h('div', { class: 'flex flex-col items-center gap-1 pt-1.5' }, [
          h(DetailsBadge, {
            transaction: tx,
            detailsMode: props.enableBulkEdit,
            onDocumentClick: (id: string) => openDocumentsDrawer(id),
          }),
          h(TransactionStatusIndicator, {
            transaction: tx,
            importContext: getStatusContext(tx.id),
            ledgerContext: getLedgerContext(tx.id),
            onDuplicateClick: (id: string) => emit('duplicateClick', id),
          }),
        ])
      },
      size: statusConfig?.defaultWidth || 60,
      minSize: statusConfig?.minWidth || 40,
      enableResizing: statusConfig?.resizable ?? true,
    })
  }

  // Prepend the selection column (bulk-edit only). Header is a select-all
  // checkbox over the visible set; the per-row checkbox is rowspanned so it
  // shows once per transaction (see spannedColumnIds / shouldSkipCell).
  if (props.enableBulkEdit) {
    const selectHelper = createColumnHelper<TableRowData>()
    const checkboxClass = 'h-3.5 w-3.5 cursor-pointer rounded border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/20 dark:bg-white/5'
    factoryColumns.unshift(selectHelper.display({
      id: 'select',
      header: () => h('div', { class: 'flex justify-center' }, [
        h('input', {
          type: 'checkbox',
          class: checkboxClass,
          checked: allVisibleSelected.value,
          indeterminate: someVisibleSelected.value && !allVisibleSelected.value,
          onChange: toggleSelectAll,
          'aria-label': 'Select all transactions',
        }),
      ]),
      cell: ({ row }) => row.original.isFirstPosting
        ? h('div', { class: 'flex justify-center pt-2' }, [
            h('input', {
              type: 'checkbox',
              class: checkboxClass,
              checked: selectedTxIds.value.has(row.original.transaction.id),
              // Click (not change) so shift-click range selection can read modifier keys.
              onClick: (e: MouseEvent) => onSelectClick(row.original.transaction.id, e),
              'aria-label': 'Select transaction',
            }),
          ])
        : null,
      size: 44,
      minSize: 44,
      enableResizing: false,
    }))
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

  return factoryColumns
})

// Styling helpers — precompute static class strings per (column, row-shape)
// to avoid per-cell array allocations on every render.
const ROW_TOP = 'border-t-2 border-t-indigo-200 dark:border-t-indigo-800'
const ROW_BOTTOM = 'border-b-2 border-b-indigo-200 dark:border-b-indigo-800'
const ROW_TOP_BOTTOM = `${ROW_TOP} ${ROW_BOTTOM}`

const getTransactionRowClasses = (rowData: any): string => {
  if (rowData.isFirstPosting && rowData.isLastPosting) return ROW_TOP_BOTTOM
  if (rowData.isFirstPosting) return ROW_TOP
  if (rowData.isLastPosting) return ROW_BOTTOM
  return ''
}

const CELL_BASE = 'px-3 py-2 text-sm overflow-hidden border-r border-b border-gray-200 dark:border-white/10 last:border-r-0'
const RIGHT_ALIGNED_COLS = new Set(['amount', 'cost_amount', 'price_amount'])
const CENTER_ALIGNED_COLS = new Set(['actions'])
const cellBaseByColumn = new Map<string, string>()
for (const def of COLUMN_DEFS) {
  let cls = CELL_BASE
  if (RIGHT_ALIGNED_COLS.has(def.id)) cls += ' text-right'
  if (CENTER_ALIGNED_COLS.has(def.id)) cls += ' text-center'
  cellBaseByColumn.set(def.id, cls)
}
cellBaseByColumn.set('actions', `${CELL_BASE} text-center`)

// Sticky-left columns keep their own solid background (so scrolling content
// doesn't bleed through), so the modified tint would be hidden under them —
// skip those and tint the scrollable cells instead.
const STICKY_LEFT_COLS = new Set(['select', 'status', 'index'])

// ── In-table cell diff (bulk-edit only) ──────────────────────────────────────
// Show "was: <old>" beneath a changed cell, diffed against the last-saved
// (edit baseline) value. Covers bulk and manual edits alike.
const baselineById = computed(() => {
  const m = new Map<string, TransactionViewModel>()
  for (const t of store.editBaseline.value) m.set(t.id, t)
  return m
})

const DIFF_COLUMNS = new Set([
  'date', 'flag', 'payee', 'memo', 'narration', 'tags_links',
  'account', 'amount', 'currency', 'cost_amount', 'cost_currency', 'cost_date',
  'price_amount', 'price_currency', 'price_type',
])

const fieldValueForColumn = (tx: TransactionViewModel, colId: string, pi: number): string | undefined => {
  const p = tx.postings[pi]
  switch (colId) {
    case 'date': return tx.date ?? ''
    case 'flag': return tx.flag ?? ''
    case 'payee': return tx.payee ?? ''
    case 'memo': return tx.memo ?? ''
    case 'narration': return tx.narration ?? ''
    case 'tags_links': return [...tx.tags.map(t => `#${t}`), ...tx.links.map(l => `^${l}`)].join(' ')
    case 'account': return p?.account ?? ''
    case 'amount': return p?.amount != null ? String(p.amount) : ''
    case 'currency': return p?.currency ?? ''
    case 'cost_amount': return p?.cost?.amount != null ? String(p.cost.amount) : ''
    case 'cost_currency': return p?.cost?.currency ?? ''
    case 'cost_date': return p?.cost?.date ?? ''
    case 'price_amount': return p?.price?.amount != null ? String(p.price.amount) : ''
    case 'price_currency': return p?.price?.currency ?? ''
    case 'price_type': return p?.price?.type ?? ''
    default: return undefined
  }
}

// When on, changed cells render a read-only old → new diff instead of the input.
const showDiff = ref(false)
const hasModifiedRows = computed(() => store.transactions.value.some(t => t.internal.isModified))

// The baseline (pre-edit) value for a changed cell, or undefined if unchanged /
// not a diffable column. '' is rendered as "(empty)".
const cellDiffOld = (cell: Cell<any, any>, rowData: any): string | undefined => {
  if (!props.enableBulkEdit) return undefined
  const tx = rowData.transaction as TransactionViewModel
  if (!tx.internal.isModified) return undefined
  const colId = cell.column.id
  if (!DIFF_COLUMNS.has(colId)) return undefined
  const baseTx = baselineById.value.get(tx.id)
  if (!baseTx) return undefined
  const cur = fieldValueForColumn(tx, colId, rowData.postingIndex)
  const old = fieldValueForColumn(baseTx, colId, rowData.postingIndex)
  if (cur === undefined || old === undefined || cur === old) return undefined
  return old === '' ? '(empty)' : old
}

// The current (post-edit) value for a changed cell, for the diff display.
const cellDiffNew = (cell: Cell<any, any>, rowData: any): string => {
  const cur = fieldValueForColumn(rowData.transaction as TransactionViewModel, cell.column.id, rowData.postingIndex)
  return cur === undefined || cur === '' ? '(empty)' : cur
}

const getCellClasses = (cell: Cell<any, any>): string => {
  const id = cell.column.id
  let cls = cellBaseByColumn.get(id) ?? CELL_BASE
  const rowspan = getRowSpan(cell)
  if (rowspan > 1) {
    cls += ' align-top'
    if (id === 'index') cls += ' bg-gray-50 dark:bg-gray-800/50'
  }
  // In-table diff signal: tint cells of a modified transaction so the user can
  // see which rows a bulk (or manual) edit touched, before Save.
  if (props.enableBulkEdit && !STICKY_LEFT_COLS.has(id) && cell.row.original.transaction.internal.isModified) {
    cls += ' bg-amber-50 dark:bg-amber-500/10'
  }
  return cls
}

// Keyboard navigation
const handleCellKeydown = (event: KeyboardEvent, cell: any, rowData: any) => {
  const target = event.target as Element

  const isDropdownColumn = ['account', 'currency', 'cost_currency', 'price_currency', 'price_type'].includes(cell.column.id)

  if (isDropdownColumn) {
    // ComboboxOptions is teleported to <body>, so check the document for a HeadlessUI listbox
    const isDropdownOpen = document.querySelector('[role="listbox"]') !== null

    if (isDropdownOpen && ['ArrowUp', 'ArrowDown', 'Enter', 'Escape'].includes(event.key) && !event.altKey) {
      return
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
    clearRawAmountsForTx(transaction.id)
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
    get columnVisibility() { return columnVisibility.value },
  },
  onColumnSizingChange: (updater) => {
    const newSizing = typeof updater === 'function' ? updater(columnSizing.value) : updater

    Object.keys(newSizing).forEach(columnId => {
      setColumnWidth(columnId, newSizing[columnId])
    })
  },
  onColumnVisibilityChange: (updater) => {
    const next = typeof updater === 'function' ? updater(columnVisibility.value) : updater
    columnVisibility.value = next
  },
  enableColumnResizing: true,
  columnResizeMode: 'onChange',
  getCoreRowModel: getCoreRowModel(),
  getRowId: (row) => `${row.transaction.id}::${row.postingIndex}`,
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

const handleGlobalKeydown = (event: KeyboardEvent) => {
  if (event.key !== 'F2') return
  if (filteredTransactions.value.length === 0) return

  const firstTransaction = filteredTransactions.value[0]
  const visibleEditableColumns = ['date', 'flag', 'payee', 'narration', 'tags_links', 'account', 'amount', 'currency', 'actions']
    .filter(col => columnVisibility.value[col] === true)

  if (!firstTransaction || visibleEditableColumns.length === 0) return

  const firstColumn = visibleEditableColumns[0]
  setCellFocus({
    rowIndex: 0,
    columnId: firstColumn,
    postingIndex: ['account', 'amount', 'currency', 'actions'].includes(firstColumn) ? 0 : undefined,
  })
  event.preventDefault()
}

onMounted(() => {
  document.addEventListener('keydown', handleGlobalKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleGlobalKeydown)
})

defineExpose({
  resetToOriginal: () => {
    store.resetToImported()
    clearSelection()
    emitAndGuard()
  },
  scrollToTable,
  setNewEditBaseline: store.setEditBaseline,
  markAllSavedAndRebaseline: () => {
    store.markAllSavedAndRebaseline()
    clearSelection()
    emitAndGuard()
  },
  reinitializeBaselines: () => {
    store.reinitializeBaselines()
    clearSelection()
  },
  addToBaselines: store.addToBaselines,
  clearState: () => {
    store.clearState()
    clearSelection()
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
  background-color: rgb(31, 41, 55); /* gray-800, opaque so scrolling body content doesn't bleed through sticky header */
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
  background-color: rgb(31, 41, 55); /* gray-800, opaque (sticky header) */
}

.dark td[data-column-id="status"] {
  background-color: #111827; /* dark:bg-gray-900 */
}

/* Diff cells override the text-column shrink-wrap hack (overflow:hidden +
   height:1px) so the read-only old → new display isn't clipped. */
td.diff-cell {
  overflow: visible !important;
  height: auto !important;
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
  background-color: rgb(31, 41, 55); /* gray-800, opaque (sticky header) */
}

.dark td[data-column-id="index"] {
  background-color: #111827; /* dark:bg-gray-900 */
}

/* Bulk-edit selection column: sticky at the far left (44px), pushing the
   Status and Index sticky columns right by 44px. Scoped to .has-select-col so
   the Import flow (no select column) keeps the original left offsets. */
table.has-select-col th[data-column-id="select"],
table.has-select-col td[data-column-id="select"] {
  position: sticky;
  left: 0;
  z-index: 10;
  min-width: 44px;
  max-width: 44px;
  box-shadow: none;
}

table.has-select-col th[data-column-id="select"] {
  background-color: rgb(249 250 251); /* bg-gray-50 */
}

table.has-select-col td[data-column-id="select"] {
  background-color: white;
}

.dark table.has-select-col th[data-column-id="select"] {
  background-color: rgb(31, 41, 55); /* gray-800 (sticky header) */
}

.dark table.has-select-col td[data-column-id="select"] {
  background-color: #111827; /* dark:bg-gray-900 */
}

table.has-select-col th[data-column-id="status"],
table.has-select-col td[data-column-id="status"] {
  left: 44px;
}

table.has-select-col th[data-column-id="index"],
table.has-select-col td[data-column-id="index"] {
  left: 104px; /* 44px select + 60px status */
}

</style>
