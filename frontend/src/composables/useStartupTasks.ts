import { ref, computed, readonly } from 'vue'
import { StartupService } from '@/services/generated-api'
import type { StartupTaskInfo } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { useToast } from '@/composables/useNotifications'

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

/** Apply a task on user consent, then re-detect (a successful task disappears). */
async function applyStartupTask(taskId: string): Promise<boolean> {
  isApplying.value = true
  applyError.value = null
  try {
    const resp = await StartupService.applyStartupTask(taskId)
    if (!resp.success) {
      throw new Error(resp.error?.message || 'Upgrade failed')
    }
    // Best-effort per file: surface any items that couldn't be upgraded as a
    // dismissible warning. The gate still clears (the backend downgrades a
    // consented-but-incomplete task to a non-blocking notice) so the app loads
    // what succeeded instead of wedging on the failures.
    const errors = (resp.data?.result?.errors as string[] | undefined) ?? []
    if (errors.length > 0) {
      useToast().warning(
        'Some items could not be upgraded',
        `${errors.length} recipe file${errors.length === 1 ? '' : 's'} couldn't be upgraded and ` +
          "won't load until fixed. Edit or remove the affected file(s), or restore the original " +
          'from its timestamped .backup.',
      )
    }
    // Re-detect: a completed migration self-retires and the gate clears.
    const after = await StartupService.getStartupTasks()
    tasks.value = after.success && after.data ? after.data.tasks : []
    return true
  } catch (err: unknown) {
    applyError.value = err instanceof Error ? err.message : 'Upgrade failed'
    errorHandler.display(err)
    return false
  } finally {
    isApplying.value = false
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
    docsUrlFor,
  }
}
