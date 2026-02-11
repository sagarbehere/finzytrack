import type { TransactionViewModel } from '@/types/transactions'

export function isTransactionBalanced(transaction: TransactionViewModel): boolean {
  const currencyTotals: Record<string, number> = {}

  for (const posting of transaction.postings) {
    const amount = posting.amount || 0
    let effectiveCurrency: string
    let effectiveAmountInCents: number

    if (posting.price?.amount && posting.price?.currency) {
      effectiveCurrency = posting.price.currency
      if (posting.price.type === '@@') {
        // @@ = total price: sign follows the posting amount
        effectiveAmountInCents = Math.round((amount >= 0 ? posting.price.amount : -posting.price.amount) * 100)
      } else {
        // @ = per-unit price (default)
        effectiveAmountInCents = Math.round(amount * posting.price.amount * 100)
      }
    } else if (posting.cost?.amount && posting.cost?.currency) {
      effectiveCurrency = posting.cost.currency
      effectiveAmountInCents = Math.round(amount * posting.cost.amount * 100)
    } else {
      effectiveCurrency = posting.currency || 'UNKNOWN'
      effectiveAmountInCents = Math.round(amount * 100)
    }

    currencyTotals[effectiveCurrency] = (currencyTotals[effectiveCurrency] || 0) + effectiveAmountInCents
  }

  // Balanced if every currency bucket is within tolerance (1 cent)
  return Object.values(currencyTotals).every(total => Math.abs(total) <= 1)
}
