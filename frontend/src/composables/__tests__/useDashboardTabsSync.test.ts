// Dashboard tabs stay in step across browser tabs: when another tab saves its
// tab list, this tab adopts it (keeping its own active dashboard while that is
// still open) instead of later overwriting it with a stale list.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const DASHBOARDS: Record<string, { id: string; title: string }> = {
  a: { id: 'a', title: 'A' },
  b: { id: 'b', title: 'B' },
  c: { id: 'c', title: 'C' },
}
vi.mock('@/composables/useRecipeLoader', () => ({
  useRecipeLoader: () => ({
    getDashboard: (id: string) => DASHBOARDS[id],
    getAllDashboardIds: () => Object.keys(DASHBOARDS),
  }),
}))

const KEY = 'finzytrack:dashboardTabs'

/** What another browser tab writing the tab list looks like to this one. */
function otherTabSaves(state: { tabs: string[]; activeTabId: string | null }) {
  const newValue = JSON.stringify(state)
  localStorage.setItem(KEY, newValue)
  window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue, storageArea: localStorage }))
}

async function freshTabs() {
  vi.resetModules() // the tab state is module-level; start each test clean
  const { useDashboardTabs } = await import('@/composables/useDashboardTabs')
  const t = useDashboardTabs()
  await t.loadTabs()
  return t
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem(KEY, JSON.stringify({ tabs: ['a', 'b'], activeTabId: 'b' }))
})

describe('dashboard tabs across browser tabs', () => {
  it('adopts a tab list saved by another tab and keeps its own active dashboard', async () => {
    const t = await freshTabs()
    otherTabSaves({ tabs: ['a', 'b', 'c'], activeTabId: 'c' })
    expect(t.tabs.value.map((x) => x.id)).toEqual(['a', 'b', 'c'])
    expect(t.activeTabId.value).toBe('b')
  })

  it('moves to another open dashboard when its own was closed elsewhere', async () => {
    const t = await freshTabs()
    otherTabSaves({ tabs: ['a', 'c'], activeTabId: 'c' })
    expect(t.tabs.value.map((x) => x.id)).toEqual(['a', 'c'])
    expect(t.activeTabId.value).toBe('c')
  })

  it('then saves the adopted list, not its stale one', async () => {
    const t = await freshTabs()
    otherTabSaves({ tabs: ['a', 'b', 'c'], activeTabId: 'a' })
    t.setActiveTab('a')
    expect(JSON.parse(localStorage.getItem(KEY)!).tabs).toEqual(['a', 'b', 'c'])
  })
})
