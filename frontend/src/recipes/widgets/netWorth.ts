import type { WidgetRecipe } from '@/types/recipes'

/**
 * Net Worth KPI Widget
 *
 * Shows total net worth (Assets - Liabilities) per currency.
 * Uses multi-currency display to show stacked amounts.
 */
export const netWorthWidget: WidgetRecipe = {
  id: 'net-worth',
  title: 'Net Worth',
  description: 'Total assets minus liabilities',
  dbType: 'sqlite',
  query: `
    SELECT
      currency,
      SUM(CASE WHEN account_type IN ('Assets', 'Liabilities') THEN amount ELSE 0 END) as amount
    FROM postings
    GROUP BY currency
    HAVING amount != 0
    ORDER BY ABS(amount) DESC
  `,
  transform: (rows) => {
    return rows.map((row) => ({
      amount: Number(row.amount) || 0,
      currency: String(row.currency),
    }))
  },
  visualization: {
    type: 'kpi',
    icon: '$',
    multiCurrency: true,
  },
}
