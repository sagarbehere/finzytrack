import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import TransactionDocumentsDrawer from '@/components/documents/TransactionDocumentsDrawer.vue'
import DocumentUploadZone from '@/components/documents/DocumentUploadZone.vue'
import { makeTx } from '@/test/factories'

const uploadDocument = vi.fn()
const openDocument = vi.fn()

// HeadlessUI Dialog teleports its content to document.body, so assertions query
// the document rather than the wrapper's local DOM.
vi.mock('@/composables/useDocuments', () => ({
  useDocuments: () => ({ uploadDocument, openDocument, isUploading: { value: false } }),
}))

describe('TransactionDocumentsDrawer', () => {
  beforeEach(() => {
    uploadDocument.mockReset()
    openDocument.mockReset()
  })
  afterEach(() => { document.body.innerHTML = '' })

  it('lists the attached documents (basename) from meta', async () => {
    const transaction = makeTx({
      meta: {
        document: '../documents/2026/2026-06-15-acme-a1b2c3d4.pdf',
        document2: '../documents/2026/2026-06-15-invoice-9f8e7d6c.pdf',
      },
    })
    mount(TransactionDocumentsDrawer, { props: { open: true, transaction }, attachTo: document.body })
    await flushPromises()
    expect(document.body.textContent).toContain('2026-06-15-acme-a1b2c3d4.pdf')
    expect(document.body.textContent).toContain('2026-06-15-invoice-9f8e7d6c.pdf')
  })

  it('emits changed with the document removed and re-compacted', async () => {
    const transaction = makeTx({ meta: { document: 'a.pdf', document2: 'b.pdf' } })
    const wrapper = mount(TransactionDocumentsDrawer, { props: { open: true, transaction }, attachTo: document.body })
    await flushPromises()
    const removeButtons = document.body.querySelectorAll<HTMLElement>('button[title="Remove document"]')
    expect(removeButtons.length).toBe(2)
    removeButtons[0].click()
    await nextTick()
    expect(wrapper.emitted('changed')?.[0]).toEqual([{ document: 'b.pdf' }])
  })

  it('uploads a dropped file then emits changed with the new path appended', async () => {
    uploadDocument.mockResolvedValue({
      path: '../documents/2026/new-xyz.pdf', full_hash: 'x', size: 1, display_name: 'new-xyz.pdf',
    })
    const transaction = makeTx({ id: 'tx-1', date: '2026-06-15', payee: 'ACME', meta: {} })
    const wrapper = mount(TransactionDocumentsDrawer, { props: { open: true, transaction }, attachTo: document.body })
    await flushPromises()

    const file = new File(['x'], 'r.pdf', { type: 'application/pdf' })
    wrapper.findComponent(DocumentUploadZone).vm.$emit('files-selected', [file])
    await flushPromises()

    expect(uploadDocument).toHaveBeenCalledWith(file, { date: '2026-06-15', narration: 'ACME' })
    expect(wrapper.emitted('changed')?.[0]).toEqual([{ document: '../documents/2026/new-xyz.pdf' }])
  })

  describe('metadata editing (showMetadata)', () => {
    function inputByPlaceholder(placeholder: string): HTMLInputElement {
      const el = Array.from(document.body.querySelectorAll('input')).find(i => i.placeholder === placeholder)
      if (!el) throw new Error(`no input with placeholder "${placeholder}"`)
      return el as HTMLInputElement
    }

    it('shows only free user keys and hides protected/surfaced ones', async () => {
      const transaction = makeTx({
        meta: { project: 'q1', memo: 'note', document: 'a.pdf', id: 'uuid', source_account: 'Assets:Bank' },
      })
      mount(TransactionDocumentsDrawer, { props: { open: true, transaction, showMetadata: true }, attachTo: document.body })
      await flushPromises()

      const keyInputs = Array.from(document.body.querySelectorAll('input')).filter(i => i.placeholder === 'key') as HTMLInputElement[]
      expect(keyInputs.map(i => i.value)).toEqual(['project'])
      // System section shows read-only id/source_account.
      expect(document.body.textContent).toContain('source_account')
    })

    it('emits changed on a value edit, preserving protected keys', async () => {
      const transaction = makeTx({ meta: { project: 'q1', id: 'uuid', source_account: 'Assets:Bank' } })
      const wrapper = mount(TransactionDocumentsDrawer, { props: { open: true, transaction, showMetadata: true }, attachTo: document.body })
      await flushPromises()

      const valueInput = inputByPlaceholder('value')
      valueInput.value = 'q2'
      valueInput.dispatchEvent(new Event('input'))
      await nextTick()

      const last = wrapper.emitted('changed')!.at(-1)![0]
      expect(last).toEqual({ id: 'uuid', source_account: 'Assets:Bank', project: 'q2' })
    })

    it('shows a diff for changed and removed metadata against the baseline', async () => {
      const transaction = makeTx({ meta: { project: 'q2' } })
      mount(TransactionDocumentsDrawer, {
        props: { open: true, transaction, showMetadata: true, baselineMeta: { project: 'q1', invoice: 'INV-1' } },
        attachTo: document.body,
      })
      await flushPromises()

      const text = document.body.textContent || ''
      expect(text).toContain('was:')       // project changed q1 → q2
      expect(text).toContain('q1')          // the old value, struck through
      expect(text).toContain('invoice')     // removed key listed
      expect(text).toContain('(removed)')
    })

    it('flags a reserved key and does not emit it', async () => {
      const transaction = makeTx({ meta: { source_account: 'Assets:Bank' } })
      const wrapper = mount(TransactionDocumentsDrawer, { props: { open: true, transaction, showMetadata: true }, attachTo: document.body })
      await flushPromises()

      // No editable fields yet — add one and give it a reserved key.
      const addBtn = Array.from(document.body.querySelectorAll('button')).find(b => b.textContent?.includes('Add field'))!
      addBtn.click()
      await nextTick()

      const keyInput = inputByPlaceholder('key')
      keyInput.value = 'source_account'
      keyInput.dispatchEvent(new Event('input'))
      await nextTick()

      expect(document.body.textContent).toContain('Reserved key')
      // The reserved field is excluded, so the rebuilt meta keeps only the preserved key.
      const last = wrapper.emitted('changed')!.at(-1)![0]
      expect(last).toEqual({ source_account: 'Assets:Bank' })
    })
  })
})
