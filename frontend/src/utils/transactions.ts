import type { TransactionViewModel } from '@/types/transactions'
import { abs, add, gt, mul, neg, sign, toMoney, zero, type Money } from '@/utils/money'

// Balance tolerance: 0.01 (one cent) matches Beancount's default rounding
// tolerance for fiat currencies. For commodities with higher precision the
// tolerance is irrelevant in practice because exact Decimal sums hit zero.
const BALANCE_TOLERANCE = toMoney('0.01')

export function isTransactionBalanced(transaction: TransactionViewModel): boolean {
  const currencyTotals: Record<string, Money> = {}

  for (const posting of transaction.postings) {
    const amount: Money = posting.amount ?? zero()
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

    currencyTotals[effectiveCurrency] = add(currencyTotals[effectiveCurrency] ?? zero(), effectiveAmount)
  }

  return Object.values(currencyTotals).every(total => !gt(abs(total), BALANCE_TOLERANCE))
}
