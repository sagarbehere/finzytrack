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

  it('older orphans are listed and checked by default', async () => {
    const orphans = [
      makeOrphan({ path: 'a.pdf', display_name: 'a.pdf', modified: '2020-01-01T00:00:00' }),
      makeOrphan({ path: 'b.pdf', display_name: 'b.pdf', modified: '2020-01-02T00:00:00' }),
    ]
    mount(OrphanSweepModal, { props: { open: true, orphans }, attachTo: document.body })
    await flushPromises()
    const boxes = document.body.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')
    expect(boxes.length).toBe(2)
    expect(boxes[0].checked).toBe(true)
    expect(boxes[1].checked).toBe(true)
  })

  it('recent orphans appear in a separate section, unchecked by default', async () => {
    const recentIso = new Date().toISOString() // within the grace window
    const orphans = [
      makeOrphan({ path: 'old.pdf', display_name: 'old.pdf', modified: '2020-01-01T00:00:00' }),
      makeOrphan({ path: 'fresh.pdf', display_name: 'fresh.pdf', modified: recentIso }),
    ]
    const wrapper = mount(OrphanSweepModal, {
      props: { open: true, orphans, graceSeconds: 24 * 60 * 60 },
      attachTo: document.body,
    })
    await flushPromises()
    expect(document.body.textContent).toContain('Recent (last 24h)')
    // Confirm with defaults: only the older (checked) file is submitted.
    const deleteBtn = Array.from(document.body.querySelectorAll('button')).find(b => b.textContent?.includes('Delete')) as HTMLElement
    deleteBtn.click()
    await nextTick()
    expect(wrapper.emitted('confirm')?.[0]).toEqual([['old.pdf']])
  })

  it('warns that deletion is permanent without assuming git', async () => {
    mount(OrphanSweepModal, { props: { open: true, orphans: [makeOrphan()] }, attachTo: document.body })
    await flushPromises()
    const text = document.body.textContent || ''
    expect(text).toContain('permanently removes these files')
    expect(text).toContain("can't be undone")
    // git is mentioned only as a conditional, not promised.
    expect(text).toContain('If your documents folder is under version control')
  })

  it('confirm emits only the still-checked paths', async () => {
    const orphans = [
      makeOrphan({ path: 'a.pdf', display_name: 'a.pdf', modified: '2020-01-01T00:00:00' }),
      makeOrphan({ path: 'b.pdf', display_name: 'b.pdf', modified: '2020-01-02T00:00:00' }),
    ]
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
