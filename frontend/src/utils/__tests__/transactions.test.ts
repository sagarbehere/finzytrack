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
