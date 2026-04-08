import { normalizeForComparison, isModified } from '@/utils/transactionModification'
import { makeTx, makePosting } from '@/test/factories'

describe('normalizeForComparison', () => {
  it('produces identical output for identical transactions', () => {
    const a = makeTx({ id: 'tx-1', payee: 'Store', postings: [{ amount: 50, currency: 'USD' }] })
    const b = makeTx({ id: 'tx-1', payee: 'Store', postings: [{ amount: 50, currency: 'USD' }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('sorts tags before comparison', () => {
    const a = makeTx({ tags: ['b', 'a'] })
    const b = makeTx({ tags: ['a', 'b'] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('sorts links before comparison', () => {
    const a = makeTx({ links: ['y', 'x'] })
    const b = makeTx({ links: ['x', 'y'] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('normalizes undefined cost to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', cost: undefined }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD' }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('normalizes empty cost to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', cost: { amount: undefined } }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', cost: undefined }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('normalizes cost with zero amount to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', cost: { amount: 0 } }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', cost: undefined }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('preserves non-empty cost', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', cost: { amount: 150, currency: 'USD', date: '2025-01-01' } }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', cost: undefined }] })
    expect(normalizeForComparison(a)).not.toBe(normalizeForComparison(b))
  })

  it('normalizes undefined price to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', price: undefined }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD' }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('normalizes empty price to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', price: { amount: undefined } }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', price: undefined }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('normalizes price with zero amount to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', price: { amount: 0 } }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', price: undefined }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('preserves non-empty price', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', price: { amount: 1.5, currency: 'EUR', type: '@' } }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', price: undefined }] })
    expect(normalizeForComparison(a)).not.toBe(normalizeForComparison(b))
  })

  it('normalizes undefined meta to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', meta: undefined }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD' }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('normalizes empty meta object to null', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', meta: {} }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', meta: undefined }] })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('preserves non-empty meta', () => {
    const a = makeTx({ postings: [{ amount: 50, currency: 'USD', meta: { source: 'ofx' } }] })
    const b = makeTx({ postings: [{ amount: 50, currency: 'USD', meta: undefined }] })
    expect(normalizeForComparison(a)).not.toBe(normalizeForComparison(b))
  })

  it('ignores the internal field', () => {
    const a = makeTx({ internal: { isNew: false, isModified: false } })
    const b = makeTx({ internal: { isNew: false, isModified: true } })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })

  it('ignores the meta field at transaction level', () => {
    const a = makeTx({ meta: { source_account: 'Assets:Bank', content_hash: 'abc' } })
    const b = makeTx({ meta: { source_account: 'Assets:Other', content_hash: 'xyz' } })
    expect(normalizeForComparison(a)).toBe(normalizeForComparison(b))
  })
})

describe('isModified', () => {
  it('returns false when transaction matches its baseline', () => {
    const tx = makeTx({ id: 'tx-1' })
    const baseline = makeTx({ id: 'tx-1' })
    expect(isModified(tx, [baseline])).toBe(false)
  })

  it('returns false when no baseline exists for the transaction', () => {
    const tx = makeTx({ id: 'tx-1' })
    expect(isModified(tx, [])).toBe(false)
  })

  it('detects changed date', () => {
    const baseline = makeTx({ id: 'tx-1', date: '2025-01-15' })
    const tx = makeTx({ id: 'tx-1', date: '2025-01-16' })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed payee', () => {
    const baseline = makeTx({ id: 'tx-1', payee: 'Store A' })
    const tx = makeTx({ id: 'tx-1', payee: 'Store B' })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed narration', () => {
    const baseline = makeTx({ id: 'tx-1', narration: 'Original' })
    const tx = makeTx({ id: 'tx-1', narration: 'Changed' })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed flag', () => {
    const baseline = makeTx({ id: 'tx-1', flag: '*' })
    const tx = makeTx({ id: 'tx-1', flag: '!' })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed memo', () => {
    const baseline = makeTx({ id: 'tx-1', memo: undefined })
    const tx = makeTx({ id: 'tx-1', memo: 'note' })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects added tag', () => {
    const baseline = makeTx({ id: 'tx-1', tags: [] })
    const tx = makeTx({ id: 'tx-1', tags: ['food'] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects added link', () => {
    const baseline = makeTx({ id: 'tx-1', links: [] })
    const tx = makeTx({ id: 'tx-1', links: ['receipt-123'] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed posting account', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ account: 'Expenses:Food', amount: 50, currency: 'USD' }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ account: 'Expenses:Dining', amount: 50, currency: 'USD' }] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed posting amount', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 50, currency: 'USD' }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 75, currency: 'USD' }] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed posting currency', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ currency: 'USD', amount: 50 }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ currency: 'EUR', amount: 50 }] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects added posting', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 50 }, { amount: -50 }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 50 }, { amount: -50 }, { amount: 0 }] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects removed posting', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 50 }, { amount: -50 }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 50 }] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed cost amount', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 10, currency: 'AAPL', cost: { amount: 100, currency: 'USD' } }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 10, currency: 'AAPL', cost: { amount: 150, currency: 'USD' } }] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('detects changed price type', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 100, currency: 'EUR', price: { amount: 1.5, currency: 'USD', type: '@' } }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 100, currency: 'EUR', price: { amount: 1.5, currency: 'USD', type: '@@' } }] })
    expect(isModified(tx, [baseline])).toBe(true)
  })

  it('treats empty cost same as undefined cost', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 50, currency: 'USD', cost: undefined }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 50, currency: 'USD', cost: { amount: undefined } }] })
    expect(isModified(tx, [baseline])).toBe(false)
  })

  it('treats empty price same as undefined price', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 50, currency: 'USD', price: undefined }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 50, currency: 'USD', price: { amount: undefined } }] })
    expect(isModified(tx, [baseline])).toBe(false)
  })

  it('treats empty meta same as undefined meta', () => {
    const baseline = makeTx({ id: 'tx-1', postings: [{ amount: 50, currency: 'USD', meta: undefined }] })
    const tx = makeTx({ id: 'tx-1', postings: [{ amount: 50, currency: 'USD', meta: {} }] })
    expect(isModified(tx, [baseline])).toBe(false)
  })

  it('returns false when changed back to original', () => {
    const baseline = makeTx({ id: 'tx-1', payee: 'Original' })
    const tx = makeTx({ id: 'tx-1', payee: 'Original' })
    expect(isModified(tx, [baseline])).toBe(false)
  })
})
