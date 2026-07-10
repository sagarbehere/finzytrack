import { ref, computed, readonly } from 'vue'
import { StartupService } from '@/services/generated-api'
import type { StartupTaskInfo } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

/**
 * Pending startup tasks (upgrades / notices) detected by the backend at launch.
 *
 * A task with `requires_consent` gates the whole app behind a dialog until the
 * user applies it (see StartupGate / App.vue). Detection is read-only — nothing
 * on disk changes until the user consents. See dev-docs/upgrades.md.
 *
 * Module-level singleton: the gate and any notices share one source of truth.
 */

const DOCS_BASE = 'https://docs.finzytrack.com'

const tasks = ref<StartupTaskInfo[]>([])
const checked = ref(false)        // have we completed the initial detection?
const isApplying = ref(false)
const applyError = ref<string | null>(null)

/** The first task that gates the app (requires consent), or null. */
const gateTask = computed<StartupTaskInfo | null>(
  () => tasks.value.find((t) => t.requires_consent) ?? null,
)

/** Non-blocking informational tasks. */
const infoTasks = computed(() => tasks.value.filter((t) => !t.requires_consent))

function docsUrlFor(task: StartupTaskInfo): string | null {
  if (!task.docs_path) return null
  const path = task.docs_path.replace(/^\/+|\/+$/g, '')
  return `${DOCS_BASE}/${path}/`
}

/** Read-only detection. Failures are non-fatal — we proceed without gating. */
async function checkStartupTasks(): Promise<void> {
  try {
    const resp = await StartupService.getStartupTasks()
    tasks.value = resp.success && resp.data ? resp.data.tasks : []
  } catch {
    // If the check fails (e.g. backend not ready), don't block the app.
    tasks.value = []
  } finally {
    checked.value = true
  }
}

/**
 * Apply a task on user consent and return its result (the `outcome`/`errors`
 * payload the modal renders). Returns null on failure (`applyError` is set).
 *
 * Does NOT re-detect — the caller (StartupGate) shows the result screen first,
 * then calls `checkStartupTasks()` when the user dismisses it, so the gate stays
 * up long enough to display what happened.
 */
async function applyStartupTask(taskId: string): Promise<Record<string, unknown> | null> {
  isApplying.value = true
  applyError.value = null
  try {
    const resp = await StartupService.applyStartupTask(taskId)
    if (!resp.success) {
      throw new Error(resp.error?.message || 'Upgrade failed')
    }
    return (resp.data?.result as Record<string, unknown> | undefined) ?? {}
  } catch (err: unknown) {
    applyError.value = err instanceof Error ? err.message : 'Upgrade failed'
    errorHandler.display(err)
    return null
  } finally {
    isApplying.value = false
  }
}

/**
 * Dismiss a non-blocking notice without applying it (the seed-content notice
 * snoozes for the current bundle; a one-shot notice is marked seen). Best-effort:
 * a failure is surfaced but never blocks the app.
 */
async function dismissStartupTask(taskId: string): Promise<void> {
  try {
    await StartupService.dismissStartupTask(taskId)
  } catch (err: unknown) {
    errorHandler.display(err)
  }
}

/**
 * Undo any accidental dismissal of non-gating startup notices (today the
 * seed-content demo-content offer), then refresh the task list so a re-opened
 * notice surfaces again. Generic — future info notices re-open the same way; it
 * never re-opens gating migrations and never applies anything. Returns the count
 * of notices now pending (so the caller can say "1 re-opened" vs "all caught up").
 * Best-effort: a failure is surfaced but never blocks the app.
 */
async function reopenDismissedNotices(): Promise<number> {
  try {
    await StartupService.reopenDismissedNotices()
    await checkStartupTasks() // refresh so the re-opened notice(s) render
    return infoTasks.value.length
  } catch (err: unknown) {
    errorHandler.display(err)
    return 0
  }
}

export function useStartupTasks() {
  return {
    tasks: readonly(tasks),
    checked: readonly(checked),
    gateTask,
    infoTasks,
    isApplying: readonly(isApplying),
    applyError: readonly(applyError),
    checkStartupTasks,
    applyStartupTask,
    dismissStartupTask,
    reopenDismissedNotices,
    docsUrlFor,
  }
}
