import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import OrphanSweepModal from '@/components/documents/OrphanSweepModal.vue'
import { makeOrphan } from '@/test/factories'

// HeadlessUI Dialog teleports content to document.body.
vi.mock('@/composables/useDocuments', () => ({
  useDocuments: () => ({ openDocument: vi.fn() }),
}))

describe('OrphanSweepModal', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('shows the no-orphans info message when the list is empty', async () => {
    mount(OrphanSweepModal, { props: { open: true, orphans: [] }, attachTo: document.body })
    await flushPromises()
    expect(document.body.textContent).toContain('No orphaned documents found')
    const buttons = Array.from(document.body.querySelectorAll('button'))
    expect(buttons.some(b => b.textContent?.includes('Delete'))).toBe(false)
  })

  it('renders a checkbox per orphan, all checked by default', async () => {
    const orphans = [makeOrphan({ path: 'a.pdf', display_name: 'a.pdf' }), makeOrphan({ path: 'b.pdf', display_name: 'b.pdf' })]
    mount(OrphanSweepModal, { props: { open: true, orphans }, attachTo: document.body })
    await flushPromises()
    const boxes = document.body.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')
    expect(boxes.length).toBe(2)
    expect(boxes[0].checked).toBe(true)
    expect(boxes[1].checked).toBe(true)
  })

  it('shows the git-recoverability note', async () => {
    mount(OrphanSweepModal, { props: { open: true, orphans: [makeOrphan()] }, attachTo: document.body })
    await flushPromises()
    expect(document.body.textContent).toContain('restored from your git history')
  })

  it('confirm emits only the still-checked paths', async () => {
    const orphans = [makeOrphan({ path: 'a.pdf', display_name: 'a.pdf' }), makeOrphan({ path: 'b.pdf', display_name: 'b.pdf' })]
    const wrapper = mount(OrphanSweepModal, { props: { open: true, orphans }, attachTo: document.body })
    await flushPromises()
    const boxes = document.body.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')
    // Uncheck the first orphan (v-model on checkbox listens to 'change').
    boxes[0].checked = false
    boxes[0].dispatchEvent(new Event('change'))
    await nextTick()
    const deleteBtn = Array.from(document.body.querySelectorAll('button')).find(b => b.textContent?.includes('Delete')) as HTMLElement
    deleteBtn.click()
    await nextTick()
    expect(wrapper.emitted('confirm')?.[0]).toEqual([['b.pdf']])
  })
})
