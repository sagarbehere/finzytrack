/**
 * Startup-task gate logic (dev-docs/upgrades.md): read-only detection surfaces
 * an action_required task, applying it returns the result (the modal shows it,
 * then re-detects to clear the gate), and a failed detection never blocks the app.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const getStartupTasks = vi.fn()
const applyStartupTask = vi.fn()
const dismissStartupTask = vi.fn()
const reopenDismissedNotices = vi.fn()

vi.mock('@/services/generated-api', () => ({
  StartupService: {
    getStartupTasks: () => getStartupTasks(),
    applyStartupTask: (id: string) => applyStartupTask(id),
    dismissStartupTask: (id: string) => dismissStartupTask(id),
    reopenDismissedNotices: () => reopenDismissedNotices(),
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

const seedNotice = {
  id: 'seed-content',
  title: 'New demo content available',
  summary: '2 new demo files.',
  severity: 'info',
  requires_consent: false,
  docs_path: 'upgrade-notes/seed-content',
  details: { items: [{ path: 'config/recipes/dashboards/budget-overview.json', note: 'new' }], changed: 1 },
}

beforeEach(() => {
  getStartupTasks.mockReset()
  applyStartupTask.mockReset()
  dismissStartupTask.mockReset()
  reopenDismissedNotices.mockReset()
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

  it('applying returns the result and leaves the gate up until re-detected', async () => {
    getStartupTasks.mockResolvedValueOnce(ok({ tasks: [gateTaskInfo] }))
    const s = useStartupTasks()
    await s.checkStartupTasks()
    expect(s.gateTask.value).not.toBeNull()

    // apply() returns the result payload (the modal renders it); it does NOT
    // re-detect — the gate stays up so the result screen can show.
    const outcome = { succeeded: [{ path: 'dashboards/d.json', note: 'upgraded' }], failed: [] }
    applyStartupTask.mockResolvedValue(ok({ id: 'recipes-upgrade', applied: true, message: 'done', result: { outcome } }))
    const result = await s.applyStartupTask('recipes-upgrade')
    expect(result).toEqual({ outcome })
    expect(s.gateTask.value).not.toBeNull() // still gating until the modal re-detects

    // The modal re-detects on "Continue" → a self-retired task clears the gate.
    getStartupTasks.mockResolvedValueOnce(ok({ tasks: [] }))
    await s.checkStartupTasks()
    expect(s.gateTask.value).toBeNull()
  })

  it('does not gate when detection fails', async () => {
    getStartupTasks.mockRejectedValue(new Error('backend not ready'))
    const s = useStartupTasks()
    await s.checkStartupTasks()
    expect(s.checked.value).toBe(true)
    expect(s.gateTask.value).toBeNull()
  })

  it('exposes an info notice via infoTasks, not as the gate', async () => {
    getStartupTasks.mockResolvedValue(ok({ tasks: [seedNotice] }))
    const s = useStartupTasks()
    await s.checkStartupTasks()
    expect(s.gateTask.value).toBeNull()
    expect(s.infoTasks.value.map((t) => t.id)).toEqual(['seed-content'])
  })

  it('dismiss calls the dismiss endpoint (snooze) without throwing on failure', async () => {
    dismissStartupTask.mockResolvedValue(ok({ id: 'seed-content', applied: false, message: 'Dismissed.', result: { dismissed: true } }))
    const s = useStartupTasks()
    await s.dismissStartupTask('seed-content')
    expect(dismissStartupTask).toHaveBeenCalledWith('seed-content')

    // A dismiss failure is surfaced but never rejects (must not block the app).
    dismissStartupTask.mockRejectedValue(new Error('offline'))
    await expect(s.dismissStartupTask('seed-content')).resolves.toBeUndefined()
  })

  it('reopenDismissedNotices un-snoozes, re-detects, and returns the pending count', async () => {
    reopenDismissedNotices.mockResolvedValue(ok({ tasks: [seedNotice] }))
    getStartupTasks.mockResolvedValue(ok({ tasks: [seedNotice] }))
    const s = useStartupTasks()
    const count = await s.reopenDismissedNotices()
    expect(reopenDismissedNotices).toHaveBeenCalled()
    expect(count).toBe(1)                                    // the re-opened notice
    expect(s.infoTasks.value.map((t) => t.id)).toEqual(['seed-content'])
  })

  it('reopenDismissedNotices returns 0 when nothing is pending', async () => {
    reopenDismissedNotices.mockResolvedValue(ok({ tasks: [] }))
    getStartupTasks.mockResolvedValue(ok({ tasks: [] }))
    const s = useStartupTasks()
    expect(await s.reopenDismissedNotices()).toBe(0)
  })
})
