import type { WidgetRecipe } from '@/types/recipes'

/**
 * Format account name to show only the last part
 * e.g., "Expenses:Food:Groceries" -> "Groceries"
 */
function formatAccountName(account: string): string {
  const parts = account.split(':')
  return parts[parts.length - 1]
}

/**
 * Top Spending Categories Widget
 *
 * Shows a horizontal bar chart of top spending categories
 */
export const topSpendingCategoriesWidget: WidgetRecipe = {
  id: 'top-spending-categories',
  title: 'Top Spending Categories',
  description: 'Highest expense accounts by total amount',
  dbType: 'sqlite',
  parameters: [
    {
      name: 'limit',
      label: 'Show Top',
      type: 'number',
      default: 10,
      min: 5,
      max: 20,
    },
  ],
  query: `
    SELECT account, SUM(amount) as total
    FROM postings
    WHERE account_type = 'Expenses' AND currency = 'USD'
    GROUP BY account
    ORDER BY total DESC
    LIMIT :limit
  `,
  transform: (rows) => {
    // Transform for horizontal bar chart (need to reverse for proper display)
    return rows
      .map((row) => ({
        category: formatAccountName(String(row.account)),
        fullAccount: String(row.account),
        amount: Math.round(Number(row.total) || 0),
      }))
      .reverse() // Reverse so largest is at top in horizontal bar
  },
  visualization: {
    type: 'chart',
    chartType: 'bar',
    options: {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: { data: { fullAccount: string; amount: number } }[]) => {
          const data = params[0]?.data
          if (!data) return ''
          return `${data.fullAccount}<br/>$${data.amount.toLocaleString()}`
        },
      },
      grid: {
        left: 120,
        right: 24,
        top: 16,
        bottom: 16,
      },
      xAxis: {
        type: 'value',
        axisLabel: {
          formatter: (value: number) =>
            value >= 1000 ? `$${value / 1000}k` : `$${value}`,
        },
      },
      yAxis: {
        type: 'category',
        axisLabel: {
          width: 100,
          overflow: 'truncate',
        },
      },
      series: [
        {
          name: 'Amount',
          type: 'bar',
          encode: { x: 'amount', y: 'category' },
          itemStyle: {
            color: '#6366f1',
          },
          label: {
            show: true,
            position: 'right',
            formatter: (params: { data: { amount: number } }) =>
              `$${params.data.amount.toLocaleString()}`,
          },
        },
      ],
    },
  },
}
