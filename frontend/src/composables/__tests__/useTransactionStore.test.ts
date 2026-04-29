import { ref } from 'vue'
import type { TransactionViewModel } from '@/types/transactions'
import { useTransactionStore } from '@/composables/useTransactionStore'
import { makeTx } from '@/test/factories'

function setup(transactions?: TransactionViewModel[]) {
  const input = ref(transactions ?? [
    makeTx({
      id: 'tx-1',
      payee: 'Original',
      postings: [
        { account: 'Expenses:Food', amount: 50, currency: 'USD' },
        { account: 'Assets:Bank', amount: -50, currency: 'USD' },
      ],
    }),
  ])
  return useTransactionStore(input)
}

describe('updateField', () => {
  it('updates a transaction-level string field', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'New Payee')
    expect(store.transactions.value[0].payee).toBe('New Payee')
  })

  it('updates a transaction-level date field', () => {
    const store = setup()
    store.updateField('tx-1', 'date', '2025-06-01')
    expect(store.transactions.value[0].date).toBe('2025-06-01')
  })

  it('updates memo to a value', () => {
    const store = setup()
    store.updateField('tx-1', 'memo', 'some note')
    expect(store.transactions.value[0].memo).toBe('some note')
  })

  it('updates memo to undefined', () => {
    const store = setup()
    store.updateField('tx-1', 'memo', undefined)
    expect(store.transactions.value[0].memo).toBe(undefined)
  })

  it('updates a posting account', () => {
    const store = setup()
    store.updateField('tx-1', 'postings.0.account', 'Expenses:Dining')
    expect(store.transactions.value[0].postings[0].account).toBe('Expenses:Dining')
  })

  it('updates a posting amount', () => {
    const store = setup()
    store.updateField('tx-1', 'postings.0.amount', 75)
    expect(store.transactions.value[0].postings[0].amount).toBe(75)
  })

  it('updates a posting currency', () => {
    const store = setup()
    store.updateField('tx-1', 'postings.1.currency', 'EUR')
    expect(store.transactions.value[0].postings[1].currency).toBe('EUR')
  })

  it('sets posting amount to null for empty string input', () => {
    const store = setup()
    store.updateField('tx-1', 'postings.0.amount', null)
    expect(store.transactions.value[0].postings[0].amount).toBe(null)
  })

  it('creates cost object when setting cost.amount on a posting without cost', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        date: '2025-01-15',
        postings: [{ account: 'Assets:Stock', amount: 10, currency: 'AAPL', cost: undefined }],
      }),
    ])
    store.updateField('tx-1', 'postings.0.cost.amount', 150)
    expect(store.transactions.value[0].postings[0].cost).toBeDefined()
    expect(store.transactions.value[0].postings[0].cost!.amount).toBe(150)
    expect(store.transactions.value[0].postings[0].cost!.date).toBe('2025-01-15')
  })

  it('updates existing cost amount without clobbering other cost fields', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        postings: [{ amount: 10, currency: 'AAPL', cost: { amount: 100, currency: 'USD', date: '2025-01-01' } }],
      }),
    ])
    store.updateField('tx-1', 'postings.0.cost.amount', 200)
    const cost = store.transactions.value[0].postings[0].cost!
    expect(cost.amount).toBe(200)
    expect(cost.currency).toBe('USD')
    expect(cost.date).toBe('2025-01-01')
  })

  it('creates price object when setting price.amount on a posting without price', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        postings: [{ amount: 100, currency: 'EUR', price: undefined }],
      }),
    ])
    store.updateField('tx-1', 'postings.0.price.amount', 1.5)
    expect(store.transactions.value[0].postings[0].price).toBeDefined()
    expect(store.transactions.value[0].postings[0].price!.amount).toBe(1.5)
  })

  it('updates price type', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        postings: [{ amount: 100, currency: 'EUR', price: { amount: 1.5, currency: 'EUR', type: '@' } }],
      }),
    ])
    store.updateField('tx-1', 'postings.0.price.type', '@@')
    expect(store.transactions.value[0].postings[0].price!.type).toBe('@@')
  })

  it('no-ops for nonexistent transaction ID', () => {
    const store = setup()
    const before = JSON.stringify(store.transactions.value)
    store.updateField('nonexistent', 'payee', 'X')
    expect(JSON.stringify(store.transactions.value)).toBe(before)
  })
})

describe('updateField — source_account sync', () => {
  it('syncs source_account when renaming the source posting account', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        meta: { source_account: 'Assets:Checking' },
        postings: [
          { account: 'Assets:Checking', amount: -50, currency: 'USD' },
          { account: 'Expenses:Food', amount: 50, currency: 'USD' },
        ],
      }),
    ])
    store.updateField('tx-1', 'postings.0.account', 'Assets:Savings')
    expect(store.transactions.value[0].meta['source_account']).toBe('Assets:Savings')
  })

  it('does not change source_account when renaming a non-source posting', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        meta: { source_account: 'Assets:Checking' },
        postings: [
          { account: 'Assets:Checking', amount: -50, currency: 'USD' },
          { account: 'Expenses:Food', amount: 50, currency: 'USD' },
        ],
      }),
    ])
    store.updateField('tx-1', 'postings.1.account', 'Expenses:Dining')
    expect(store.transactions.value[0].meta['source_account']).toBe('Assets:Checking')
  })

  it('does not crash when meta has no source_account', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        meta: {},
        postings: [{ account: 'Expenses:Food', amount: 50, currency: 'USD' }],
      }),
    ])
    expect(() => store.updateField('tx-1', 'postings.0.account', 'X')).not.toThrow()
  })
})

describe('updateField — tags_links virtual field', () => {
  it('parses tags and links from combined string', () => {
    const store = setup()
    store.updateField('tx-1', 'tags_links', '#food #dinner ^receipt-123')
    expect(store.transactions.value[0].tags).toEqual(['food', 'dinner'])
    expect(store.transactions.value[0].links).toEqual(['receipt-123'])
  })

  it('handles empty string', () => {
    const store = setup()
    store.updateField('tx-1', 'tags_links', '')
    expect(store.transactions.value[0].tags).toEqual([])
    expect(store.transactions.value[0].links).toEqual([])
  })

  it('handles string with only tags', () => {
    const store = setup()
    store.updateField('tx-1', 'tags_links', '#a #b')
    expect(store.transactions.value[0].tags).toEqual(['a', 'b'])
    expect(store.transactions.value[0].links).toEqual([])
  })

  it('handles string with only links', () => {
    const store = setup()
    store.updateField('tx-1', 'tags_links', '^x ^y')
    expect(store.transactions.value[0].tags).toEqual([])
    expect(store.transactions.value[0].links).toEqual(['x', 'y'])
  })
})

describe('updateField — modification tracking', () => {
  it('marks transaction as modified after field change', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'Changed')
    expect(store.transactions.value[0].internal.isModified).toBe(true)
  })

  it('marks transaction as unmodified when changed back to baseline value', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'Changed')
    store.updateField('tx-1', 'payee', 'Original')
    expect(store.transactions.value[0].internal.isModified).toBe(false)
  })

  it('marks transaction as modified after posting field change', () => {
    const store = setup()
    store.updateField('tx-1', 'postings.0.amount', 999)
    expect(store.transactions.value[0].internal.isModified).toBe(true)
  })
})

describe('addPosting', () => {
  it('appends a new posting with default values', () => {
    const store = setup()
    const before = store.transactions.value[0].postings.length
    store.addPosting('tx-1')
    expect(store.transactions.value[0].postings.length).toBe(before + 1)
    const newPosting = store.transactions.value[0].postings[store.transactions.value[0].postings.length - 1]
    expect(newPosting.account).toBe('')
    expect(newPosting.amount).toBe(null)
    expect(newPosting.currency).toBe('USD')
  })

  it('marks transaction as modified', () => {
    const store = setup()
    store.addPosting('tx-1')
    expect(store.transactions.value[0].internal.isModified).toBe(true)
  })
})

describe('removePosting', () => {
  it('removes posting at given index', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        postings: [
          { account: 'A', amount: 10, currency: 'USD' },
          { account: 'B', amount: -5, currency: 'USD' },
          { account: 'C', amount: -5, currency: 'USD' },
        ],
      }),
    ])
    store.removePosting('tx-1', 1)
    expect(store.transactions.value[0].postings.length).toBe(2)
    expect(store.transactions.value[0].postings[0].account).toBe('A')
    expect(store.transactions.value[0].postings[1].account).toBe('C')
  })

  it('does not remove the last posting', () => {
    const store = setup([
      makeTx({ id: 'tx-1', postings: [{ account: 'A', amount: 0, currency: 'USD' }] }),
    ])
    store.removePosting('tx-1', 0)
    expect(store.transactions.value[0].postings.length).toBe(1)
  })

  it('marks transaction as modified', () => {
    const store = setup([
      makeTx({
        id: 'tx-1',
        postings: [
          { account: 'A', amount: 10, currency: 'USD' },
          { account: 'B', amount: -5, currency: 'USD' },
          { account: 'C', amount: -5, currency: 'USD' },
        ],
      }),
    ])
    store.removePosting('tx-1', 1)
    expect(store.transactions.value[0].internal.isModified).toBe(true)
  })
})

describe('removeTransaction', () => {
  it('removes transaction from the array', () => {
    const store = setup([
      makeTx({ id: 'tx-1' }),
      makeTx({ id: 'tx-2' }),
    ])
    store.removeTransaction('tx-1')
    expect(store.transactions.value.length).toBe(1)
    expect(store.transactions.value[0].id).toBe('tx-2')
  })
})

describe('baseline management', () => {
  it('resetToImported restores original transactions', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'Changed')
    store.resetToImported()
    expect(store.transactions.value[0].payee).toBe('Original')
  })

  it('resetToImported also resets editBaseline so isModified returns false', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'Changed')
    store.resetToImported()
    expect(store.transactions.value[0].internal.isModified).toBe(false)
  })

  it('setEditBaseline makes current state the new comparison point', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'Changed')
    expect(store.transactions.value[0].internal.isModified).toBe(true)
    store.setEditBaseline()
    expect(store.transactions.value[0].internal.isModified).toBe(false)
  })

  it('after setEditBaseline, further changes are detected', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'Changed')
    store.setEditBaseline()
    store.updateField('tx-1', 'payee', 'Changed Again')
    expect(store.transactions.value[0].internal.isModified).toBe(true)
  })

  it('reinitializeBaselines resets both baselines to current state', () => {
    const store = setup()
    store.updateField('tx-1', 'payee', 'Changed')
    store.reinitializeBaselines()
    expect(store.transactions.value[0].internal.isModified).toBe(false)
    store.resetToImported()
    expect(store.transactions.value[0].payee).toBe('Changed')
  })

  it('addToBaselines adds transaction to both baselines', () => {
    const store = setup()
    const newTx = makeTx({ id: 'tx-new', payee: 'New Tx' })
    store.transactions.value.push(newTx)
    store.addToBaselines(newTx)
    store.updateField('tx-new', 'payee', 'Modified New')
    expect(store.transactions.value.find(t => t.id === 'tx-new')!.internal.isModified).toBe(true)
    store.resetToImported()
    expect(store.transactions.value.find(t => t.id === 'tx-new')!.payee).toBe('New Tx')
  })

  it('clearState empties everything', () => {
    const store = setup()
    store.clearState()
    expect(store.transactions.value).toEqual([])
  })
})

describe('notifyChange identity guarantees', () => {
  function setupTwo() {
    const input = ref([
      makeTx({ id: 'tx-1', payee: 'One', postings: [
        { account: 'Expenses:A', amount: 10, currency: 'USD' },
        { account: 'Assets:Bank', amount: -10, currency: 'USD' },
      ]}),
      makeTx({ id: 'tx-2', payee: 'Two', postings: [
        { account: 'Expenses:B', amount: 20, currency: 'USD' },
        { account: 'Assets:Bank', amount: -20, currency: 'USD' },
      ]}),
    ])
    return useTransactionStore(input)
  }

  it('changed transaction gets a new top-level reference', () => {
    const store = setupTwo()
    const before = store.transactions.value[0]
    store.updateField('tx-1', 'payee', 'Changed')
    expect(store.transactions.value[0]).not.toBe(before)
  })

  it('changed transaction preserves id and all unmodified field values', () => {
    const store = setupTwo()
    const before = store.transactions.value[0]
    const beforeId = before.id
    const beforeDate = before.date
    store.updateField('tx-1', 'payee', 'Changed')
    const after = store.transactions.value[0]
    expect(after.id).toBe(beforeId)
    expect(after.id).toBe('tx-1')
    expect(after.date).toBe(beforeDate)
    expect(after.payee).toBe('Changed')
  })

  it('untouched transactions keep their reference', () => {
    const store = setupTwo()
    const tx2Before = store.transactions.value[1]
    store.updateField('tx-1', 'payee', 'Changed')
    expect(store.transactions.value[1]).toBe(tx2Before)
  })

  it('addPosting produces a new top-level reference for the touched tx only', () => {
    const store = setupTwo()
    const tx1Before = store.transactions.value[0]
    const tx2Before = store.transactions.value[1]
    store.addPosting('tx-1')
    expect(store.transactions.value[0]).not.toBe(tx1Before)
    expect(store.transactions.value[1]).toBe(tx2Before)
  })

  it('removePosting produces a new top-level reference for the touched tx only', () => {
    const store = setupTwo()
    const tx1Before = store.transactions.value[0]
    const tx2Before = store.transactions.value[1]
    store.removePosting('tx-1', 1)
    expect(store.transactions.value[0]).not.toBe(tx1Before)
    expect(store.transactions.value[1]).toBe(tx2Before)
  })

  it('shallow clone does not deep-copy nested objects (perf invariant)', () => {
    const store = setupTwo()
    const postingsBefore = store.transactions.value[0].postings
    const internalBefore = store.transactions.value[0].internal
    store.updateField('tx-1', 'payee', 'Changed')
    expect(store.transactions.value[0].postings).toBe(postingsBefore)
    expect(store.transactions.value[0].internal).toBe(internalBefore)
  })
})

describe('markAllSavedAndRebaseline', () => {
  function setupModified() {
    const input = ref([
      makeTx({ id: 'tx-1', payee: 'A' }),
      makeTx({ id: 'tx-2', payee: 'B' }),
    ])
    const store = useTransactionStore(input)
    store.updateField('tx-1', 'payee', 'A-edited')
    store.updateField('tx-2', 'payee', 'B-edited')
    return store
  }

  it('clears isModified on every transaction', () => {
    const store = setupModified()
    expect(store.transactions.value[0].internal.isModified).toBe(true)
    expect(store.transactions.value[1].internal.isModified).toBe(true)
    store.markAllSavedAndRebaseline()
    expect(store.transactions.value[0].internal.isModified).toBe(false)
    expect(store.transactions.value[1].internal.isModified).toBe(false)
  })

  it('preserves all field values including ids', () => {
    const store = setupModified()
    store.markAllSavedAndRebaseline()
    expect(store.transactions.value[0].id).toBe('tx-1')
    expect(store.transactions.value[0].payee).toBe('A-edited')
    expect(store.transactions.value[1].id).toBe('tx-2')
    expect(store.transactions.value[1].payee).toBe('B-edited')
  })

  it('rebaselines so subsequent updateField with the saved value is not flagged', () => {
    const store = setupModified()
    store.markAllSavedAndRebaseline()
    // Re-applying the same value should not flag the tx as modified
    store.updateField('tx-1', 'payee', 'A-edited')
    expect(store.transactions.value[0].internal.isModified).toBe(false)
  })

  it('flags tx as modified again only when changed away from new baseline', () => {
    const store = setupModified()
    store.markAllSavedAndRebaseline()
    store.updateField('tx-1', 'payee', 'A-different')
    expect(store.transactions.value[0].internal.isModified).toBe(true)
  })

  it('produces a single new top-level array reference', () => {
    const store = setupModified()
    const before = store.transactions.value
    store.markAllSavedAndRebaseline()
    expect(store.transactions.value).not.toBe(before)
  })
})
