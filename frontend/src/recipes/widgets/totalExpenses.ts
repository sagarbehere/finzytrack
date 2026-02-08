import type { WidgetRecipe } from '@/types/recipes'

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
 * Shows total expenses for a selected year, per currency.
 */
export const totalExpensesWidget: WidgetRecipe = {
  id: 'total-expenses',
  title: 'Total Expenses',
  description: 'Sum of all expenses for the selected year',
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
    SELECT currency, SUM(amount) AS amount
    FROM postings
    WHERE account_type = 'Expenses' AND year = :year
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
    icon: '\u2193',
    multiCurrency: true,
  },
}
