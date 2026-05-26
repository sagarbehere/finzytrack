import type { TransactionViewModel } from '@/types/transactions'
import { add, eq, mul, neg, sign, zero, type Money } from '@/utils/money'

const ZERO = zero()

export function isTransactionBalanced(transaction: TransactionViewModel): boolean {
  // Decimal arithmetic is exact, so a balanced transaction sums to exactly
  // zero per currency. No tolerance fudge — a 1-cent off-by-one is a real
  // imbalance and the user wants to see it.
  const currencyTotals: Record<string, Money> = {}

  for (const posting of transaction.postings) {
    const amount: Money = posting.amount ?? ZERO
    let effectiveCurrency: string
    let effectiveAmount: Money

    if (posting.price?.amount && posting.price?.currency) {
      effectiveCurrency = posting.price.currency
      if (posting.price.type === '@@') {
        // @@ = total price: sign follows the posting amount
        effectiveAmount = sign(amount) < 0 ? neg(posting.price.amount) : posting.price.amount
      } else {
        // @ = per-unit price (default)
        effectiveAmount = mul(amount, posting.price.amount)
      }
    } else if (posting.cost?.amount && posting.cost?.currency) {
      effectiveCurrency = posting.cost.currency
      effectiveAmount = mul(amount, posting.cost.amount)
    } else {
      effectiveCurrency = posting.currency || 'UNKNOWN'
      effectiveAmount = amount
    }

    currencyTotals[effectiveCurrency] = add(currencyTotals[effectiveCurrency] ?? ZERO, effectiveAmount)
  }

  return Object.values(currencyTotals).every(total => eq(total, ZERO))
}
