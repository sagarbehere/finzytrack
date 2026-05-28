/**
 * Why this composable assembles SQL on the frontend rather than calling a
 * structured-filter endpoint:
 *
 * SQL-on-the-wire to ``/api/ledger/query`` is the app's user-facing query
 * API by design, not an implementation leak. Three other places already
 * cross this boundary intentionally:
 *   1. The SQL Assistant view, where users type queries directly.
 *   2. Recipes (dashboards/widgets) embed user-authored SQL.
 *   3. The AI assistant's ``execute_query`` tool writes SQL on the user's
 *      behalf.
 * The schema is *published* (see backend ``GET /api/ledger/schema/postings``)
 * so users / AI can compose queries against it. The backend enforces
 * read-only at the engine level via ``PRAGMA query_only = true`` per
 * connection — that's the load-bearing safety property, and it's not
 * bypassable by input crafting.
 *
 * This composable assembles SQL on the user's behalf using form filter
 * values — it's another *front-end* to the same public SQL surface, not a
 * new abstraction. Replacing it with a parallel structured-filter
 * endpoint would double the API surface for marginal gain (one fewer file
 * touching the schema, while four other places by design still do).
 *
 * Revisit only if (a) the read-only enforcement story changes, or (b) a
 * second non-trivial frontend client needs the same data. See
 * dev-docs/code-review.md H8 #2 for the audit trail.
 */
import { ref } from 'vue'
import type { TransactionViewModel, PostingViewModel } from '@/types/transactions'
import type { TransactionFilters } from '@/types/filters'
import { LedgerService } from '@/services/generated-api'
import type { QueryRequest } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

/**
 * Escape a value for use inside a single-quoted SQL string literal.
 *
 * Doubles single quotes per SQL's standard escape. Read-only enforcement
 * (`PRAGMA query_only`) lives on the backend connection — see query.py —
 * so this is only about producing syntactically valid SQL, not an
 * injection defense.
 */
function escapeSQLString(str: string): string {
  return str.replace(/'/g, "''")
}

/**
 * Escape a value for use inside a SQL ``LIKE`` pattern.
 *
 * In addition to the single-quote escape, `%` and `_` are LIKE wildcards
 * (match any sequence / single char) and the backslash is the escape
 * character we declare via ``ESCAPE '\'``. Without this, a user searching
 * for ``100%`` would match ``1004``, ``100A``, etc., and ``path_to``
 * would match ``pathXto``. Use together with ``ESCAPE '\'`` on the LIKE
 * clause so SQLite knows about the escape character.
 */
function escapeSQLLike(str: string): string {
  return str
    .replace(/\\/g, "\\\\")  // escape the escape char first
    .replace(/%/g, "\\%")
    .replace(/_/g, "\\_")
    .replace(/'/g, "''")
}

/**
 * Build WHERE clauses from transaction-level and posting-level filters.
 * Returns the full WHERE clause string (including the WHERE keyword), or empty string if no filters.
 */
function buildWhereClause(filters: TransactionFilters): string {
  const transactionLevelWhereClauses: string[] = []
  const postingLevelFilters: string[] = []

  if (filters.dateFrom) {
    transactionLevelWhereClauses.push(`transaction_date >= '${filters.dateFrom}'`)
  }
  if (filters.dateTo) {
    transactionLevelWhereClauses.push(`transaction_date <= '${filters.dateTo}'`)
  }
  if (filters.search) {
    const term = escapeSQLLike(filters.search)
    transactionLevelWhereClauses.push(
      `(LOWER(transaction_payee) LIKE LOWER('%${term}%') ESCAPE '\\' `
      + `OR LOWER(transaction_narration) LIKE LOWER('%${term}%') ESCAPE '\\')`
    )
  }
  if (filters.payeeContains) {
    transactionLevelWhereClauses.push(`LOWER(transaction_payee) LIKE LOWER('%${escapeSQLLike(filters.payeeContains)}%') ESCAPE '\\'`)
  }
  if (filters.narrationContains) {
    transactionLevelWhereClauses.push(`LOWER(transaction_narration) LIKE LOWER('%${escapeSQLLike(filters.narrationContains)}%') ESCAPE '\\'`)
  }
  if (filters.flag) {
    transactionLevelWhereClauses.push(`LOWER(transaction_flag) = LOWER('${escapeSQLString(filters.flag)}')`)
  }
  if (filters.tagsContain) {
    transactionLevelWhereClauses.push(`LOWER(transaction_tags) LIKE LOWER('%${escapeSQLLike(filters.tagsContain)}%') ESCAPE '\\'`)
  }
  if (filters.linksContain) {
    transactionLevelWhereClauses.push(`LOWER(transaction_links) LIKE LOWER('%${escapeSQLLike(filters.linksContain)}%') ESCAPE '\\'`)
  }
  if (filters.year !== undefined) {
    transactionLevelWhereClauses.push(`year = ${filters.year}`)
  }
  if (filters.quarter !== undefined) {
    transactionLevelWhereClauses.push(`quarter = ${filters.quarter}`)
  }

  // POSTING-LEVEL FILTERS
  if (filters.accountContains) {
    postingLevelFilters.push(`LOWER(account) LIKE LOWER('%${escapeSQLLike(filters.accountContains)}%') ESCAPE '\\'`)
  }
  if (filters.amountGreaterThan !== undefined) {
    postingLevelFilters.push(`ABS(amount) > ${filters.amountGreaterThan}`)
  }
  if (filters.amountLessThan !== undefined) {
    postingLevelFilters.push(`ABS(amount) < ${filters.amountLessThan}`)
  }
  if (filters.currency) {
    postingLevelFilters.push(`LOWER(currency) = LOWER('${escapeSQLString(filters.currency)}')`)
  }
  if (filters.accountType) {
    postingLevelFilters.push(`LOWER(account_type) = LOWER('${escapeSQLString(filters.accountType)}')`)
  }

  if (postingLevelFilters.length > 0) {
    const postingFilterClause = postingLevelFilters.join(' AND ')
    transactionLevelWhereClauses.push(
      `transaction_id IN (SELECT DISTINCT transaction_id FROM postings WHERE ${postingFilterClause})`
    )
  }

  return transactionLevelWhereClauses.length > 0
    ? `WHERE ${transactionLevelWhereClauses.join(' AND ')}`
    : ''
}

/** Build the GROUP BY clause for transaction grouping. */
function buildGroupByClause(): string {
  return `GROUP BY
          transaction_id,
          transaction_content_hash,
          transaction_date,
          transaction_flag,
          transaction_payee,
          transaction_narration,
          transaction_tags,
          transaction_links,
          transaction_metadata_json`
}

/** Build the ORDER BY clause for result ordering. */
function buildOrderByClause(): string {
  return `ORDER BY transaction_date DESC, transaction_id`
}

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
      errorHandler.display(err)
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
    const whereClause = buildWhereClause(filters)
    const groupByClause = buildGroupByClause()
    const orderByClause = buildOrderByClause()

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
        ${groupByClause}
      )
      SELECT
        *,
        COUNT(*) OVER() as total_count
      FROM grouped_transactions
      ${orderByClause}
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

      if (metadata.memo || metadata.ofx_memo) {
        transaction.memo = metadata.memo || metadata.ofx_memo
      }

      return transaction
    })

    return { transactions, totalCount }
  }

  return {
    queryTransactions,
    isLoading,
    error,
  }
}
