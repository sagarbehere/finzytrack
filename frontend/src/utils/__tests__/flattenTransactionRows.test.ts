import { flattenTransactionRows } from '@/utils/flattenTransactionRows'
import { makeTx } from '@/test/factories'
import type { TransactionViewModel } from '@/types/transactions'
import type { TableRowData } from '@/composables/useTransactionColumns'

describe('flattenTransactionRows', () => {
  it('flattens transactions into one row per posting', () => {
    const tx1 = makeTx({ id: 'a', postings: [
      { account: 'A', amount: 1, currency: 'USD' },
      { account: 'B', amount: -1, currency: 'USD' },
    ]})
    const tx2 = makeTx({ id: 'b', postings: [
      { account: 'C', amount: 2, currency: 'USD' },
    ]})
    const cache = new Map<TransactionViewModel, TableRowData[]>()
    const rows = flattenTransactionRows([tx1, tx2], cache)
    expect(rows).toHaveLength(3)
    expect(rows[0].postingIndex).toBe(0)
    expect(rows[0].isFirstPosting).toBe(true)
    expect(rows[1].isLastPosting).toBe(true)
    expect(rows[2].transaction).toBe(tx2)
  })

  it('reuses row objects across calls when transactions are unchanged', () => {
    const tx1 = makeTx({ id: 'a' })
    const tx2 = makeTx({ id: 'b' })
    const cache = new Map<TransactionViewModel, TableRowData[]>()
    const first = flattenTransactionRows([tx1, tx2], cache)
    const second = flattenTransactionRows([tx1, tx2], cache)
    expect(second[0]).toBe(first[0])
    expect(second[1]).toBe(first[1])
  })

  it('rebuilds rows only for the transaction whose reference changed', () => {
    const tx1 = makeTx({ id: 'a' })
    const tx2 = makeTx({ id: 'b' })
    const cache = new Map<TransactionViewModel, TableRowData[]>()
    const first = flattenTransactionRows([tx1, tx2], cache)

    // Simulate store's per-tx shallow clone for the first transaction
    const tx1Updated = { ...tx1, payee: 'Edited' }
    const second = flattenTransactionRows([tx1Updated, tx2], cache)

    // tx1's rows were rebuilt (different objects)
    expect(second[0]).not.toBe(first[0])
    expect(second[0].transaction).toBe(tx1Updated)
    // tx2's rows are reused
    expect(second.at(-1)).toBe(first.at(-1))
  })

  it('rebuilds rows when a transaction moves to a different position', () => {
    const tx1 = makeTx({ id: 'a' })
    const tx2 = makeTx({ id: 'b' })
    const cache = new Map<TransactionViewModel, TableRowData[]>()
    const first = flattenTransactionRows([tx1, tx2], cache)
    const firstTx1Row = first[0]
    expect(firstTx1Row.transactionIndex).toBe(1)

    // Reorder: tx1 now at index 1
    const second = flattenTransactionRows([tx2, tx1], cache)
    const tx1RowAfter = second.find(r => r.transaction === tx1)!
    expect(tx1RowAfter.transactionIndex).toBe(2)
    expect(tx1RowAfter).not.toBe(firstTx1Row)
  })

  it('drops cache entries for transactions no longer present', () => {
    const tx1 = makeTx({ id: 'a' })
    const tx2 = makeTx({ id: 'b' })
    const cache = new Map<TransactionViewModel, TableRowData[]>()
    flattenTransactionRows([tx1, tx2], cache)
    expect(cache.size).toBe(2)

    flattenTransactionRows([tx1], cache)
    expect(cache.size).toBe(1)
    expect(cache.has(tx1)).toBe(true)
    expect(cache.has(tx2)).toBe(false)
  })

  it('handles empty input', () => {
    const cache = new Map<TransactionViewModel, TableRowData[]>()
    expect(flattenTransactionRows([], cache)).toEqual([])
    expect(cache.size).toBe(0)
  })

  it('assigns sequential transactionIndex starting at 1', () => {
    const single = { account: 'X', amount: 1, currency: 'USD' }
    const txs = [
      makeTx({ id: 'a', postings: [single] }),
      makeTx({ id: 'b', postings: [single] }),
      makeTx({ id: 'c', postings: [single] }),
    ]
    const cache = new Map<TransactionViewModel, TableRowData[]>()
    const rows = flattenTransactionRows(txs, cache)
    expect(rows.map(r => r.transactionIndex)).toEqual([1, 2, 3])
  })
})
