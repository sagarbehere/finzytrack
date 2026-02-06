import type { WidgetRecipe } from '@/types/recipes'

function formatCurrency(value: number): string {
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })
}

function generateYearOptions() {
  const currentYear = new Date().getFullYear()
  const years = []
  for (let year = currentYear; year >= currentYear - 5; year--) {
    years.push({ value: year, label: String(year) })
  }
  return years
}

/**
 * Total Income KPI Widget
 *
 * Shows total income for a selected year.
 * Income amounts are negated since they're stored as negative (credits).
 */
export const totalIncomeWidget: WidgetRecipe = {
  id: 'total-income',
  title: 'Total Income',
  description: 'Sum of all income for the selected year',

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
    SELECT SUM(amount) * -1 AS total_income
    FROM postings
    WHERE account_type = 'Income' AND year = :year
  `,

  transform: (rows) => {
    if (rows.length === 0) return { value: 0 }
    return { value: Number(rows[0].total_income) || 0 }
  },

  visualization: {
    type: 'kpi',
    icon: '↑',
    formatValue: formatCurrency,
  },
}
