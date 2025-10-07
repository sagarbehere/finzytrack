/**
 * Transaction importer composable for autocategorization and commit workflows.
 *
 * Manages the process of categorizing imported transactions and committing them to the ledger.
 */

import { ref } from 'vue'
import { ImportService, ApiError } from '@/services/generated-api'
import type {
  CategorizedTransactionResult,
  CategorizeRequest,
  CommitRequest,
  CommitTransaction
} from '@/services/generated-api'
import type { TransactionViewModel } from '@/types/transactions'

export function useTransactionImporter() {
  // State
  const isLoading = ref(false)
  const categorizeError = ref<{ message: string; details?: string } | null>(null)
  const commitError = ref<{ message: string; details?: string } | null>(null)

  /**
   * Perform autocategorization on transactions.
   *
   * Sends transactions to backend for ML categorization and duplicate detection.
   * Returns results in same order as input transactions.
   */
  async function performCategorization(
    transactions: TransactionViewModel[],
    sourceAccount: string,
    sourceCurrency: string
  ): Promise<CategorizedTransactionResult[]> {
    isLoading.value = true
    categorizeError.value = null

    try {
      // Map TransactionViewModel to backend schema
      const request: CategorizeRequest = {
        transactions: transactions.map(tx => ({
          date: tx.date,
          payee: tx.payee,
          memo: tx.memo,
          narration: tx.narration,
          amount: tx.postings[0]?.amount?.toString() || '0',
          ofx_id: tx.meta['ofx_id']
        })),
        source_account: sourceAccount,
        currency: sourceCurrency
      }

      // Call backend API
      const response = await ImportService.categorizeTransactionsApiImportCategorizePost(request)

      if (!response.data) {
        throw new Error('No data received from categorization endpoint')
      }

      return response.data.results

    } catch (error) {
      if (error instanceof ApiError) {
        const errorBody = error.body as any
        categorizeError.value = {
          message: errorBody?.error?.message || 'Categorization failed',
          details: errorBody?.error?.details ? JSON.stringify(errorBody.error.details) : undefined
        }
      } else {
        categorizeError.value = {
          message: 'An unexpected error occurred during categorization',
          details: error instanceof Error ? error.message : String(error)
        }
      }
      throw error
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Commit transactions to the ledger.
   *
   * Validates and writes transactions to the Beancount ledger file.
   */
  async function performCommit(
    transactions: TransactionViewModel[]
  ): Promise<{ success: boolean; count: number }> {
    isLoading.value = true
    commitError.value = null

    try {
      // Validate transactions before sending to backend
      for (let i = 0; i < transactions.length; i++) {
        const tx = transactions[i]
        const rowNum = i + 1

        // Validate source_account is present
        if (!tx.meta['source_account']) {
          commitError.value = {
            message: `Validation failed: source_account is required (row ${rowNum})`,
            details: `Date: ${tx.date}, Payee: ${tx.payee}`
          }
          throw new Error('Validation failed')
        }

        for (let j = 0; j < tx.postings.length; j++) {
          const posting = tx.postings[j]

          // Validate cost completeness (treat 0 as empty)
          const costAmountIsNonZero = posting.cost?.amount !== undefined &&
                                      posting.cost?.amount !== null &&
                                      posting.cost?.amount !== 0
          if (costAmountIsNonZero) {
            if (!posting.cost?.currency) {
              commitError.value = {
                message: `Validation failed: Cost amount specified but cost currency missing (row ${rowNum}, posting ${j + 1})`,
                details: `Account: ${posting.account}, Date: ${tx.date}`
              }
              throw new Error('Validation failed')
            }
          }
          if (posting.cost?.currency && !costAmountIsNonZero) {
            commitError.value = {
              message: `Validation failed: Cost currency specified but cost amount missing or zero (row ${rowNum}, posting ${j + 1})`,
              details: `Account: ${posting.account}, Date: ${tx.date}`
            }
            throw new Error('Validation failed')
          }

          // Validate price completeness (treat 0 as empty)
          const priceAmountIsNonZero = posting.price?.amount !== undefined &&
                                       posting.price?.amount !== null &&
                                       posting.price?.amount !== 0
          if (priceAmountIsNonZero) {
            if (!posting.price?.currency || !posting.price?.type) {
              commitError.value = {
                message: `Validation failed: Price amount specified but currency or type missing (row ${rowNum}, posting ${j + 1})`,
                details: `Account: ${posting.account}, Date: ${tx.date}`
              }
              throw new Error('Validation failed')
            }
          }
          if (posting.price?.currency || posting.price?.type) {
            if (!priceAmountIsNonZero) {
              commitError.value = {
                message: `Validation failed: Price currency or type specified but amount missing or zero (row ${rowNum}, posting ${j + 1})`,
                details: `Account: ${posting.account}, Date: ${tx.date}`
              }
              throw new Error('Validation failed')
            }
          }

          // Validate price type
          if (posting.price?.type && !['@', '@@'].includes(posting.price.type)) {
            commitError.value = {
              message: `Validation failed: Invalid price type '${posting.price.type}' (must be '@' or '@@') (row ${rowNum}, posting ${j + 1})`,
              details: `Account: ${posting.account}, Date: ${tx.date}`
            }
            throw new Error('Validation failed')
          }
        }
      }

      // Map TransactionViewModel to backend schema
      const commitTransactions: CommitTransaction[] = transactions.map(tx => ({
        date: tx.date,
        flag: tx.flag,
        payee: tx.payee,
        memo: tx.memo,
        narration: tx.narration,
        tags: tx.tags,
        links: tx.links,
        postings: tx.postings.map(p => {
          // Only send cost fields if amount is non-zero and non-empty
          const hasCost = p.cost?.amount !== undefined && p.cost?.amount !== null && p.cost?.amount !== 0
          // Only send price fields if amount is non-zero and non-empty
          const hasPrice = p.price?.amount !== undefined && p.price?.amount !== null && p.price?.amount !== 0

          return {
            account: p.account,
            amount: p.amount?.toString() || '0',
            currency: p.currency,
            // Cost fields (only if amount is non-zero)
            cost_amount: hasCost ? p.cost!.amount!.toString() : null,
            cost_currency: hasCost ? (p.cost!.currency || null) : null,
            cost_date: hasCost ? (p.cost!.date || null) : null,
            // Price fields (only if amount is non-zero)
            price_amount: hasPrice ? p.price!.amount!.toString() : null,
            price_currency: hasPrice ? (p.price!.currency || null) : null,
            price_type: hasPrice ? (p.price!.type || null) : null,
            // Posting metadata
            posting_meta: p.meta || null
          }
        }),
        // Source account (REQUIRED top-level field)
        source_account: tx.meta['source_account'] || '',

        // Additional metadata (optional)
        meta: tx.meta
      }))

      const request: CommitRequest = {
        transactions: commitTransactions
      }

      // Call backend API
      const response = await ImportService.commitTransactionsApiImportCommitPost(request)

      if (!response.data) {
        throw new Error('No data received from commit endpoint')
      }

      return {
        success: response.data.success,
        count: response.data.count
      }

    } catch (error) {
      if (error instanceof ApiError) {
        const errorBody = error.body as any
        commitError.value = {
          message: errorBody?.error?.message || 'Commit failed',
          details: errorBody?.error?.details ? JSON.stringify(errorBody.error.details) : undefined
        }
      } else {
        commitError.value = {
          message: 'An unexpected error occurred during commit',
          details: error instanceof Error ? error.message : String(error)
        }
      }
      throw error
    } finally {
      isLoading.value = false
    }
  }

  return {
    isLoading,
    categorizeError,
    commitError,
    performCategorization,
    performCommit
  }
}
