/**
 * Composable for tracking ledger parse errors.
 *
 * Calls GET /api/ledger/errors on startup, after API errors,
 * and after ledger-writing operations. Exposes reactive state
 * for the AppShell banner.
 */

import { ref, readonly } from 'vue'
import { LedgerService } from '@/services/generated-api'
import type { LedgerValidationError } from '@/services/generated-api'

const errorCount = ref(0)
const errors = ref<LedgerValidationError[]>([])
const dismissed = ref(false)

export function useLedgerHealth() {
  async function checkErrors(): Promise<void> {
    try {
      const response = await LedgerService.getLedgerErrors()
      if (response.data) {
        const prevCount = errorCount.value
        errorCount.value = response.data.error_count
        errors.value = response.data.errors
        // Re-show banner if error count changed
        if (response.data.error_count !== prevCount) {
          dismissed.value = false
        }
      }
    } catch {
      // Don't surface health-check failures — they'd mask the real problem
    }
  }

  function dismiss(): void {
    dismissed.value = true
  }

  return {
    errorCount: readonly(errorCount),
    errors: readonly(errors),
    dismissed: readonly(dismissed),
    checkErrors,
    dismiss,
  }
}
