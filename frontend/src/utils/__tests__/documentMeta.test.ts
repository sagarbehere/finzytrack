import { describe, it, expect } from 'vitest'
import {
  documentKey,
  listDocuments,
  documentCount,
  addDocument,
  removeDocument,
} from '@/utils/documentMeta'

describe('documentMeta', () => {
  describe('documentKey', () => {
    it('uses bare "document" for the first slot, numbered after', () => {
      expect(documentKey(1)).toBe('document')
      expect(documentKey(2)).toBe('document2')
      expect(documentKey(3)).toBe('document3')
    })
  })

  describe('listDocuments / documentCount', () => {
    it('returns document keys in numeric order, ignoring other meta', () => {
      const meta = {
        id: 'x',
        document: 'a.pdf',
        document3: 'c.pdf',
        document2: 'b.pdf',
        source_account: 'Assets:Bank',
      }
      expect(listDocuments(meta)).toEqual([
        { key: 'document', path: 'a.pdf' },
        { key: 'document2', path: 'b.pdf' },
        { key: 'document3', path: 'c.pdf' },
      ])
      expect(documentCount(meta)).toBe(3)
    })

    it('counts 0 for empty / undefined / non-document meta', () => {
      expect(documentCount(undefined)).toBe(0)
      expect(documentCount(null)).toBe(0)
      expect(documentCount({})).toBe(0)
      expect(documentCount({ id: 'x', memo: 'hi' })).toBe(0)
    })

    it('does not treat empty-string values as documents', () => {
      expect(documentCount({ document: '' })).toBe(0)
    })
  })

  describe('addDocument', () => {
    it('appends to the next gapless slot, preserving non-document keys', () => {
      const meta = { id: 'x', document: 'a.pdf' }
      const next = addDocument(meta, 'b.pdf')
      expect(next).toEqual({ id: 'x', document: 'a.pdf', document2: 'b.pdf' })
    })

    it('adds the first document as bare "document"', () => {
      expect(addDocument({ id: 'x' }, 'a.pdf')).toEqual({ id: 'x', document: 'a.pdf' })
    })

    it('does not mutate the input', () => {
      const meta = { document: 'a.pdf' }
      addDocument(meta, 'b.pdf')
      expect(meta).toEqual({ document: 'a.pdf' })
    })
  })

  describe('removeDocument', () => {
    it('removes the middle doc and re-compacts to a gapless scheme', () => {
      const meta = { id: 'x', document: 'a.pdf', document2: 'b.pdf', document3: 'c.pdf' }
      const next = removeDocument(meta, 'document2')
      // b.pdf gone; c.pdf renumbered down to document2 — no gap
      expect(next).toEqual({ id: 'x', document: 'a.pdf', document2: 'c.pdf' })
      expect('document3' in next).toBe(false)
    })

    it('removing the only doc leaves no document keys', () => {
      expect(removeDocument({ id: 'x', document: 'a.pdf' }, 'document')).toEqual({ id: 'x' })
    })

    it('re-compacts a non-gapless external scheme on removal', () => {
      const meta = { document: 'a.pdf', document3: 'c.pdf' }
      // removing the first leaves c.pdf, which becomes the bare "document"
      expect(removeDocument(meta, 'document')).toEqual({ document: 'c.pdf' })
    })
  })
})
