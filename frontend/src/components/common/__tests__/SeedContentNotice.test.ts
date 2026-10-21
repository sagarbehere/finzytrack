import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SeedContentNotice from '@/components/common/SeedContentNotice.vue'

// The notice applies while the app is running, so after "Add now" it must
// re-fetch what the frontend loaded once per session (dashboards, theme, and —
// when a demo ledger changed — the ledger-derived caches).
const m = vi.hoisted(() => ({
  applyResult: null as Record<string, unknown> | null,
  applyStartupTask: vi.fn(),
  checkStartupTasks: vi.fn(),
  reloadUserRecipes: vi.fn(),
  loadTheme: vi.fn(),
  invalidateAccounts: vi.fn(),
  invalidateCommodities: vi.fn(),
  fetchYears: vi.fn(),
}))

vi.mock('@/composables/useStartupTasks', async () => {
  const { ref, computed } = await import('vue')
  return {
    useStartupTasks: () => ({
      infoTasks: computed(() => [{ id: 'seed-content', title: 'New demo content', summary: 'x', details: {} }]),
      isApplying: ref(false),
      applyError: ref(null),
      applyStartupTask: m.applyStartupTask,
      dismissStartupTask: vi.fn(),
      checkStartupTasks: m.checkStartupTasks,
      docsUrlFor: () => null,
    }),
  }
})
vi.mock('@/composables/useRecipeLoader', () => ({
  useRecipeLoader: () => ({ reloadUserRecipes: m.reloadUserRecipes }),
}))
vi.mock('@/composables/useDashboardTheme', () => ({
  useDashboardTheme: () => ({ loadTheme: m.loadTheme }),
}))
vi.mock('@/composables/useAccounts', () => ({
  useAccounts: () => ({ invalidateCache: m.invalidateAccounts }),
}))
vi.mock('@/composables/useCommodities', () => ({
  useCommodities: () => ({ invalidateCache: m.invalidateCommodities }),
}))
vi.mock('@/composables/useAvailableYears', async () => {
  const { ref } = await import('vue')
  return { useAvailableYears: () => ({ hasBeenFetched: ref(true), fetchYears: m.fetchYears }) }
})

function result(paths: string[]) {
  return {
    outcome: { succeeded: paths.map((path) => ({ path, note: 'added' })), failed: [] },
    skipped: [], baseDir: '/x', summary: 'Demo content: added.',
  }
}

async function clickAddNow() {
  const wrapper = mount(SeedContentNotice)
  const button = wrapper.findAll('button').find((b) => b.text() === 'Add now')!
  await button.trigger('click')
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  m.applyStartupTask.mockImplementation(async () => m.applyResult)
  m.fetchYears.mockResolvedValue(undefined)
})

describe('SeedContentNotice — refresh after apply', () => {
  it('reloads dashboards and the theme when content was delivered', async () => {
    m.applyResult = result(['config/recipes/dashboards/net-worth.json'])
    await clickAddNow()
    expect(m.reloadUserRecipes).toHaveBeenCalledTimes(1)
    expect(m.loadTheme).toHaveBeenCalledTimes(1)
    expect(m.invalidateAccounts).not.toHaveBeenCalled()
    expect(m.invalidateCommodities).not.toHaveBeenCalled()
    expect(m.fetchYears).not.toHaveBeenCalled()
  })

  it('also drops the ledger-derived caches when a demo ledger changed', async () => {
    m.applyResult = result(['data/ledgers/fake.beancount'])
    await clickAddNow()
    expect(m.reloadUserRecipes).toHaveBeenCalledTimes(1)
    expect(m.invalidateAccounts).toHaveBeenCalledTimes(1)
    expect(m.invalidateCommodities).toHaveBeenCalledTimes(1)
    expect(m.fetchYears).toHaveBeenCalledWith(true)
  })

  it('refreshes nothing when the apply changed no files', async () => {
    m.applyResult = result([])
    await clickAddNow()
    expect(m.reloadUserRecipes).not.toHaveBeenCalled()
    expect(m.loadTheme).not.toHaveBeenCalled()
  })

  it('refreshes nothing when the apply failed', async () => {
    m.applyResult = null
    await clickAddNow()
    expect(m.reloadUserRecipes).not.toHaveBeenCalled()
    expect(m.loadTheme).not.toHaveBeenCalled()
  })
})
