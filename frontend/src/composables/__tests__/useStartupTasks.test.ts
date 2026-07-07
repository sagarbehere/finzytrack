/**
 * Startup-task gate logic (dev-docs/upgrades.md): read-only detection surfaces
 * an action_required task, applying it re-detects and clears the gate, and a
 * failed detection never blocks the app.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const getStartupTasks = vi.fn()
const applyStartupTask = vi.fn()

vi.mock('@/services/generated-api', () => ({
  StartupService: {
    getStartupTasks: () => getStartupTasks(),
    applyStartupTask: (id: string) => applyStartupTask(id),
  },
  ApiError: class ApiError extends Error {},
}))

import { useStartupTasks } from '@/composables/useStartupTasks'

const gateTaskInfo = {
  id: 'recipes-upgrade',
  title: 'Upgrade saved dashboards',
  summary: '3 dashboards will be upgraded.',
  severity: 'action_required',
  requires_consent: true,
  docs_path: 'upgrade-notes/dashboards-step-format',
  details: { legacy_dashboards: 3 },
}

function ok<T>(data: T) {
  return { success: true, data, error: null }
}

beforeEach(() => {
  getStartupTasks.mockReset()
  applyStartupTask.mockReset()
})

describe('useStartupTasks', () => {
  it('detects an action_required task and exposes it as the gate', async () => {
    getStartupTasks.mockResolvedValue(ok({ tasks: [gateTaskInfo] }))
    const { checkStartupTasks, gateTask, checked } = useStartupTasks()
    await checkStartupTasks()
    expect(checked.value).toBe(true)
    expect(gateTask.value?.id).toBe('recipes-upgrade')
  })

  it('builds a docs URL from docs_path', () => {
    const { docsUrlFor } = useStartupTasks()
    expect(docsUrlFor(gateTaskInfo as never)).toBe(
      'https://docs.finzytrack.com/upgrade-notes/dashboards-step-format/',
    )
  })

  it('applying re-detects and clears the gate', async () => {
    getStartupTasks.mockResolvedValueOnce(ok({ tasks: [gateTaskInfo] }))
    const s = useStartupTasks()
    await s.checkStartupTasks()
    expect(s.gateTask.value).not.toBeNull()

    applyStartupTask.mockResolvedValue(ok({ id: 'recipes-upgrade', applied: true, message: 'done', result: {} }))
    getStartupTasks.mockResolvedValueOnce(ok({ tasks: [] })) // re-detect after apply
    const applied = await s.applyStartupTask('recipes-upgrade')
    expect(applied).toBe(true)
    expect(s.gateTask.value).toBeNull()
  })

  it('does not gate when detection fails', async () => {
    getStartupTasks.mockRejectedValue(new Error('backend not ready'))
    const s = useStartupTasks()
    await s.checkStartupTasks()
    expect(s.checked.value).toBe(true)
    expect(s.gateTask.value).toBeNull()
  })
})
