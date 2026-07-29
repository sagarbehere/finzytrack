import { toMoney } from '@/utils/money'
import { makeTx } from '@/test/factories'
import {
  applyOperationToTransaction,
  describeOperation,
  isEditableMetaKey,
  isValidMetaKey,
  type BulkOperation,
} from '@/utils/bulkOperations'

describe('applyOperationToTransaction', () => {
  describe('replaceAccount', () => {
    it('replaces every posting that uses the from-account and reports changed', () => {
      const tx = makeTx({
        postings: [
          { account: 'Expenses:Fees', amount: toMoney(10), currency: 'USD' },
          { account: 'Expenses:Fees', amount: toMoney(5), currency: 'USD' },
          { account: 'Assets:Bank', amount: toMoney(-15), currency: 'USD' },
        ],
      })
      const changed = applyOperationToTransaction(tx, { type: 'replaceAccount', from: 'Expenses:Fees', to: 'Expenses:SpecialFees' })
      expect(changed).toBe(true)
      expect(tx.postings.map(p => p.account)).toEqual(['Expenses:SpecialFees', 'Expenses:SpecialFees', 'Assets:Bank'])
    })

    it('is a no-op (returns false) when no posting uses the from-account', () => {
      const tx = makeTx({ postings: [{ account: 'Assets:Bank', amount: toMoney(-15), currency: 'USD' }] })
      const changed = applyOperationToTransaction(tx, { type: 'replaceAccount', from: 'Expenses:Fees', to: 'Expenses:SpecialFees' })
      expect(changed).toBe(false)
      expect(tx.postings[0].account).toBe('Assets:Bank')
    })

    it('keeps source_account in step when it pointed at the replaced account', () => {
      const tx = makeTx({
        meta: { source_account: 'Assets:Bank' },
        postings: [
          { account: 'Assets:Bank', amount: toMoney(-15), currency: 'USD' },
          { account: 'Expenses:Fees', amount: toMoney(15), currency: 'USD' },
        ],
      })
      applyOperationToTransaction(tx, { type: 'replaceAccount', from: 'Assets:Bank', to: 'Assets:Checking' })
      expect(tx.meta['source_account']).toBe('Assets:Checking')
    })

    it('leaves source_account untouched when it pointed elsewhere', () => {
      const tx = makeTx({
        meta: { source_account: 'Assets:Bank' },
        postings: [{ account: 'Expenses:Fees', amount: toMoney(15), currency: 'USD' }],
      })
      applyOperationToTransaction(tx, { type: 'replaceAccount', from: 'Expenses:Fees', to: 'Expenses:SpecialFees' })
      expect(tx.meta['source_account']).toBe('Assets:Bank')
    })

    it('does not touch amount/currency (balance-neutral)', () => {
      const tx = makeTx({ postings: [{ account: 'Expenses:Fees', amount: toMoney(15), currency: 'USD' }] })
      applyOperationToTransaction(tx, { type: 'replaceAccount', from: 'Expenses:Fees', to: 'Expenses:X' })
      expect(tx.postings[0].amount).toEqual(toMoney(15))
      expect(tx.postings[0].currency).toBe('USD')
    })
  })

  describe('tags', () => {
    it('adds a tag, stripping a leading #, and dedupes', () => {
      const tx = makeTx({ tags: ['existing'] })
      expect(applyOperationToTransaction(tx, { type: 'addTag', tag: '#trip' })).toBe(true)
      expect(tx.tags).toEqual(['existing', 'trip'])
      expect(applyOperationToTransaction(tx, { type: 'addTag', tag: 'trip' })).toBe(false)
      expect(tx.tags).toEqual(['existing', 'trip'])
    })

    it('removes a tag only when present', () => {
      const tx = makeTx({ tags: ['trip', 'keep'] })
      expect(applyOperationToTransaction(tx, { type: 'removeTag', tag: 'trip' })).toBe(true)
      expect(tx.tags).toEqual(['keep'])
      expect(applyOperationToTransaction(tx, { type: 'removeTag', tag: 'absent' })).toBe(false)
    })
  })

  describe('links', () => {
    it('adds a link, stripping a leading ^, and dedupes', () => {
      const tx = makeTx({ links: [] })
      expect(applyOperationToTransaction(tx, { type: 'addLink', link: '^invoice-1' })).toBe(true)
      expect(tx.links).toEqual(['invoice-1'])
      expect(applyOperationToTransaction(tx, { type: 'addLink', link: 'invoice-1' })).toBe(false)
    })

    it('removes a link only when present', () => {
      const tx = makeTx({ links: ['invoice-1'] })
      expect(applyOperationToTransaction(tx, { type: 'removeLink', link: 'invoice-1' })).toBe(true)
      expect(tx.links).toEqual([])
    })
  })

  describe('flag and payee', () => {
    it('sets the flag when different, no-op when same', () => {
      const tx = makeTx({ flag: '*' })
      expect(applyOperationToTransaction(tx, { type: 'setFlag', flag: '!' })).toBe(true)
      expect(tx.flag).toBe('!')
      expect(applyOperationToTransaction(tx, { type: 'setFlag', flag: '!' })).toBe(false)
    })

    it('sets the payee when different', () => {
      const tx = makeTx({ payee: 'Old' })
      expect(applyOperationToTransaction(tx, { type: 'setPayee', payee: 'New' })).toBe(true)
      expect(tx.payee).toBe('New')
    })

    it('appends to payee with a space, or sets it when empty', () => {
      const tx = makeTx({ payee: 'Acme' })
      expect(applyOperationToTransaction(tx, { type: 'appendPayee', text: '(refund)' })).toBe(true)
      expect(tx.payee).toBe('Acme (refund)')

      const empty = makeTx({ payee: '' })
      applyOperationToTransaction(empty, { type: 'appendPayee', text: 'Acme' })
      expect(empty.payee).toBe('Acme')

      expect(applyOperationToTransaction(tx, { type: 'appendPayee', text: '   ' })).toBe(false)
    })

    it('sets and appends narration', () => {
      const tx = makeTx({ narration: 'Old' })
      expect(applyOperationToTransaction(tx, { type: 'setNarration', narration: 'New' })).toBe(true)
      expect(tx.narration).toBe('New')
      expect(applyOperationToTransaction(tx, { type: 'appendNarration', text: 'extra' })).toBe(true)
      expect(tx.narration).toBe('New extra')
    })
  })

  describe('metadata', () => {
    it('sets, overwrites, and no-ops metadata', () => {
      const tx = makeTx({ meta: {} })
      expect(applyOperationToTransaction(tx, { type: 'setMetadata', key: 'project', value: 'q1' })).toBe(true)
      expect(tx.meta['project']).toBe('q1')
      expect(applyOperationToTransaction(tx, { type: 'setMetadata', key: 'project', value: 'q1' })).toBe(false)
      expect(applyOperationToTransaction(tx, { type: 'setMetadata', key: 'project', value: 'q2' })).toBe(true)
      expect(tx.meta['project']).toBe('q2')
    })

    it('removes metadata only when present', () => {
      const tx = makeTx({ meta: { project: 'q1' } })
      expect(applyOperationToTransaction(tx, { type: 'removeMetadata', key: 'project' })).toBe(true)
      expect('project' in tx.meta).toBe(false)
      expect(applyOperationToTransaction(tx, { type: 'removeMetadata', key: 'project' })).toBe(false)
    })

    it('renames a metadata key, preserving the value', () => {
      const tx = makeTx({ meta: { proj: 'q1' } })
      expect(applyOperationToTransaction(tx, { type: 'renameMetadata', from: 'proj', to: 'project' })).toBe(true)
      expect(tx.meta).toEqual({ project: 'q1' })
    })

    it('refuses to set/remove/rename protected keys', () => {
      const tx = makeTx({ meta: { source_account: 'Assets:Bank', id: 'abc' } })
      expect(applyOperationToTransaction(tx, { type: 'setMetadata', key: 'source_account', value: 'x' })).toBe(false)
      expect(applyOperationToTransaction(tx, { type: 'removeMetadata', key: 'id' })).toBe(false)
      expect(applyOperationToTransaction(tx, { type: 'renameMetadata', from: 'source_account', to: 'foo' })).toBe(false)
      expect(tx.meta['source_account']).toBe('Assets:Bank')
      expect(tx.meta['id']).toBe('abc')
    })
  })
})

describe('metadata key helpers', () => {
  it('flags protected keys as non-editable', () => {
    expect(isEditableMetaKey('project')).toBe(true)
    expect(isEditableMetaKey('id')).toBe(false)
    expect(isEditableMetaKey('content_hash')).toBe(false)
    expect(isEditableMetaKey('source_account')).toBe(false)
    expect(isEditableMetaKey('filename')).toBe(false)
  })

  it('validates Beancount key syntax', () => {
    expect(isValidMetaKey('project')).toBe(true)
    expect(isValidMetaKey('invoice-no')).toBe(true)
    expect(isValidMetaKey('claim_amount')).toBe(true)
    expect(isValidMetaKey('Project')).toBe(false)
    expect(isValidMetaKey('1st')).toBe(false)
    expect(isValidMetaKey('has space')).toBe(false)
    expect(isValidMetaKey('')).toBe(false)
  })
})

describe('describeOperation', () => {
  it('produces intent-level labels', () => {
    const cases: [BulkOperation, string][] = [
      [{ type: 'replaceAccount', from: 'A', to: 'B' }, 'Replace account A → B'],
      [{ type: 'addTag', tag: '#trip' }, 'Add tag #trip'],
      [{ type: 'setFlag', flag: '!' }, 'Set flag !'],
      [{ type: 'setMetadata', key: 'project', value: 'q1' }, 'Set metadata project = q1'],
      [{ type: 'renameMetadata', from: 'a', to: 'b' }, 'Rename metadata a → b'],
    ]
    for (const [op, label] of cases) {
      expect(describeOperation(op)).toBe(label)
    }
  })
})
