import { type Ref, ref } from 'vue'
import type { TransactionViewModel } from '@/types/transactions'
import { isModified } from '@/utils/transactionModification'

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
    if (path === 'tags_links') {
      const str = (value as string) || ''
      const parts = str.split(/\s+/).filter(p => p)
      tx.tags = parts.filter(p => p.startsWith('#')).map(p => p.substring(1))
      tx.links = parts.filter(p => p.startsWith('^')).map(p => p.substring(1))
      refreshModifiedFlag(tx)
      notifyChange(tx)
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
      posting.cost.amount = value as number | undefined
      if (posting.cost.amount !== undefined && !posting.cost.date) {
        posting.cost.date = tx.date
      }
      refreshModifiedFlag(tx)
      notifyChange(tx)
      return
    }

    // General case: set by path
    setByPath(tx as unknown as Record<string, any>, path, value)
    refreshModifiedFlag(tx)
    notifyChange(tx)
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

  function resetToImported(): void {
    transactions.value = deepCopy(importedBaseline.value)
    editBaseline.value = deepCopy(importedBaseline.value)
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
  }

  function reinitializeBaselines(): void {
    importedBaseline.value = deepCopy(transactions.value)
    editBaseline.value = deepCopy(transactions.value)
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
  }

  return {
    transactions,
    replaceTransactions,
    updateField,
    addPosting,
    removePosting,
    removeTransaction,
    resetToImported,
    setEditBaseline,
    markAllSavedAndRebaseline,
    reinitializeBaselines,
    addToBaselines,
    clearState,
  }
}
