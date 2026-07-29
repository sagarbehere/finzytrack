/**
 * Helpers for bulk autocategorization of *existing* ledger transactions.
 *
 * Scope (see dev-docs/bulk-edits.md §10): only transactions with exactly one
 * posting whose account is the configured "unknown" account are targets — i.e.
 * "resolve the single Expenses:Unknown posting." Transactions with zero or
 * multiple unknown postings are skipped (ambiguous). The `sourceAccount` (the
 * other posting's account) is sent as AI prompt context.
 */
import type { TransactionViewModel } from '@/types/transactions'

export interface AutocategorizeTarget {
  txId: string
  /** Index of the single posting to resolve (its account is the unknown account). */
  postingIndex: number
  /** The known posting's account — AI prompt context. '' if there is no other posting. */
  sourceAccount: string
  payee: string
  memo?: string
  narration: string
}

/**
 * Returns a target for `tx` iff it has exactly one posting equal to
 * `unknownAccount`; otherwise null (nothing to resolve, or ambiguous).
 */
export function autocategorizeTarget(
  tx: TransactionViewModel,
  unknownAccount: string,
): AutocategorizeTarget | null {
  const unknownIndices: number[] = []
  tx.postings.forEach((p, i) => {
    if (p.account === unknownAccount) unknownIndices.push(i)
  })
  if (unknownIndices.length !== 1) return null

  const sourceAccount = tx.postings.find(p => p.account !== unknownAccount)?.account ?? ''
  return {
    txId: tx.id,
    postingIndex: unknownIndices[0],
    sourceAccount,
    payee: tx.payee,
    memo: tx.memo,
    narration: tx.narration,
  }
}

/** The subset of `txns` that can be autocategorized, as targets. */
export function autocategorizeTargets(
  txns: TransactionViewModel[],
  unknownAccount: string,
): AutocategorizeTarget[] {
  const out: AutocategorizeTarget[] = []
  for (const tx of txns) {
    const target = autocategorizeTarget(tx, unknownAccount)
    if (target) out.push(target)
  }
  return out
}
