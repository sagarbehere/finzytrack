import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import DocumentPreviewModal from '@/components/documents/DocumentPreviewModal.vue'
import { useDocumentPreview } from '@/composables/useDocumentPreview'

// The modal is driven by the app-level preview singleton, not props.
describe('DocumentPreviewModal', () => {
  beforeEach(() => useDocumentPreview().close())
  afterEach(() => { document.body.innerHTML = ''; useDocumentPreview().close() })

  it('embeds an image in an <img>', async () => {
    useDocumentPreview().openPreview('../documents/main/2026/r.png', 'r.png')
    mount(DocumentPreviewModal, { attachTo: document.body })
    await flushPromises()
    const img = document.body.querySelector('img')
    expect(img).not.toBeNull()
    expect(img!.getAttribute('src')).toContain('/api/documents/file?path=')
    expect(document.body.querySelector('iframe')).toBeNull()
  })

  it('embeds a PDF in an <iframe>', async () => {
    useDocumentPreview().openPreview('../documents/main/2026/r.pdf', 'r.pdf')
    mount(DocumentPreviewModal, { attachTo: document.body })
    await flushPromises()
    expect(document.body.querySelector('iframe')).not.toBeNull()
    expect(document.body.querySelector('img')).toBeNull()
  })

  it('falls back to a download message for non-previewable types', async () => {
    useDocumentPreview().openPreview('../documents/main/2026/data.csv', 'data.csv')
    mount(DocumentPreviewModal, { attachTo: document.body })
    await flushPromises()
    expect(document.body.querySelector('img')).toBeNull()
    expect(document.body.querySelector('iframe')).toBeNull()
    expect(document.body.textContent).toContain("can't be previewed")
  })

  it('always offers a download link with the serve URL', async () => {
    useDocumentPreview().openPreview('../documents/main/2026/r.pdf', 'r.pdf')
    mount(DocumentPreviewModal, { attachTo: document.body })
    await flushPromises()
    const link = Array.from(document.body.querySelectorAll('a')).find(a => a.textContent?.includes('Download')) as HTMLAnchorElement
    expect(link).toBeTruthy()
    expect(link.getAttribute('href')).toContain('/api/documents/file?path=')
    expect(link.getAttribute('download')).toBe('r.pdf')
  })
})
