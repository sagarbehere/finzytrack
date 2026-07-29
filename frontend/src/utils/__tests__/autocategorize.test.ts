import { toMoney } from '@/utils/money'
import { makeTx } from '@/test/factories'
import { autocategorizeTarget, autocategorizeTargets } from '@/utils/autocategorize'

const UNKNOWN = 'Expenses:Unknown'

describe('autocategorizeTarget', () => {
  it('targets a transaction with exactly one unknown posting and derives the source account', () => {
    const tx = makeTx({
      id: 'tx-1', payee: 'Coffee Co', memo: 'ref1', narration: 'latte',
      postings: [
        { account: UNKNOWN, amount: toMoney(5), currency: 'USD' },
        { account: 'Assets:Bank', amount: toMoney(-5), currency: 'USD' },
      ],
    })
    expect(autocategorizeTarget(tx, UNKNOWN)).toEqual({
      txId: 'tx-1', postingIndex: 0, sourceAccount: 'Assets:Bank',
      payee: 'Coffee Co', memo: 'ref1', narration: 'latte',
    })
  })

  it('derives the source account regardless of posting order', () => {
    const tx = makeTx({
      id: 'tx-1',
      postings: [
        { account: 'Liabilities:CC', amount: toMoney(-5), currency: 'USD' },
        { account: UNKNOWN, amount: toMoney(5), currency: 'USD' },
      ],
    })
    const target = autocategorizeTarget(tx, UNKNOWN)!
    expect(target.postingIndex).toBe(1)
    expect(target.sourceAccount).toBe('Liabilities:CC')
  })

  it('skips a transaction with no unknown posting', () => {
    const tx = makeTx({ postings: [
      { account: 'Expenses:Food', amount: toMoney(5), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney(-5), currency: 'USD' },
    ] })
    expect(autocategorizeTarget(tx, UNKNOWN)).toBeNull()
  })

  it('skips a transaction with multiple unknown postings (ambiguous)', () => {
    const tx = makeTx({ postings: [
      { account: UNKNOWN, amount: toMoney(3), currency: 'USD' },
      { account: UNKNOWN, amount: toMoney(2), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney(-5), currency: 'USD' },
    ] })
    expect(autocategorizeTarget(tx, UNKNOWN)).toBeNull()
  })
})

describe('autocategorizeTargets', () => {
  it('returns only the resolvable transactions', () => {
    const a = makeTx({ id: 'a', postings: [
      { account: UNKNOWN, amount: toMoney(5), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney(-5), currency: 'USD' },
    ] })
    const b = makeTx({ id: 'b', postings: [
      { account: 'Expenses:Food', amount: toMoney(5), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney(-5), currency: 'USD' },
    ] })
    const targets = autocategorizeTargets([a, b], UNKNOWN)
    expect(targets.map(t => t.txId)).toEqual(['a'])
  })
})
