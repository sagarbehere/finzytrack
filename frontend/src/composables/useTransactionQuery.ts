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
    dbType: 'duckdb' | 'sqlite' = 'sqlite'
  ): Promise<TransactionViewModel[]> {
    isLoading.value = true
    error.value = null

    try {
      // Construct SQL query from filters
      const sqlQuery = buildSQLQuery(filters, dbType)

      // Execute query via API
      const queryRequest: QueryRequest = { query: sqlQuery }
      const response = await LedgerService.executeQuery(queryRequest, dbType)

      if (!response.success || !response.data) {
        throw new Error('Query failed: No data returned')
      }

      const queryData = response.data

      // Transform query results to TransactionViewModel[]
      const transactions = transformQueryResultsToTransactions(queryData.rows, dbType)

      return transactions

    } catch (err: any) {
      error.value = err.message || 'Failed to query transactions'
      console.error('Transaction query error:', err)
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
  function buildSQLQuery(filters: TransactionFilters, dbType: 'duckdb' | 'sqlite' = 'sqlite'): string {
    const whereClauses: string[] = []

    // Date filters
    if (filters.dateFrom) {
      whereClauses.push(`transaction_date >= '${filters.dateFrom}'`)
    }
    if (filters.dateTo) {
      whereClauses.push(`transaction_date <= '${filters.dateTo}'`)
    }

    // Amount filters (apply to posting amounts)
    if (filters.amountGreaterThan !== undefined) {
      whereClauses.push(`ABS(amount) > ${filters.amountGreaterThan}`)
    }
    if (filters.amountLessThan !== undefined) {
      whereClauses.push(`ABS(amount) < ${filters.amountLessThan}`)
    }

    // Text filters (case-insensitive LIKE)
    if (filters.payeeContains) {
      whereClauses.push(`LOWER(transaction_payee) LIKE LOWER('%${escapeSQLString(filters.payeeContains)}%')`)
    }
    if (filters.narrationContains) {
      whereClauses.push(`LOWER(transaction_narration) LIKE LOWER('%${escapeSQLString(filters.narrationContains)}%')`)
    }
    if (filters.accountContains) {
      whereClauses.push(`LOWER(account) LIKE LOWER('%${escapeSQLString(filters.accountContains)}%')`)
    }

    // Tags filter
    // DuckDB: transaction_tags is VARCHAR[] array, use unnest()
    // SQLite: transaction_tags is TEXT JSON string, use LIKE
    if (filters.tagsContain) {
      if (dbType === 'duckdb') {
        whereClauses.push(`EXISTS(SELECT 1 FROM unnest(transaction_tags) AS tag WHERE LOWER(tag) LIKE LOWER('%${escapeSQLString(filters.tagsContain)}%'))`)
      } else {
        whereClauses.push(`LOWER(transaction_tags) LIKE LOWER('%${escapeSQLString(filters.tagsContain)}%')`)
      }
    }

    // Links filter (same logic as tags)
    if (filters.linksContain) {
      if (dbType === 'duckdb') {
        whereClauses.push(`EXISTS(SELECT 1 FROM unnest(transaction_links) AS link WHERE LOWER(link) LIKE LOWER('%${escapeSQLString(filters.linksContain)}%'))`)
      } else {
        whereClauses.push(`LOWER(transaction_links) LIKE LOWER('%${escapeSQLString(filters.linksContain)}%')`)
      }
    }

    // Currency filter
    if (filters.currency) {
      whereClauses.push(`currency = '${escapeSQLString(filters.currency)}'`)
    }

    // Flag filter
    if (filters.flag) {
      whereClauses.push(`transaction_flag = '${escapeSQLString(filters.flag)}'`)
    }

    // Account Type filter
    if (filters.accountType) {
      whereClauses.push(`account_type = '${escapeSQLString(filters.accountType)}'`)
    }

    // Year filter
    if (filters.year !== undefined) {
      whereClauses.push(`year = ${filters.year}`)
    }

    // Quarter filter
    if (filters.quarter !== undefined) {
      whereClauses.push(`quarter = ${filters.quarter}`)
    }

    const whereClause = whereClauses.length > 0 ? `WHERE ${whereClauses.join(' AND ')}` : ''

    // SQL Query to reconstruct transactions
    // Different aggregation syntax for DuckDB vs SQLite
    let query: string

    if (dbType === 'duckdb') {
      // DuckDB: Use LIST() and STRUCT_PACK() for structured aggregation
      query = `
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
        ORDER BY transaction_date DESC, transaction_id
        LIMIT 1000
      `.trim()
    } else {
      // SQLite: Use json_group_array() and json_object() for aggregation
      query = `
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
        ORDER BY transaction_date DESC, transaction_id
        LIMIT 1000
      `.trim()
    }

    return query
  }

  /**
   * Transform SQL query results into TransactionViewModel array.
   * Handles both DuckDB and SQLite result formats.
   */
  function transformQueryResultsToTransactions(rows: any[], dbType: 'duckdb' | 'sqlite' = 'sqlite'): TransactionViewModel[] {
    return rows.map((row) => {
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
