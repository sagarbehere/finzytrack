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
    dbType: 'duckdb' | 'sqlite' = 'sqlite',
    limit: number = 1000
  ): Promise<{ transactions: TransactionViewModel[], totalCount: number | null }> {
    isLoading.value = true
    error.value = null

    try {
      // Construct SQL query from filters
      const sqlQuery = buildSQLQuery(filters, dbType, limit)

      console.log('Executing SQL query:', sqlQuery)

      // Execute query via API
      const queryRequest: QueryRequest = { query: sqlQuery }
      const response = await LedgerService.executeQuery(queryRequest, dbType)

      if (!response.success || !response.data) {
        throw new Error('Query failed: No data returned')
      }

      const queryData = response.data

      // Transform query results to TransactionViewModel[]
      const result = transformQueryResultsToTransactions(queryData.rows, dbType)

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
   * 1. Query the postings table (DuckDB or SQLite, exported by backend)
   * 2. Group by transaction_id to reconstruct transactions
   * 3. Apply WHERE clauses based on filters
   * 4. Use aggregation to collect postings per transaction
   *
   * Database Differences:
   * - DuckDB: Uses LIST() and STRUCT_PACK() for aggregation, native array support
   * - SQLite: Arrays stored as JSON strings, requires json_group_array() for aggregation
   */
  function buildSQLQuery(filters: TransactionFilters, dbType: 'duckdb' | 'sqlite' = 'sqlite', limit: number = 1000): string {
    // Separate filters into two categories:
    // 1. Transaction-level filters: Apply to transaction fields (safe to use in WHERE before GROUP BY)
    //    These work because all postings in a transaction share the same transaction-level values
    // 2. Posting-level filters: Apply to posting fields (need subquery to keep transactions balanced)
    //    These need special handling because different postings in same transaction have different values

    const transactionLevelWhereClauses: string[] = []
    const postingLevelFilters: string[] = []

    // Date filters (transaction-level - all postings in same transaction have same date)
    if (filters.dateFrom) {
      transactionLevelWhereClauses.push(`transaction_date >= '${filters.dateFrom}'`)
    }
    if (filters.dateTo) {
      transactionLevelWhereClauses.push(`transaction_date <= '${filters.dateTo}'`)
    }

    // Payee filter (transaction-level - all postings in same transaction have same payee)
    if (filters.payeeContains) {
      transactionLevelWhereClauses.push(`LOWER(transaction_payee) LIKE LOWER('%${escapeSQLString(filters.payeeContains)}%')`)
    }

    // Narration filter (transaction-level - all postings in same transaction have same narration)
    if (filters.narrationContains) {
      transactionLevelWhereClauses.push(`LOWER(transaction_narration) LIKE LOWER('%${escapeSQLString(filters.narrationContains)}%')`)
    }

    // Flag filter (transaction-level - all postings in same transaction have same flag)
    if (filters.flag) {
      transactionLevelWhereClauses.push(`transaction_flag = '${escapeSQLString(filters.flag)}'`)
    }

    // Tags filter (transaction-level - all postings in same transaction have same tags)
    if (filters.tagsContain) {
      if (dbType === 'duckdb') {
        transactionLevelWhereClauses.push(`EXISTS(SELECT 1 FROM unnest(transaction_tags) AS tag WHERE LOWER(tag) LIKE LOWER('%${escapeSQLString(filters.tagsContain)}%'))`)
      } else {
        transactionLevelWhereClauses.push(`LOWER(transaction_tags) LIKE LOWER('%${escapeSQLString(filters.tagsContain)}%')`)
      }
    }

    // Links filter (transaction-level - all postings in same transaction have same links)
    if (filters.linksContain) {
      if (dbType === 'duckdb') {
        transactionLevelWhereClauses.push(`EXISTS(SELECT 1 FROM unnest(transaction_links) AS link WHERE LOWER(link) LIKE LOWER('%${escapeSQLString(filters.linksContain)}%'))`)
      } else {
        transactionLevelWhereClauses.push(`LOWER(transaction_links) LIKE LOWER('%${escapeSQLString(filters.linksContain)}%')`)
      }
    }

    // Year filter (transaction-level - all postings in same transaction have same year)
    if (filters.year !== undefined) {
      transactionLevelWhereClauses.push(`year = ${filters.year}`)
    }

    // Quarter filter (transaction-level - all postings in same transaction have same quarter)
    if (filters.quarter !== undefined) {
      transactionLevelWhereClauses.push(`quarter = ${filters.quarter}`)
    }

    // POSTING-LEVEL FILTERS
    // These filters apply to individual posting fields that can differ within a transaction
    // We use a subquery to find transactions with at least one matching posting,
    // then include ALL postings from those transactions to keep them balanced

    // Account filter (posting-level - different postings have different accounts)
    if (filters.accountContains) {
      postingLevelFilters.push(`LOWER(account) LIKE LOWER('%${escapeSQLString(filters.accountContains)}%')`)
    }

    // Amount filters (posting-level - different postings have different amounts)
    if (filters.amountGreaterThan !== undefined) {
      postingLevelFilters.push(`ABS(amount) > ${filters.amountGreaterThan}`)
    }
    if (filters.amountLessThan !== undefined) {
      postingLevelFilters.push(`ABS(amount) < ${filters.amountLessThan}`)
    }

    // Currency filter (posting-level - different postings can have different currencies)
    if (filters.currency) {
      postingLevelFilters.push(`currency = '${escapeSQLString(filters.currency)}'`)
    }

    // Account Type filter (posting-level - different postings have different account types)
    if (filters.accountType) {
      postingLevelFilters.push(`account_type = '${escapeSQLString(filters.accountType)}'`)
    }

    // Build WHERE clause
    // If we have posting-level filters, add a subquery to find matching transaction IDs
    if (postingLevelFilters.length > 0) {
      const postingFilterClause = postingLevelFilters.join(' AND ')
      // Subquery: Find all transaction IDs where at least one posting matches the filter
      // Main query: Include ALL postings from those transactions
      transactionLevelWhereClauses.push(
        `transaction_id IN (SELECT DISTINCT transaction_id FROM postings WHERE ${postingFilterClause})`
      )
    }

    const whereClause = transactionLevelWhereClauses.length > 0
      ? `WHERE ${transactionLevelWhereClauses.join(' AND ')}`
      : ''

    // SQL Query to reconstruct transactions
    // Different aggregation syntax for DuckDB vs SQLite
    let query: string

    if (dbType === 'duckdb') {
      // DuckDB: Use LIST() and STRUCT_PACK() for structured aggregation
      query = `
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
            LIST(STRUCT_PACK(
              account := account,
              amount := amount,
              currency := currency,
              cost_amount := cost_amount,
              cost_currency := cost_currency,
              price_amount := price_amount,
              price_currency := price_currency,
              posting_metadata_json := posting_metadata_json
            )) AS postings
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
    } else {
      // SQLite: Use json_group_array() and json_object() for aggregation
      query = `
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

    return query
  }

  /**
   * Transform SQL query results into TransactionViewModel array.
   * Handles both DuckDB and SQLite result formats.
   */
  function transformQueryResultsToTransactions(rows: any[], dbType: 'duckdb' | 'sqlite' = 'sqlite'): { transactions: TransactionViewModel[], totalCount: number | null } {
    // Extract total count from first row (all rows have same total_count due to window function)
    const totalCount = rows.length > 0 && rows[0].total_count !== undefined ? rows[0].total_count : null

    const transactions = rows.map((row) => {
      // Parse metadata JSON
      const metadata = row.transaction_metadata_json ? JSON.parse(row.transaction_metadata_json) : {}

      // Parse tags and links
      // DuckDB: Native arrays
      // SQLite: JSON string arrays that need parsing
      let tags: string[] = []
      let links: string[] = []

      if (dbType === 'duckdb') {
        tags = row.transaction_tags || []
        links = row.transaction_links || []
      } else {
        // SQLite: Parse JSON strings
        tags = row.transaction_tags ? JSON.parse(row.transaction_tags) : []
        links = row.transaction_links ? JSON.parse(row.transaction_links) : []
      }

      // Parse postings
      // DuckDB: Array of structs
      // SQLite: JSON string that needs parsing
      let postingsData: any[]
      if (dbType === 'duckdb') {
        postingsData = row.postings || []
      } else {
        // SQLite: json_group_array() returns a JSON string
        postingsData = typeof row.postings === 'string' ? JSON.parse(row.postings) : row.postings
      }

      const postings: PostingViewModel[] = postingsData.map((p: any) => {
        const posting: PostingViewModel = {
          account: p.account,
          amount: p.amount,
          currency: p.currency,
        }

        // Add cost if present
        if (p.cost_amount !== null || p.cost_currency !== null) {
          posting.cost = {
            amount: p.cost_amount,
            currency: p.cost_currency,
          }
        }

        // Add price if present
        if (p.price_amount !== null || p.price_currency !== null) {
          posting.price = {
            amount: p.price_amount,
            currency: p.price_currency,
            type: '@', // Default to per-unit
          }
        }

        // Add posting metadata if present
        if (p.posting_metadata_json) {
          posting.meta = JSON.parse(p.posting_metadata_json)
        }

        return posting
      })

      // Construct TransactionViewModel
      const transaction: TransactionViewModel = {
        id: row.transaction_id,
        date: row.transaction_date,
        flag: row.transaction_flag,
        payee: row.transaction_payee,
        narration: row.transaction_narration,
        tags: tags,
        links: links,
        postings: postings,
        meta: metadata,
        internal: {
          isNew: false,
          isModified: false,
        },
      }

      // Extract memo if present in metadata
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
