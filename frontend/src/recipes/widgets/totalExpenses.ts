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
 * Total Expenses KPI Widget
 *
 * Shows total expenses for a selected year.
 */
export const totalExpensesWidget: WidgetRecipe = {
  id: 'total-expenses',
  title: 'Total Expenses',
  description: 'Sum of all expenses for the selected year',

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
    SELECT SUM(amount) AS total_expenses
    FROM postings
    WHERE account_type = 'Expenses' AND year = :year
  `,

  transform: (rows) => {
    if (rows.length === 0) return { value: 0 }
    return { value: Number(rows[0].total_expenses) || 0 }
  },

  visualization: {
    type: 'kpi',
    icon: '↓',
    formatValue: formatCurrency,
  },
}
