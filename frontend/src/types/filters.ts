/**
 * Filter parameters for querying transactions.
 */
export interface TransactionFilters {
  // Date filters
  dateFrom?: string          // ISO date YYYY-MM-DD
  dateTo?: string            // ISO date YYYY-MM-DD

  // Amount filters
  amountGreaterThan?: number
  amountLessThan?: number

  // Text filters
  payeeContains?: string
  narrationContains?: string
  accountContains?: string
  tagsContain?: string       // Searches within transaction tags array
  linksContain?: string      // Searches within transaction links array

  // Categorical filters
  currency?: string          // e.g., "USD", "EUR"
  flag?: string              // e.g., "*", "!"
  accountType?: string       // e.g., "Assets", "Expenses", "Income", "Liabilities", "Equity"

  // Time-based filters
  year?: number              // e.g., 2024
  quarter?: number           // 1-4
}
