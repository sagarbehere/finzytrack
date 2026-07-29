/**
 * Calls POST /api/ledger/categorize to suggest accounts for existing ledger
 * transactions (bulk autocategorization). No duplicate detection — the rows are
 * already committed. See dev-docs/bulk-edits.md §10.
 */
import { ref } from 'vue'
import { LedgerService } from '@/services/generated-api'
import type {
  CategorizeExistingTransaction,
  CategorizeExistingResult,
  CategorizationStats,
} from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

export interface CategorizeExistingOutcome {
  results: CategorizeExistingResult[]
  stats: CategorizationStats
}

export function useCategorizeExisting() {
  const isCategorizing = ref(false)

  async function categorizeExisting(
    transactions: CategorizeExistingTransaction[],
  ): Promise<CategorizeExistingOutcome | null> {
    isCategorizing.value = true
    try {
      const response = await LedgerService.categorizeExistingTransactions({ transactions })
      if (!response.data) throw new Error('No data received from categorize endpoint')
      return { results: response.data.results, stats: response.data.stats }
    } catch (error) {
      errorHandler.display(error)
      return null
    } finally {
      isCategorizing.value = false
    }
  }

  return { isCategorizing, categorizeExisting }
}
