// The recipe loader tells "no dashboards" (the manifest is missing: 404) apart
// from "couldn't load them" (backend down or restarting): the latter is an
// error and stays retryable, so callers don't treat it as an empty list.
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/composables/useNotifications', () => ({
  useNotifications: () => ({ addNotification: vi.fn() }),
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

function respond(status: number, statusText: string) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status, statusText, json: async () => ({}) }))
}

async function freshLoader() {
  vi.resetModules() // the loader's state is module-level
  const { useRecipeLoader } = await import('@/composables/useRecipeLoader')
  const loader = useRecipeLoader()
  await loader.loadUserRecipes()
  return loader
}

beforeEach(() => vi.unstubAllGlobals())

describe('recipe loader failures', () => {
  it('reports an unreachable backend as an error and stays retryable', async () => {
    respond(502, 'Bad Gateway')
    const loader = await freshLoader()
    expect(loader.loadError.value).toMatch(/Bad Gateway/)
    expect(loader.isLoaded.value).toBe(false)
  })

  it('treats a missing manifest as "no dashboards", not an error', async () => {
    respond(404, 'Not Found')
    const loader = await freshLoader()
    expect(loader.loadError.value).toBeNull()
    expect(loader.isLoaded.value).toBe(true)
    expect(loader.getAllDashboardIds()).toEqual([])
  })
})
