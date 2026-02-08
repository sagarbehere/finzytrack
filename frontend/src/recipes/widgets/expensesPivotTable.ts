import type { WidgetRecipe, PivotData, PivotRow, PivotLinkContext, ValueLinkConfig } from '@/types/recipes'

function generateYearOptions() {
  const currentYear = new Date().getFullYear()
  const years = []
  for (let year = currentYear; year >= currentYear - 5; year--) {
    years.push({ value: year, label: String(year) })
  }
  return years
}

/**
 * Format month from YYYY-MM to "Month YYYY" (e.g., "June 2025")
 */
function formatMonthHeader(yearMonth: string): string {
  const [year, month] = yearMonth.split('-')
  const monthNames = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ]
  const monthIndex = parseInt(month, 10) - 1
  return `${monthNames[monthIndex]} ${year}`
}

/**
 * Get the first day of a month from YYYY-MM format
 */
function getMonthStart(yearMonth: string): string {
  return `${yearMonth}-01`
}

/**
 * Get the last day of a month from YYYY-MM format
 */
function getMonthEnd(yearMonth: string): string {
  const [year, month] = yearMonth.split('-').map(Number)
  // Day 0 of next month = last day of current month
  const lastDay = new Date(year, month, 0).getDate()
  return `${yearMonth}-${String(lastDay).padStart(2, '0')}`
}

/**
 * Expenses Pivot Table Widget
 *
 * Shows a pivot table with expense accounts as rows, months as columns,
 * and amounts as values. Includes row totals and column (grand) totals.
 *
 * Cell values are clickable and link to the Transactions page with
 * appropriate filters for the account and date range.
 */
export const expensesPivotTableWidget: WidgetRecipe = {
  id: 'expenses-pivot-table',
  title: 'Expenses Pivot Table',
  description: 'Monthly breakdown of expenses by account',
  dbType: 'sqlite',

  parameters: [
    {
      name: 'year',
      label: 'Year',
      type: 'select',
      default: new Date().getFullYear(),
      options: generateYearOptions(),
    },
  ],

  query: `
    SELECT
      account,
      year_month,
      SUM(amount) as amount
    FROM postings
    WHERE account_type = 'Expenses'
      AND year = :year
      AND currency = 'USD'
    GROUP BY account, year_month
    ORDER BY account, year_month
  `,

  transform: (rows): PivotData => {
    // Collect unique months and accounts
    const monthsSet = new Set<string>()
    const accountsMap = new Map<string, Map<string, number>>()

    for (const row of rows) {
      const account = String(row.account)
      const yearMonth = String(row.year_month)
      const amount = Number(row.amount) || 0

      monthsSet.add(yearMonth)

      if (!accountsMap.has(account)) {
        accountsMap.set(account, new Map())
      }
      accountsMap.get(account)!.set(yearMonth, amount)
    }

    // Sort months chronologically
    const months = Array.from(monthsSet).sort()
    const columns = months.map(formatMonthHeader)

    // Create column metadata with raw YYYY-MM values for link generation
    const columnMeta = months.map((yearMonth) => ({
      yearMonth,
      startDate: getMonthStart(yearMonth),
      endDate: getMonthEnd(yearMonth),
    }))

    // Build pivot rows
    const pivotRows: PivotRow[] = []
    const columnTotals: Record<string, number> = {}
    let grandTotal = 0

    // Initialize column totals
    for (const col of columns) {
      columnTotals[col] = 0
    }

    // Sort accounts alphabetically
    const sortedAccounts = Array.from(accountsMap.keys()).sort()

    for (const account of sortedAccounts) {
      const monthValues = accountsMap.get(account)!
      const values: Record<string, number> = {}
      let rowTotal = 0

      for (let i = 0; i < months.length; i++) {
        const month = months[i]
        const colName = columns[i]
        const amount = monthValues.get(month) || 0

        if (amount !== 0) {
          values[colName] = amount
          rowTotal += amount
          columnTotals[colName] += amount
        }
      }

      grandTotal += rowTotal

      pivotRows.push({
        label: account,
        values,
        total: rowTotal,
        // Store full account path in meta for link generation
        meta: { fullAccountPath: account },
      })
    }

    // Sort rows by total (descending) for better visibility
    pivotRows.sort((a, b) => (b.total || 0) - (a.total || 0))

    return {
      columns,
      rows: pivotRows,
      columnTotals,
      grandTotal,
      columnMeta,
    }
  },

  visualization: {
    type: 'pivot',
    rowHeader: 'Account',
    showRowTotals: true,
    showColumnTotals: true,

    /**
     * Generate link to Transactions page for each cell value.
     * Links filter by account name and date range (month).
     */
    getValueLink: (context: PivotLinkContext): ValueLinkConfig | null => {
      const { rowData } = context

      // Get the account path from row
      const accountPath = rowData.meta?.fullAccountPath as string || rowData.label

      // Get date range from column metadata
      // The columnMeta is attached to PivotData, accessible via rowData's parent
      // But we don't have direct access here, so we need to parse the column name
      // Column format: "Month YYYY" (e.g., "January 2025")
      const column = context.column
      const match = column.match(/^(\w+)\s+(\d{4})$/)

      if (!match) return null

      const monthName = match[1]
      const year = match[2]
      const monthNames = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
      ]
      const monthIndex = monthNames.indexOf(monthName)
      if (monthIndex === -1) return null

      const month = String(monthIndex + 1).padStart(2, '0')
      const yearMonth = `${year}-${month}`

      return {
        name: 'transactions',
        query: {
          accountContains: accountPath,
          dateFrom: getMonthStart(yearMonth),
          dateTo: getMonthEnd(yearMonth),
        },
      }
    },
  },
}
