import type { TransactionViewModel } from '@/types/transactions'
import type { TableRowData } from '@/composables/useTransactionColumns'

/**
 * Incremental flatten of transactions → posting rows.
 *
 * The cache is keyed by transaction object identity. When a transaction's
 * reference is unchanged (and its position in the list is unchanged), its
 * row array is reused as-is — TanStack/Vue then see identical row.original
 * references and skip re-rendering those cells. When a transaction's
 * reference changes (the store's notifyChange replaces only the touched
 * tx), only that tx's rows are rebuilt.
 *
 * Caller is responsible for holding the cache across calls (it must persist
 * for the lifetime of the table).
 */
export function flattenTransactionRows(
  transactions: TransactionViewModel[],
  cache: Map<TransactionViewModel, TableRowData[]>,
): TableRowData[] {
  const result: TableRowData[] = []
  const seen = new Set<TransactionViewModel>()
  let counter = 0

  for (const tx of transactions) {
    counter++
    seen.add(tx)
    let rows = cache.get(tx)
    // Rebuild if missing OR if the tx moved to a different display position
    // (transactionIndex is part of the row data and must stay accurate).
    if (!rows || rows.length === 0 || rows[0].transactionIndex !== counter) {
      rows = tx.postings.map((posting, index) => ({
        ...posting,
        transaction: tx,
        postingIndex: index,
        isFirstPosting: index === 0,
        isLastPosting: index === tx.postings.length - 1,
        transactionIndex: counter,
      }))
      cache.set(tx, rows)
    }
    result.push(...rows)
  }

  // GC: drop entries for transactions no longer present
  for (const key of cache.keys()) {
    if (!seen.has(key)) cache.delete(key)
  }

  return result
}
