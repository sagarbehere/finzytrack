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
})
