// Dashboard tabs survive two failure modes:
// - another browser tab saving its list: this tab adopts it (keeping its own
//   active dashboard while that is still open) instead of later overwriting it;
// - dashboards failing to load (backend down/restarting): the saved tabs are
//   left untouched instead of being replaced by an empty list.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const ALL: Record<string, { id: string; title: string }> = {
  a: { id: 'a', title: 'A' },
  b: { id: 'b', title: 'B' },
  c: { id: 'c', title: 'C' },
}
// What the recipe loader currently holds; a failed load has none and an error.
const loader = { dashboards: ALL, loadError: null as string | null }
vi.mock('@/composables/useRecipeLoader', async () => {
  const { ref } = await import('vue')
  const loadError = ref<string | null>(null)
  return {
    useRecipeLoader: () => {
      loadError.value = loader.loadError
      return {
        getDashboard: (id: string) => loader.dashboards[id],
        getAllDashboardIds: () => Object.keys(loader.dashboards),
        loadError,
      }
    },
  }
})

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
  loader.dashboards = ALL
  loader.loadError = null
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

describe('dashboards failing to load', () => {
  it('leaves the saved tabs untouched, and restores them on the next load', async () => {
    loader.dashboards = {}
    loader.loadError = 'Failed to fetch /api/recipes/manifest.json: Bad Gateway'
    vi.resetModules()
    const { useDashboardTabs } = await import('@/composables/useDashboardTabs')
    const t = useDashboardTabs()
    await t.loadTabs()
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ tabs: ['a', 'b'], activeTabId: 'b' })
    expect(t.isInitialized.value).toBe(false)

    loader.dashboards = ALL // the backend is back
    loader.loadError = null
    const again = useDashboardTabs()
    await again.loadTabs()
    expect(again.tabs.value.map((x) => x.id)).toEqual(['a', 'b'])
    expect(again.activeTabId.value).toBe('b')
  })

  it('never saves an empty list when there are no dashboards at all', async () => {
    loader.dashboards = {}
    vi.resetModules()
    const { useDashboardTabs } = await import('@/composables/useDashboardTabs')
    await useDashboardTabs().loadTabs()
    expect(JSON.parse(localStorage.getItem(KEY)!).tabs).toEqual(['a', 'b'])
  })
})
