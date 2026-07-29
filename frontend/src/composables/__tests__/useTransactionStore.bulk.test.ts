import { toMoney } from '@/utils/money'
import { ref } from 'vue'
import type { TransactionViewModel } from '@/types/transactions'
import { useTransactionStore } from '@/composables/useTransactionStore'
import { makeTx } from '@/test/factories'

function setup() {
  const input = ref<TransactionViewModel[]>([
    makeTx({
      id: 'tx-1',
      tags: [],
      postings: [
        { account: 'Expenses:Fees', amount: toMoney(10), currency: 'USD' },
        { account: 'Assets:Bank', amount: toMoney(-10), currency: 'USD' },
      ],
    }),
    makeTx({
      id: 'tx-2',
      tags: [],
      postings: [
        { account: 'Expenses:Fees', amount: toMoney(20), currency: 'USD' },
        { account: 'Assets:Bank', amount: toMoney(-20), currency: 'USD' },
      ],
    }),
    makeTx({
      id: 'tx-3',
      tags: [],
      postings: [
        { account: 'Expenses:Food', amount: toMoney(30), currency: 'USD' },
        { account: 'Assets:Bank', amount: toMoney(-30), currency: 'USD' },
      ],
    }),
  ])
  return useTransactionStore(input)
}

function byId(store: ReturnType<typeof useTransactionStore>, id: string) {
  return store.transactions.value.find(t => t.id === id)!
}

describe('applyBulkOperation', () => {
  it('applies to selected transactions and marks them modified (the Save gotcha)', () => {
    const store = setup()
    store.applyBulkOperation(['tx-1', 'tx-2'], { type: 'addTag', tag: 'q1' })

    expect(byId(store, 'tx-1').tags).toEqual(['q1'])
    expect(byId(store, 'tx-2').tags).toEqual(['q1'])
    // Bulk-edited transactions must be modified so Save picks them up.
    expect(byId(store, 'tx-1').internal.isModified).toBe(true)
    expect(byId(store, 'tx-2').internal.isModified).toBe(true)
    // Unselected transaction untouched.
    expect(byId(store, 'tx-3').tags).toEqual([])
    expect(byId(store, 'tx-3').internal.isModified).toBe(false)
  })

  it('only affects transactions the operation actually changes (selected-but-unaffected subset)', () => {
    const store = setup()
    // Select all three, but only tx-1 and tx-2 use Expenses:Fees.
    const entry = store.applyBulkOperation(['tx-1', 'tx-2', 'tx-3'], {
      type: 'replaceAccount', from: 'Expenses:Fees', to: 'Expenses:SpecialFees',
    })

    expect(entry?.affectedIds.sort()).toEqual(['tx-1', 'tx-2'])
    expect(byId(store, 'tx-1').postings[0].account).toBe('Expenses:SpecialFees')
    expect(byId(store, 'tx-3').postings[0].account).toBe('Expenses:Food')
    expect(byId(store, 'tx-3').internal.isModified).toBe(false)
  })

  it('records a log entry with label and affected ids, and returns null on a pure no-op', () => {
    const store = setup()
    const entry = store.applyBulkOperation(['tx-1'], { type: 'addTag', tag: 'q1' })
    expect(entry).not.toBeNull()
    expect(store.operationLog.value).toHaveLength(1)
    expect(entry!.label).toBe('Add tag #q1')

    const noop = store.applyBulkOperation(['tx-3'], {
      type: 'replaceAccount', from: 'Expenses:DoesNotExist', to: 'Expenses:X',
    })
    expect(noop).toBeNull()
    expect(store.operationLog.value).toHaveLength(1)
  })

  it('chains operations, accumulating the log', () => {
    const store = setup()
    store.applyBulkOperation(['tx-1'], { type: 'addTag', tag: 'q1' })
    store.applyBulkOperation(['tx-1'], { type: 'setFlag', flag: '!' })
    expect(store.operationLog.value.map(e => e.label)).toEqual(['Add tag #q1', 'Set flag !'])
    expect(byId(store, 'tx-1').tags).toEqual(['q1'])
    expect(byId(store, 'tx-1').flag).toBe('!')
  })
})

describe('net-zero pruning', () => {
  it('clears the whole log when a sequence of ops returns the rows to baseline', () => {
    const store = setup()
    store.applyBulkOperation(['tx-1', 'tx-2'], { type: 'addTag', tag: 'bar' })
    store.applyBulkOperation(['tx-1', 'tx-2'], { type: 'addTag', tag: 'foo' })
    expect(store.operationLog.value).toHaveLength(2)

    store.applyBulkOperation(['tx-1', 'tx-2'], { type: 'removeTag', tag: 'bar' })
    expect(store.operationLog.value).toHaveLength(3) // still one tag left → still staged

    store.applyBulkOperation(['tx-1', 'tx-2'], { type: 'removeTag', tag: 'foo' })
    // Back to baseline → nothing staged → log cleared.
    expect(store.operationLog.value).toHaveLength(0)
    expect(byId(store, 'tx-1').internal.isModified).toBe(false)
    expect(byId(store, 'tx-1').tags).toEqual([])
  })

  it('clears the log when a manual field revert returns the rows to baseline', () => {
    const store = setup()
    store.applyBulkOperation(['tx-1'], { type: 'addTag', tag: 'bar' })
    expect(store.operationLog.value).toHaveLength(1)
    // Manually clear the tag via the tags_links field → back to baseline.
    store.updateField('tx-1', 'tags_links', '')
    expect(store.operationLog.value).toHaveLength(0)
  })
})

describe('undoBulkOperation', () => {
  it('restores affected transactions and drops the log entry', () => {
    const store = setup()
    const entry = store.applyBulkOperation(['tx-1', 'tx-2'], { type: 'addTag', tag: 'q1' })!
    store.undoBulkOperation(entry.id)

    expect(byId(store, 'tx-1').tags).toEqual([])
    expect(byId(store, 'tx-2').tags).toEqual([])
    expect(byId(store, 'tx-1').internal.isModified).toBe(false)
    expect(store.operationLog.value).toHaveLength(0)
  })
})

describe('operation log lifecycle', () => {
  it('clears the log on reset, rebaseline, and reinitialize', () => {
    const store = setup()

    store.applyBulkOperation(['tx-1'], { type: 'addTag', tag: 'q1' })
    store.resetToImported()
    expect(store.operationLog.value).toHaveLength(0)
    expect(byId(store, 'tx-1').tags).toEqual([])

    store.applyBulkOperation(['tx-1'], { type: 'addTag', tag: 'q2' })
    store.markAllSavedAndRebaseline()
    expect(store.operationLog.value).toHaveLength(0)
    // After save-rebaseline the tag persists but is no longer "modified".
    expect(byId(store, 'tx-1').tags).toEqual(['q2'])
    expect(byId(store, 'tx-1').internal.isModified).toBe(false)

    store.applyBulkOperation(['tx-1'], { type: 'addTag', tag: 'q3' })
    store.reinitializeBaselines()
    expect(store.operationLog.value).toHaveLength(0)
  })
})
