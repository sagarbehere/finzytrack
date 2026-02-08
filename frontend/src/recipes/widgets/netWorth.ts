import type { WidgetRecipe } from '@/types/recipes'

/**
 * Format a number as currency
 */
function formatCurrency(value: number): string {
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })
}

/**
 * Net Worth KPI Widget
 *
 * Shows total net worth (Assets - Liabilities)
 */
export const netWorthWidget: WidgetRecipe = {
  id: 'net-worth',
  title: 'Net Worth',
  description: 'Total assets minus liabilities',
  dbType: 'sqlite',
  query: `
    SELECT
      SUM(CASE WHEN account_type = 'Assets' THEN amount ELSE 0 END) as total_assets,
      SUM(CASE WHEN account_type = 'Liabilities' THEN amount ELSE 0 END) as total_liabilities,
      SUM(CASE WHEN account_type IN ('Assets', 'Liabilities') THEN amount ELSE 0 END) as net_worth
    FROM postings
    WHERE currency = 'USD'
  `,
  transform: (rows) => {
    if (rows.length === 0) return { value: 0 }
    const row = rows[0]
    return {
      value: Number(row.net_worth) || 0,
      totalAssets: Number(row.total_assets) || 0,
      totalLiabilities: Number(row.total_liabilities) || 0,
    }
  },
  visualization: {
    type: 'kpi',
    icon: '$',
    formatValue: formatCurrency,
  },
}
