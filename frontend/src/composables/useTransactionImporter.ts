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
          narration: tx.narration,
          amount: tx.postings[0]?.amount?.toString() || '0',
          ofx_id: tx.meta.ofx_id
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
      // Map TransactionViewModel to backend schema
      const commitTransactions: CommitTransaction[] = transactions.map(tx => ({
        date: tx.date,
        flag: tx.flag,
        payee: tx.payee,
        narration: tx.narration,
        tags: tx.tags,
        links: tx.links,
        postings: tx.postings.map(p => ({
          account: p.account,
          amount: p.amount?.toString() || '0',
          currency: p.currency
        })),
        ofx_id: tx.meta.ofx_id,
        source_account: tx.meta.source_account || ''
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
