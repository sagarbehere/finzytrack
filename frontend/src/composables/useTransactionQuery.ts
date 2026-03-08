import { ref } from 'vue'
import type { TransactionViewModel, PostingViewModel } from '@/types/transactions'
import type { TransactionFilters } from '@/types/filters'
import { LedgerService } from '@/services/generated-api'
import type { QueryRequest } from '@/services/generated-api'

export function useTransactionQuery() {
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  /**
   * Query transactions based on filters.
   * Returns TransactionViewModel[] for use with TransactionTable.
   */
  async function queryTransactions(
    filters: TransactionFilters,
    limit: number = 1000
  ): Promise<{ transactions: TransactionViewModel[], totalCount: number | null }> {
    isLoading.value = true
    error.value = null

    try {
      const sqlQuery = buildSQLQuery(filters, limit)

      console.log('Executing SQL query:', sqlQuery)

      const queryRequest: QueryRequest = { query: sqlQuery }
      const response = await LedgerService.executeQuery(queryRequest, 'sqlite')

      if (!response.success || !response.data) {
        throw new Error('Query failed: No data returned')
      }

      const result = transformQueryResultsToTransactions(response.data.rows)

      return result

    } catch (err: any) {
      error.value = err.message || 'Failed to query transactions'
      console.error('Transaction query error:', err)
      console.error('Error body:', err.body)
      throw err
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Build SQL query from filter parameters.
   *
   * Strategy:
   * 1. Query the postings table (SQLite, exported by backend)
   * 2. Group by transaction_id to reconstruct transactions
   * 3. Apply WHERE clauses based on filters
   * 4. Use json_group_array() to collect postings per transaction
   */
  function buildSQLQuery(filters: TransactionFilters, limit: number = 1000): string {
    // Separate filters into two categories:
    // 1. Transaction-level filters: Apply to transaction fields (safe to use in WHERE before GROUP BY)
    //    These work because all postings in a transaction share the same transaction-level values
    // 2. Posting-level filters: Apply to posting fields (need subquery to keep transactions balanced)
    //    These need special handling because different postings in same transaction have different values

    const transactionLevelWhereClauses: string[] = []
    const postingLevelFilters: string[] = []

    if (filters.dateFrom) {
      transactionLevelWhereClauses.push(`transaction_date >= '${filters.dateFrom}'`)
    }
    if (filters.dateTo) {
      transactionLevelWhereClauses.push(`transaction_date <= '${filters.dateTo}'`)
    }
    if (filters.payeeContains) {
      transactionLevelWhereClauses.push(`LOWER(transaction_payee) LIKE LOWER('%${escapeSQLString(filters.payeeContains)}%')`)
    }
    if (filters.narrationContains) {
      transactionLevelWhereClauses.push(`LOWER(transaction_narration) LIKE LOWER('%${escapeSQLString(filters.narrationContains)}%')`)
    }
    if (filters.flag) {
      transactionLevelWhereClauses.push(`transaction_flag = '${escapeSQLString(filters.flag)}'`)
    }
    if (filters.tagsContain) {
      transactionLevelWhereClauses.push(`LOWER(transaction_tags) LIKE LOWER('%${escapeSQLString(filters.tagsContain)}%')`)
    }
    if (filters.linksContain) {
      transactionLevelWhereClauses.push(`LOWER(transaction_links) LIKE LOWER('%${escapeSQLString(filters.linksContain)}%')`)
    }
    if (filters.year !== undefined) {
      transactionLevelWhereClauses.push(`year = ${filters.year}`)
    }
    if (filters.quarter !== undefined) {
      transactionLevelWhereClauses.push(`quarter = ${filters.quarter}`)
    }

    // POSTING-LEVEL FILTERS
    if (filters.accountContains) {
      postingLevelFilters.push(`LOWER(account) LIKE LOWER('%${escapeSQLString(filters.accountContains)}%')`)
    }
    if (filters.amountGreaterThan !== undefined) {
      postingLevelFilters.push(`ABS(amount) > ${filters.amountGreaterThan}`)
    }
    if (filters.amountLessThan !== undefined) {
      postingLevelFilters.push(`ABS(amount) < ${filters.amountLessThan}`)
    }
    if (filters.currency) {
      postingLevelFilters.push(`currency = '${escapeSQLString(filters.currency)}'`)
    }
    if (filters.accountType) {
      postingLevelFilters.push(`account_type = '${escapeSQLString(filters.accountType)}'`)
    }

    if (postingLevelFilters.length > 0) {
      const postingFilterClause = postingLevelFilters.join(' AND ')
      transactionLevelWhereClauses.push(
        `transaction_id IN (SELECT DISTINCT transaction_id FROM postings WHERE ${postingFilterClause})`
      )
    }

    const whereClause = transactionLevelWhereClauses.length > 0
      ? `WHERE ${transactionLevelWhereClauses.join(' AND ')}`
      : ''

    return `
      WITH grouped_transactions AS (
        SELECT
          transaction_id,
          transaction_content_hash,
          transaction_date,
          transaction_flag,
          transaction_payee,
          transaction_narration,
          transaction_tags,
          transaction_links,
          transaction_metadata_json,
          json_group_array(
            json_object(
              'account', account,
              'amount', amount,
              'currency', currency,
              'cost_amount', cost_amount,
              'cost_currency', cost_currency,
              'price_amount', price_amount,
              'price_currency', price_currency,
              'posting_metadata_json', posting_metadata_json
            )
          ) AS postings
        FROM postings
        ${whereClause}
        GROUP BY
          transaction_id,
          transaction_content_hash,
          transaction_date,
          transaction_flag,
          transaction_payee,
          transaction_narration,
          transaction_tags,
          transaction_links,
          transaction_metadata_json
      )
      SELECT
        *,
        COUNT(*) OVER() as total_count
      FROM grouped_transactions
      ORDER BY transaction_date DESC, transaction_id
      LIMIT ${limit}
    `.trim()
  }

  /**
   * Transform SQLite query results into TransactionViewModel array.
   */
  function transformQueryResultsToTransactions(rows: any[]): { transactions: TransactionViewModel[], totalCount: number | null } {
    const totalCount = rows.length > 0 && rows[0].total_count !== undefined ? rows[0].total_count : null

    const transactions = rows.map((row) => {
      const metadata = row.transaction_metadata_json ? JSON.parse(row.transaction_metadata_json) : {}

      // SQLite stores tags/links as JSON strings
      const tags: string[] = row.transaction_tags ? JSON.parse(row.transaction_tags) : []
      const links: string[] = row.transaction_links ? JSON.parse(row.transaction_links) : []

      // SQLite: json_group_array() returns a JSON string
      const postingsData: any[] = typeof row.postings === 'string' ? JSON.parse(row.postings) : row.postings

      const postings: PostingViewModel[] = postingsData.map((p: any) => {
        const posting: PostingViewModel = {
          account: p.account,
          amount: p.amount,
          currency: p.currency,
        }

        if (p.cost_amount !== null || p.cost_currency !== null) {
          posting.cost = {
            amount: p.cost_amount,
            currency: p.cost_currency,
          }
        }

        if (p.price_amount !== null || p.price_currency !== null) {
          posting.price = {
            amount: p.price_amount,
            currency: p.price_currency,
            type: '@',
          }
        }

        if (p.posting_metadata_json) {
          posting.meta = JSON.parse(p.posting_metadata_json)
        }

        return posting
      })

      const transaction: TransactionViewModel = {
        id: row.transaction_id,
        date: row.transaction_date,
        flag: row.transaction_flag,
        payee: row.transaction_payee,
        narration: row.transaction_narration,
        tags,
        links,
        postings,
        meta: metadata,
        internal: {
          isNew: false,
          isModified: false,
        },
      }

      if (metadata.ofx_memo) {
        transaction.memo = metadata.ofx_memo
      }

      return transaction
    })

    return { transactions, totalCount }
  }

  /**
   * Escape SQL string to prevent injection.
   * Note: This is basic escaping. For production, consider using parameterized queries.
   */
  function escapeSQLString(str: string): string {
    return str.replace(/'/g, "''")
  }

  return {
    queryTransactions,
    isLoading,
    error,
  }
}
