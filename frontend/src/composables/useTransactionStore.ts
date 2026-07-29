import { type Ref, ref } from 'vue'
import type { TransactionViewModel } from '@/types/transactions'
import { isModified } from '@/utils/transactionModification'
import { applyOperationToTransaction, describeOperation, type BulkOperation } from '@/utils/bulkOperations'
import type { Money } from '@/utils/money'

/**
 * One applied bulk operation, recorded for the operation summary and per-operation
 * undo. `priors` holds a pre-mutation snapshot of each affected transaction keyed
 * by id — undo restores them. (Because priors are whole-transaction snapshots,
 * undoing an operation reverts any *later* operations on the same transactions
 * too; the summary UI undoes most-recent-first to keep that intuitive.)
 */
export interface AppliedOperation {
  id: number
  operation: BulkOperation
  label: string
  affectedIds: string[]
  priors: Map<string, TransactionViewModel>
}

function deepCopy<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj))
}

function setByPath(obj: Record<string, any>, path: string, value: unknown): void {
  const parts = path.split('.')
  let current = obj
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i]
    const nextKey = parts[i + 1]
    if (current[key] === undefined || current[key] === null) {
      // Create intermediate: array if next key is numeric, object otherwise
      current[key] = /^\d+$/.test(nextKey) ? [] : {}
    }
    current = current[key]
  }
  current[parts[parts.length - 1]] = value
}

function getByPath(obj: Record<string, any>, path: string): unknown {
  const parts = path.split('.')
  let current: any = obj
  for (const part of parts) {
    if (current === undefined || current === null) return undefined
    current = current[part]
  }
  return current
}

export function useTransactionStore(input: Ref<TransactionViewModel[]>) {
  const transactions = ref<TransactionViewModel[]>(deepCopy(input.value))
  const importedBaseline = ref<TransactionViewModel[]>(deepCopy(input.value))
  const editBaseline = ref<TransactionViewModel[]>(deepCopy(input.value))

  // Log of applied bulk operations, in application order. Drives the operation
  // summary and per-operation undo. Cleared whenever the working set is
  // rebaselined (save), reset, or reloaded (re-query).
  const operationLog = ref<AppliedOperation[]>([])
  let nextOpId = 1

  function replaceTransactions(newVal: TransactionViewModel[]): void {
    transactions.value = deepCopy(newVal)
  }

  // After in-place mutation, replace the changed transaction's slot with a
  // shallow clone so downstream consumers can detect *which* transaction
  // changed via reference comparison. Untouched transactions keep identity.
  // The shallow clone preserves id and all field values verbatim.
  function notifyChange(tx: TransactionViewModel): void {
    const i = transactions.value.indexOf(tx)
    if (i === -1) {
      transactions.value = [...transactions.value]
      return
    }
    const next = [...transactions.value]
    next[i] = { ...tx }
    transactions.value = next
  }

  function refreshModifiedFlag(tx: TransactionViewModel): void {
    tx.internal.isModified = isModified(tx, editBaseline.value)
  }

  function updateField(txId: string, path: string, value: unknown): void {
    const tx = transactions.value.find(t => t.id === txId)
    if (!tx) return

    // Special case: tags_links virtual field
    // Also accept ˆ (U+02C6, Mac dead-key) as a link prefix and normalize to ^.
    if (path === 'tags_links') {
      const str = (value as string) || ''
      const parts = str.split(/\s+/).filter(p => p)
      tx.tags = parts.filter(p => p.startsWith('#')).map(p => p.substring(1))
      tx.links = parts.filter(p => p.startsWith('^') || p.startsWith('ˆ')).map(p => p.substring(1))
      refreshModifiedFlag(tx)
      notifyChange(tx)
      pruneOperationLogIfClean()
      return
    }

    // Special case: source_account sync when changing posting account
    const accountMatch = path.match(/^postings\.(\d+)\.account$/)
    if (accountMatch) {
      const postingIndex = parseInt(accountMatch[1])
      const oldAccount = tx.postings[postingIndex].account
      tx.postings[postingIndex].account = value as string
      if (tx.meta['source_account'] === oldAccount) {
        tx.meta['source_account'] = value as string
      }
      refreshModifiedFlag(tx)
      notifyChange(tx)
      pruneOperationLogIfClean()
      return
    }

    // Special case: auto-date when setting cost.amount for the first time
    const costAmountMatch = path.match(/^postings\.(\d+)\.cost\.amount$/)
    if (costAmountMatch) {
      const postingIndex = parseInt(costAmountMatch[1])
      const posting = tx.postings[postingIndex]
      if (!posting.cost) {
        posting.cost = {}
      }
      posting.cost.amount = value as Money | undefined
      if (posting.cost.amount !== undefined && !posting.cost.date) {
        posting.cost.date = tx.date
      }
      refreshModifiedFlag(tx)
      notifyChange(tx)
      pruneOperationLogIfClean()
      return
    }

    // General case: set by path
    setByPath(tx as unknown as Record<string, any>, path, value)
    refreshModifiedFlag(tx)
    notifyChange(tx)
    pruneOperationLogIfClean()
  }

  function addPosting(txId: string): void {
    const tx = transactions.value.find(t => t.id === txId)
    if (!tx) return
    tx.postings.push({
      account: '',
      amount: null,
      currency: 'USD',
      cost: undefined,
      price: undefined,
      meta: undefined,
    })
    refreshModifiedFlag(tx)
    notifyChange(tx)
  }

  function removePosting(txId: string, postingIndex: number): void {
    const tx = transactions.value.find(t => t.id === txId)
    if (!tx || tx.postings.length <= 1) return
    tx.postings.splice(postingIndex, 1)
    refreshModifiedFlag(tx)
    notifyChange(tx)
  }

  function removeTransaction(txId: string): void {
    transactions.value = transactions.value.filter(t => t.id !== txId)
  }

  function removeTransactions(ids: string[]): void {
    const set = new Set(ids)
    transactions.value = transactions.value.filter(t => !set.has(t.id))
  }

  /**
   * Apply one bulk operation to the transactions whose ids are in `txIds`.
   *
   * Only transactions the operation actually changes are touched (e.g. a
   * replace-account skips a selected transaction that never used the account).
   * Each changed transaction has its modified flag recomputed against the
   * *unchanged* edit baseline — so bulk-edited transactions become modified and
   * are included in the next Save. (This is the opposite of the import path,
   * which rebaselines after categorizing; here rebaselining would make Save
   * silently drop the edits — see dev-docs/bulk-edits.md.)
   *
   * Returns the log entry, or null if nothing changed. A single array
   * reassignment notifies reactive consumers once for the whole batch.
   */
  function applyBulkOperation(txIds: string[], op: BulkOperation): AppliedOperation | null {
    const idSet = new Set(txIds)
    const priors = new Map<string, TransactionViewModel>()
    const affectedIds: string[] = []

    const next = transactions.value.map(tx => {
      if (!idSet.has(tx.id)) return tx
      const candidate = deepCopy(tx)
      const changed = applyOperationToTransaction(candidate, op)
      if (!changed) return tx
      priors.set(tx.id, deepCopy(tx))
      candidate.internal.isModified = isModified(candidate, editBaseline.value)
      affectedIds.push(tx.id)
      return candidate
    })

    if (affectedIds.length === 0) return null

    transactions.value = next
    const entry: AppliedOperation = {
      id: nextOpId++,
      operation: op,
      label: describeOperation(op),
      affectedIds,
      priors,
    }
    operationLog.value = [...operationLog.value, entry]
    pruneOperationLogIfClean()
    return entry
  }

  /**
   * Undo a previously applied bulk operation by restoring the pre-operation
   * snapshot of every transaction it affected, then dropping it from the log.
   */
  function undoBulkOperation(opId: number): void {
    const entry = operationLog.value.find(e => e.id === opId)
    if (!entry) return

    const next = transactions.value.map(tx => {
      const prior = entry.priors.get(tx.id)
      if (!prior) return tx
      const restored = deepCopy(prior)
      restored.internal.isModified = isModified(restored, editBaseline.value)
      return restored
    })
    transactions.value = next
    operationLog.value = operationLog.value.filter(e => e.id !== opId)
    pruneOperationLogIfClean()
  }

  function clearOperationLog(): void {
    operationLog.value = []
  }

  // If the working set is back to the edit baseline (nothing modified), there is
  // nothing staged — so drop the operation log. This keeps a net-zero sequence
  // (e.g. add tag then remove it) from leaving phantom "staged changes" that the
  // disabled Save button contradicts.
  function pruneOperationLogIfClean(): void {
    if (operationLog.value.length > 0 && !transactions.value.some(t => t.internal.isModified)) {
      operationLog.value = []
    }
  }

  function resetToImported(): void {
    transactions.value = deepCopy(importedBaseline.value)
    editBaseline.value = deepCopy(importedBaseline.value)
    clearOperationLog()
    // Refresh modified flags (all should be false after reset)
    for (const tx of transactions.value) {
      refreshModifiedFlag(tx)
    }
  }

  function setEditBaseline(): void {
    editBaseline.value = deepCopy(transactions.value)
    for (const tx of transactions.value) {
      refreshModifiedFlag(tx)
    }
  }

  // Post-save: clear isModified on every tx and rebaseline in one shot.
  // A single array reassignment notifies reactive consumers once instead
  // of firing ~2N per-property triggers (forEach setting isModified +
  // refreshModifiedFlag loop), which dominates click-to-toast latency
  // on large result sets.
  function markAllSavedAndRebaseline(): void {
    const next = transactions.value.map(tx => ({
      ...tx,
      internal: { ...tx.internal, isModified: false },
    }))
    transactions.value = next
    editBaseline.value = deepCopy(next)
    clearOperationLog()
  }

  function reinitializeBaselines(): void {
    importedBaseline.value = deepCopy(transactions.value)
    editBaseline.value = deepCopy(transactions.value)
    clearOperationLog()
    for (const tx of transactions.value) {
      refreshModifiedFlag(tx)
    }
  }

  function addToBaselines(tx: TransactionViewModel): void {
    importedBaseline.value.push(deepCopy(tx))
    editBaseline.value.push(deepCopy(tx))
  }

  function clearState(): void {
    transactions.value = []
    importedBaseline.value = []
    editBaseline.value = []
    clearOperationLog()
  }

  return {
    transactions,
    editBaseline,
    operationLog,
    replaceTransactions,
    updateField,
    addPosting,
    removePosting,
    removeTransaction,
    removeTransactions,
    applyBulkOperation,
    undoBulkOperation,
    clearOperationLog,
    resetToImported,
    setEditBaseline,
    markAllSavedAndRebaseline,
    reinitializeBaselines,
    addToBaselines,
    clearState,
  }
}
