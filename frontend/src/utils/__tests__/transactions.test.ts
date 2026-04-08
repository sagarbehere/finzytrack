import { isTransactionBalanced } from '@/utils/transactions'
import { makeTx } from '@/test/factories'

describe('isTransactionBalanced', () => {
  it('balanced: two postings in same currency sum to zero', () => {
    const tx = makeTx({
      postings: [
        { amount: 100, currency: 'USD' },
        { amount: -100, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('unbalanced: amounts do not sum to zero', () => {
    const tx = makeTx({
      postings: [
        { amount: 100, currency: 'USD' },
        { amount: -90, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)
  })

  it('balanced: within 1 cent tolerance', () => {
    const tx = makeTx({
      postings: [
        { amount: 33.33, currency: 'USD' },
        { amount: 33.33, currency: 'USD' },
        { amount: -66.67, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: multi-currency with cost basis', () => {
    const tx = makeTx({
      postings: [
        { amount: 10, currency: 'AAPL', cost: { amount: 150, currency: 'USD' } },
        { amount: -1500, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: per-unit price (@)', () => {
    const tx = makeTx({
      postings: [
        { amount: 100, currency: 'EUR', price: { amount: 1.1, currency: 'USD', type: '@' } },
        { amount: -110, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: total price (@@)', () => {
    const tx = makeTx({
      postings: [
        { amount: 100, currency: 'EUR', price: { amount: 110, currency: 'USD', type: '@@' } },
        { amount: -110, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('balanced: total price (@@) with negative posting', () => {
    const tx = makeTx({
      postings: [
        { amount: -100, currency: 'EUR', price: { amount: 110, currency: 'USD', type: '@@' } },
        { amount: 110, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('handles null amounts as zero', () => {
    const tx = makeTx({
      postings: [
        { amount: null, currency: 'USD' },
        { amount: 0, currency: 'USD' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })

  it('unbalanced: multi-currency without cost/price', () => {
    const tx = makeTx({
      postings: [
        { amount: 100, currency: 'USD' },
        { amount: -90, currency: 'EUR' },
      ],
    })
    expect(isTransactionBalanced(tx)).toBe(false)
  })

  it('balanced: single posting with zero amount', () => {
    const tx = makeTx({
      postings: [{ amount: 0, currency: 'USD' }],
    })
    expect(isTransactionBalanced(tx)).toBe(true)
  })
})
