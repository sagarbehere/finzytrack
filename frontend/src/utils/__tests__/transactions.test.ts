import { toMoney, type Money } from '@/utils/money'
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

  it('balanced: cost wins over price when a posting has both (sell with lot + price)', () => {
    // Real-world regression: selling a lot records both the cost basis {1.46 USD}
    // and the sale price @ 4.20 USD on the same reducing posting. Beancount balances
    // on the cost (-5000 × 1.46 = -7300), not the price. If price were used instead
    // (-5000 × 4.20 = -21000) the transaction would falsely appear unbalanced.
    const tx = makeTx({
      postings: [
        {
          amount: toMoney(-5000),
          currency: 'AUR',
          cost: { amount: toMoney(1.46), currency: 'USD' },
          price: { amount: toMoney(4.2), currency: 'USD', type: '@' },
        },
        { amount: toMoney(21000), currency: 'USD' },
        { amount: toMoney(-13700), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('unbalanced: cost+price posting that genuinely does not balance still fails', () => {
    // Same shape as above but the USD legs no longer offset the cost weight,
    // confirming the cost-first path doesn't blanket-pass costed postings.
    const tx = makeTx({
      postings: [
        {
          amount: toMoney(-5000),
          currency: 'AUR',
          cost: { amount: toMoney(1.46), currency: 'USD' },
          price: { amount: toMoney(4.2), currency: 'USD', type: '@' },
        },
        { amount: toMoney(21000), currency: 'USD' },
        { amount: toMoney(-14000), currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)
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

  // Beancount accepts a residual up to half the smallest unit written in the currency's
  // plain amounts (0.005 for cents). The Transactions view must agree with it.
  it('balanced: a sale whose gain is rounded to the paisa against an exact cost (as Beancount accepts)', () => {
    const tx = makeTx({
      postings: [
        { amount: '-49.905' as Money, currency: 'ICICI-DYN',
          cost: { amount: '59.1221029874' as Money, currency: 'INR' },
          price: { amount: '120.2279' as Money, currency: 'INR', type: '@' } },
        { amount: '6000.00' as Money, currency: 'INR' },
        { amount: '-3049.51' as Money, currency: 'INR' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)  // residual 0.00145 INR ≤ 0.005
  })

  it('unbalanced: beyond half a paisa is still flagged', () => {
    const tx = makeTx({
      postings: [
        { amount: '-49.905' as Money, currency: 'ICICI-DYN', cost: { amount: '59.1221029874' as Money, currency: 'INR' } },
        { amount: '6000.00' as Money, currency: 'INR' },
        { amount: '-3049.50' as Money, currency: 'INR' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)  // residual 0.0114 INR > 0.005
  })

  it('trailing zeros count, as Beancount reads the written number', () => {
    const tx = makeTx({
      postings: [
        { amount: '10.000' as Money, currency: 'X', cost: { amount: '3.3333333333' as Money, currency: 'EUR' } },
        { amount: '-33.30' as Money, currency: 'EUR' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)  // residual 0.0333 > 0.005
  })
})
