import type { TransactionViewModel } from '@/types/transactions'
import { abs, add, eq, gt, mul, neg, sign, zero, type Money } from '@/utils/money'

const ZERO = zero()

// Beancount normalizes `@@ TOTAL` into a per-unit price (`Amount(TOTAL/units, ccy)`)
// at parse time. The division has finite precision, so multiplying back to
// reconstruct the total introduces drift proportional to the priced units.
// PRICED_UNITS_EPSILON is a conservative upper bound on per-unit price
// precision: 1e-8 means for every 1 unit of priced amount, we allow 1e-8
// units of imbalance — far more than the actual drift but tight enough that
// genuine off-by-one errors in normal-sized transactions still fail.
const PRICED_UNITS_EPSILON = 1e-8

export function isTransactionBalanced(transaction: TransactionViewModel): boolean {
  // Decimal arithmetic is exact, so same-currency transactions must sum to
  // exactly zero per currency — no tolerance fudge, so a 1-cent off-by-one
  // surfaces. For priced or costed postings we allow a small tolerance
  // (see PRICED_UNITS_EPSILON) because the per-unit normalization that
  // Beancount applies at parse time loses precision proportional to the
  // priced units.
  const currencyTotals: Record<string, Money> = {}
  let pricedUnits: Money = ZERO

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
      pricedUnits = add(pricedUnits, abs(amount))
    } else if (posting.cost?.amount && posting.cost?.currency) {
      effectiveCurrency = posting.cost.currency
      effectiveAmount = mul(amount, posting.cost.amount)
      pricedUnits = add(pricedUnits, abs(amount))
    } else {
      effectiveCurrency = posting.currency || 'UNKNOWN'
      effectiveAmount = amount
    }

    currencyTotals[effectiveCurrency] = add(currencyTotals[effectiveCurrency] ?? ZERO, effectiveAmount)
  }

  const tolerance: Money = eq(pricedUnits, ZERO) ? ZERO : mul(pricedUnits, PRICED_UNITS_EPSILON)

  return Object.values(currencyTotals).every(total => !gt(abs(total), tolerance))
}
