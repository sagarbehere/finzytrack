import {
  editableMetaFields,
  systemMetaFields,
  buildMetaFromFields,
  isDocumentKey,
  isSurfacedElsewhereKey,
} from '@/utils/metadataEditor'

describe('metadataEditor partitioning', () => {
  const meta = {
    project: 'q1',
    invoice: 'INV-1',
    memo: 'a note',
    document: '../documents/x.pdf',
    document2: '../documents/y.pdf',
    id: 'uuid-1',
    content_hash: 'abc',
    source_account: 'Assets:Bank',
    filename: '/ledger.beancount',
    lineno: '42',
  }

  it('exposes only free user keys as editable', () => {
    expect(editableMetaFields(meta)).toEqual([
      { key: 'project', value: 'q1' },
      { key: 'invoice', value: 'INV-1' },
    ])
  })

  it('exposes system keys read-only in a stable order', () => {
    expect(systemMetaFields(meta)).toEqual([
      { key: 'id', value: 'uuid-1' },
      { key: 'content_hash', value: 'abc' },
      { key: 'source_account', value: 'Assets:Bank' },
    ])
  })

  it('recognizes document and surfaced-elsewhere keys', () => {
    expect(isDocumentKey('document')).toBe(true)
    expect(isDocumentKey('document3')).toBe(true)
    expect(isDocumentKey('documentx')).toBe(false)
    expect(isSurfacedElsewhereKey('memo')).toBe(true)
    expect(isSurfacedElsewhereKey('document2')).toBe(true)
    expect(isSurfacedElsewhereKey('project')).toBe(false)
  })

  it('rebuilds meta from edited fields, preserving protected and surfaced keys', () => {
    const rebuilt = buildMetaFromFields(meta, [
      { key: 'project', value: 'q2' }, // edited value
      { key: 'ref', value: '99' },     // added
      // 'invoice' dropped (removed)
    ])
    expect(rebuilt).toEqual({
      // preserved untouched
      memo: 'a note',
      document: '../documents/x.pdf',
      document2: '../documents/y.pdf',
      id: 'uuid-1',
      content_hash: 'abc',
      source_account: 'Assets:Bank',
      filename: '/ledger.beancount',
      lineno: '42',
      // edited user fields
      project: 'q2',
      ref: '99',
    })
  })

  it('drops fields with an empty key', () => {
    const rebuilt = buildMetaFromFields({ project: 'q1' }, [
      { key: 'project', value: 'q1' },
      { key: '', value: 'orphan' },
    ])
    expect(rebuilt).toEqual({ project: 'q1' })
  })
})
