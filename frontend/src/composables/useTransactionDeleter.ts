import { ref } from 'vue'
import { LedgerService } from '@/services/generated-api'
import type { DeleteTransactionRequest } from '@/services/generated-api'
import { useAccounts } from '@/composables/useAccounts'

export interface DeleteTransactionResult {
  success: boolean
  deleted_count: number
  message?: string
}

export function useTransactionDeleter() {
  const isDeleting = ref(false)
  const error = ref<string | null>(null)

  /**
   * Delete transactions from the ledger.
   *
   * @param transactionIds - Array of transaction IDs to delete
   * @returns DeleteTransactionResult with success status and count
   */
  async function deleteTransactions(
    transactionIds: string[]
  ): Promise<DeleteTransactionResult> {
    isDeleting.value = true
    error.value = null

    try {
      // Prepare request body
      const requestBody: DeleteTransactionRequest = {
        transaction_ids: transactionIds
      }

      // Call DELETE endpoint using generated client
      const response = await LedgerService.deleteLedgerTransactions(requestBody)

      if (!response.success || !response.data) {
        throw new Error('Delete failed: No data returned')
      }

      // Invalidate accounts cache so balances reflect the deleted transactions
      useAccounts().invalidateCache()

      return {
        success: true,
        deleted_count: response.data.deleted_count,
        message: response.data.message,
      }

    } catch (err: any) {
      // Extract detailed error message from API error
      let errorMessage = 'Failed to delete transactions'

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
      isDeleting.value = false
    }
  }

  return {
    deleteTransactions,
    isDeleting,
    error,
  }
}
