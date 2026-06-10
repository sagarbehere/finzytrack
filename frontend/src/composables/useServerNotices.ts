/**
 * Server-side advisory channel.
 *
 * Polls GET /api/notices on startup, after API errors, and after
 * ledger-writing operations. Replaces useLedgerHealth as the single
 * source for the AppShell banner stack.
 *
 * Dismissal is per-notice and session-only, keyed by (code, signature).
 * A notice whose signature changes (e.g. parse-error count) reappears
 * after dismissal — exactly how the old ledger-error banner behaved.
 */

import { ref, computed, readonly } from 'vue'
import { NoticesService } from '@/services/generated-api'
import type { NoticeModel } from '@/services/generated-api'

const notices = ref<NoticeModel[]>([])
const dismissedKeys = ref<Set<string>>(new Set())

function _key(n: NoticeModel | { code: string; signature: string }): string {
  return `${n.code}::${n.signature}`
}

const visibleNotices = computed<NoticeModel[]>(() =>
  notices.value.filter((n) => !dismissedKeys.value.has(_key(n))),
)

export function useServerNotices() {
  async function check(): Promise<void> {
    try {
      const response = await NoticesService.getNotices()
      const incoming = response.data?.notices ?? []
      // Prune any dismissed entries whose notice no longer exists (or
      // whose signature changed — handled implicitly because the new
      // signature produces a new key).
      const incomingKeys = new Set(incoming.map(_key))
      const prunedDismissed = new Set<string>()
      for (const k of dismissedKeys.value) {
        if (incomingKeys.has(k)) prunedDismissed.add(k)
      }
      dismissedKeys.value = prunedDismissed
      notices.value = incoming
    } catch {
      // Don't surface check failures — they'd mask the real problem.
    }
  }

  function dismiss(notice: NoticeModel): void {
    if (notice.dismissible === false) return
    const next = new Set(dismissedKeys.value)
    next.add(_key(notice))
    dismissedKeys.value = next
  }

  return {
    notices: readonly(notices),
    visibleNotices: readonly(visibleNotices),
    check,
    dismiss,
  }
}
