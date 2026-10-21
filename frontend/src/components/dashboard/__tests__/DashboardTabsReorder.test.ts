// Dashboard tabs reorder by drag and drop (or Alt+←/→), and the order is saved.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/composables/useRecipeLoader', () => ({
  useRecipeLoader: () => ({ getDashboard: () => undefined, getAllDashboardIds: () => [] }),
}))

import DashboardTabs from '@/components/dashboard/DashboardTabs.vue'
import { useDashboardTabs } from '@/composables/useDashboardTabs'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'

const tabs = [
  { id: 'a', title: 'A' },
  { id: 'b', title: 'B' },
  { id: 'c', title: 'C' },
]

describe('useDashboardTabs.moveTab', () => {
  const t = useDashboardTabs()
  const ids = () => t.tabs.value.map((x) => x.id)

  beforeEach(() => {
    for (const id of ids()) t.removeTab(id)
    for (const x of tabs) t.addTab(x.id, x.title)
    t.setActiveTab('b')
  })

  it('moves a tab and saves the new order, keeping the active tab', () => {
    t.moveTab('a', 2)
    expect(ids()).toEqual(['b', 'c', 'a'])
    expect(t.activeTabId.value).toBe('b')
    expect(getStorageAdapter().get<{ tabs: string[] }>(STORAGE_KEYS.DASHBOARD_TABS)?.tabs).toEqual(['b', 'c', 'a'])
    t.moveTab('a', 0)
    expect(ids()).toEqual(['a', 'b', 'c'])
  })

  it('clamps the target and ignores unknown tabs', () => {
    t.moveTab('a', 99)
    expect(ids()).toEqual(['b', 'c', 'a'])
    t.moveTab('c', -5)
    expect(ids()).toEqual(['c', 'b', 'a'])
    t.moveTab('nope', 0)
    expect(ids()).toEqual(['c', 'b', 'a'])
  })
})

describe('DashboardTabs drag and drop', () => {
  function render() {
    return mount(DashboardTabs, { props: { tabs, activeTabId: 'a' }, attachTo: document.body })
  }
  const tabEls = (w: ReturnType<typeof render>) => w.findAll('[role="tab"]')

  // Each tab is 100px wide, laid out left to right.
  function layOut(w: ReturnType<typeof render>) {
    tabEls(w).forEach((el, i) => {
      el.element.getBoundingClientRect = () => ({ left: i * 100, width: 100 }) as DOMRect
    })
  }
  const dt = () => ({ setData: vi.fn(), effectAllowed: '', dropEffect: '' })

  async function drag(w: ReturnType<typeof render>, from: number, over: number, clientX: number) {
    await tabEls(w)[from].trigger('dragstart', { dataTransfer: dt() })
    await tabEls(w)[over].trigger('dragover', { dataTransfer: dt(), clientX })
  }

  it('drops after the tab whose right half it is over', async () => {
    const w = render(); layOut(w)
    await drag(w, 0, 2, 280)                       // right half of C → after C
    await tabEls(w)[2].trigger('drop')
    expect(w.emitted('move')).toEqual([['a', 2]])
    w.unmount()
  })

  it('drops before the tab whose left half it is over', async () => {
    const w = render(); layOut(w)
    await drag(w, 2, 0, 10)                        // left half of A → before A
    await tabEls(w)[0].trigger('drop')
    expect(w.emitted('move')).toEqual([['c', 0]])
    w.unmount()
  })

  it('does nothing when dropped next to where the tab already is', async () => {
    const w = render(); layOut(w)
    await drag(w, 1, 1, 180)                       // own right half
    await tabEls(w)[1].trigger('drop')
    await drag(w, 1, 2, 210)                       // left half of the next tab
    await tabEls(w)[2].trigger('drop')
    expect(w.emitted('move')).toBeUndefined()
    w.unmount()
  })

  it('moves the focused tab with Alt+arrow keys, within bounds', async () => {
    const w = render()
    await tabEls(w)[1].trigger('keydown', { key: 'ArrowRight', altKey: true })
    await tabEls(w)[0].trigger('keydown', { key: 'ArrowLeft', altKey: true })   // already first
    expect(w.emitted('move')).toEqual([['b', 2]])
    w.unmount()
  })
})
