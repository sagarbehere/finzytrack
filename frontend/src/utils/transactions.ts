import type { TransactionViewModel } from '@/types/transactions'
import { abs, add, eq, gt, mul, neg, sign, toMoney, zero, type Money } from '@/utils/money'

const ZERO = zero()

// Beancount normalizes `@@ TOTAL` into a per-unit price (`Amount(TOTAL/units, ccy)`)
// at parse time. The division has finite precision, so multiplying back to
// reconstruct the total introduces drift proportional to the priced units.
// PRICED_UNITS_EPSILON is a conservative upper bound on per-unit price
// precision: 1e-8 means for every 1 unit of priced amount, we allow 1e-8
// units of imbalance — far more than the actual drift but tight enough that
// genuine off-by-one errors in normal-sized transactions still fail.
const PRICED_UNITS_EPSILON = 1e-8

/** Decimal places of a number as written ("6000.00" → 2), which is what Beancount
 *  infers a currency's tolerance from — so trailing zeros count. */
function writtenDecimals(amount: Money): number {
  const text = String(amount).trim()
  if (/e/i.test(text)) return 0 // exponent form: no written precision to infer from
  const dot = text.indexOf('.')
  return dot < 0 ? 0 : text.length - dot - 1
}

export function isTransactionBalanced(transaction: TransactionViewModel): boolean {
  // Decimal arithmetic is exact, so same-currency transactions must sum to
  // exactly zero per currency — no tolerance fudge, so a 1-cent off-by-one
  // surfaces. For priced or costed postings we allow a small tolerance
  // (see PRICED_UNITS_EPSILON) because the per-unit normalization that
  // Beancount applies at parse time loses precision proportional to the
  // priced units.
  const currencyTotals: Record<string, Money> = {}
  // Beancount's inferred tolerance per currency: half the smallest unit written in that
  // currency's plain (no cost, no price) amounts — 0.005 for amounts in cents/paise. A
  // transaction it accepts must not be flagged here (e.g. a sale whose gain is rounded
  // to the cent against an exact cost basis).
  const inferred: Record<string, Money> = {}
  let pricedUnits: Money = ZERO

  for (const posting of transaction.postings) {
    const amount: Money = posting.amount ?? ZERO
    let effectiveCurrency: string
    let effectiveAmount: Money

    // Cost takes precedence over price: Beancount's balancing weight for a
    // posting with a lot is units × cost. A price alongside a cost is a
    // documentary annotation (used for gain/loss elsewhere), not for balancing,
    // so it must not override the cost here. Price is only the balancing weight
    // when there is no cost (e.g. a plain currency conversion, 100 EUR @ 1.08 USD).
    if (posting.cost?.amount && posting.cost?.currency) {
      effectiveCurrency = posting.cost.currency
      effectiveAmount = mul(amount, posting.cost.amount)
      pricedUnits = add(pricedUnits, abs(amount))
    } else if (posting.price?.amount && posting.price?.currency) {
      effectiveCurrency = posting.price.currency
      if (posting.price.type === '@@') {
        // @@ = total price: sign follows the posting amount
        effectiveAmount = sign(amount) < 0 ? neg(posting.price.amount) : posting.price.amount
      } else {
        // @ = per-unit price (default)
        effectiveAmount = mul(amount, posting.price.amount)
      }
      pricedUnits = add(pricedUnits, abs(amount))
    } else {
      effectiveCurrency = posting.currency || 'UNKNOWN'
      effectiveAmount = amount
      const places = writtenDecimals(amount)
      if (places > 0) {
        const half = toMoney(`5e-${places + 1}`)
        if (!inferred[effectiveCurrency] || gt(half, inferred[effectiveCurrency])) inferred[effectiveCurrency] = half
      }
    }

    currencyTotals[effectiveCurrency] = add(currencyTotals[effectiveCurrency] ?? ZERO, effectiveAmount)
  }

  const drift: Money = eq(pricedUnits, ZERO) ? ZERO : mul(pricedUnits, PRICED_UNITS_EPSILON)

  return Object.entries(currencyTotals).every(([currency, total]) => {
    const beancount = inferred[currency]
    const tolerance = beancount && gt(beancount, drift) ? beancount : drift
    return !gt(abs(total), tolerance)
  })
}
