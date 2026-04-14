import { ref } from 'vue'
import type { TransactionViewModel } from '@/types/transactions'
import { LedgerService } from '@/services/generated-api'
import type { UpdateTransactionRequest } from '@/services/generated-api'
import { useAccounts } from '@/composables/useAccounts'

interface UpdateTransactionResult {
  success: boolean
  updated_count: number
  message?: string
}

/**
 * Filter out internal Beancount metadata fields that shouldn't be sent in updates.
 * These are read-only fields managed by Beancount parser.
 */
function filterInternalMetadata(meta: Record<string, any>): Record<string, string> {
  const internalFields = new Set([
    'filename',      // Beancount parser metadata
    'lineno',        // Beancount parser metadata
    '__tolerances__' // Beancount internal tolerances
  ])

  const cleanMeta: Record<string, string> = {}

  for (const [key, value] of Object.entries(meta)) {
    // Skip internal fields
    if (internalFields.has(key)) {
      continue
    }

    // Convert value to string (Beancount metadata values are always strings)
    cleanMeta[key] = String(value)
  }

  return cleanMeta
}

export function useTransactionUpdater() {
  const isUpdating = ref(false)
  const error = ref<string | null>(null)

  /**
   * Update modified transactions in the ledger.
   */
  async function updateTransactions(
    transactions: TransactionViewModel[]
  ): Promise<UpdateTransactionResult> {
    isUpdating.value = true
    error.value = null

    try {
      // Transform to API request format
      const requestBody: UpdateTransactionRequest = {
        transactions: transactions.map((txn) => {
          // Filter out internal Beancount metadata that shouldn't be sent
          const cleanMeta = filterInternalMetadata(txn.meta)

          return {
            id: txn.id,
            date: txn.date,
            flag: txn.flag,
            payee: txn.payee,
            narration: txn.narration,
            memo: txn.memo || undefined,
            tags: txn.tags,
            links: txn.links,
            postings: txn.postings.map((p) => {
              // Filter out internal posting metadata
              const cleanPostingMeta = p.meta ? filterInternalMetadata(p.meta) : undefined

              return {
                account: p.account,
                amount: p.amount,
                currency: p.currency,
                cost_amount: p.cost?.amount,
                cost_currency: p.cost?.currency,
                cost_date: p.cost?.date,
                price_amount: p.price?.amount,
                price_currency: p.price?.currency,
                price_type: p.price?.type,
                posting_meta: cleanPostingMeta,
              }
            }),
            meta: cleanMeta,
          }
        })
      }

      // Call API endpoint using generated client
      const response = await LedgerService.updateLedgerTransactions(requestBody)

      if (!response.success || !response.data) {
        throw new Error('Update failed: No data returned')
      }

      // Invalidate accounts cache so balances reflect the updated transactions
      useAccounts().invalidateCache()

      return {
        success: true,
        updated_count: response.data.updated_count,
        message: response.data.message,
      }

    } catch (err: any) {
      // Extract detailed error message from API error
      let errorMessage = 'Failed to update transactions'

      if (err.body?.error?.message) {
        errorMessage = err.body.error.message
      } else if (err.message) {
        errorMessage = err.message
      }

      error.value = errorMessage

      // Create a more informative error
      const detailedError = new Error(errorMessage)
      throw detailedError
    } finally {
      isUpdating.value = false
    }
  }

  return {
    updateTransactions,
    isUpdating,
    error,
  }
}
