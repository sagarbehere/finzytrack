import { toMoney } from '@/utils/money'
import { isTransactionBalanced } from '@/utils/transactions'
import { makeTx } from '@/test/factories'

describe('isTransactionBalanced', () => {
  it('balanced: two postings in same currency sum to zero', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(100), currency: 'USD' },
        { amount: toMoney(-100), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('unbalanced: amounts do not sum to zero', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(100), currency: 'USD' },
        { amount: toMoney(-90), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)
  })

  it('unbalanced: 1-cent off is no longer tolerated (Decimal is exact)', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(33.33), currency: 'USD' },
        { amount: toMoney(33.33), currency: 'USD' },
        { amount: toMoney(-66.67), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)
  })

  it('balanced: multi-currency with cost basis', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(10), currency: 'AAPL', cost: { amount: toMoney(150), currency: 'USD' } },
        { amount: toMoney(-1500), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: per-unit price (@)', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(100), currency: 'EUR', price: { amount: toMoney(1.1), currency: 'USD', type: '@' } },
        { amount: toMoney(-110), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: total price (@@)', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(100), currency: 'EUR', price: { amount: toMoney(110), currency: 'USD', type: '@@' } },
        { amount: toMoney(-110), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: total price (@@) with negative posting', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(-100), currency: 'EUR', price: { amount: toMoney(110), currency: 'USD', type: '@@' } },
        { amount: toMoney(110), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: priced transaction with per-unit precision drift (real-world @@ case)', () => {
    // Simulates: 7,839,929.89 INR @@ 85,000 USD → beancount stores as
    // @ 0.010841934 USD/INR, multiplying back yields 84,999.999...something,
    // which is ~1 cent off the -85,000 USD posting. Tolerance proportional
    // to priced units (1e-8 × ~7.8M ≈ 0.08) absorbs that.
    const tx = makeTx({
      postings: [
        { amount: toMoney('7839929.89'), currency: 'INR', price: { amount: toMoney('0.010841934'), currency: 'USD', type: '@' } },
        { amount: toMoney('-85000.00'), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('unbalanced: large priced drift exceeds proportional tolerance', () => {
    // Same units but the price is wrong by enough that the imbalance
    // exceeds the proportional tolerance (1e-8 × 7.8M ≈ 0.08).
    const tx = makeTx({
      postings: [
        { amount: toMoney('7839929.89'), currency: 'INR', price: { amount: toMoney('0.011'), currency: 'USD', type: '@' } },
        { amount: toMoney('-85000.00'), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)
  })

  it('handles null amounts as zero', () => {
    const tx = makeTx({
      postings: [
        { amount: null, currency: 'USD' },
        { amount: toMoney(0), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('unbalanced: multi-currency without cost/price', () => {
    const tx = makeTx({
      postings: [
        { amount: toMoney(100), currency: 'USD' },
        { amount: toMoney(-90), currency: 'EUR' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)
  })

  it('balanced: single posting with zero amount', () => {
    const tx = makeTx({
      postings: [{ amount: toMoney(0), currency: 'USD' }],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })
})
