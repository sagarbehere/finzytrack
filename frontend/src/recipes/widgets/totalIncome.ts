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
 * Total Income KPI Widget
 *
 * Shows total income for a selected year, per currency.
 * Income amounts are negated since they're stored as negative (credits).
 */
export const totalIncomeWidget: WidgetRecipe = {
  id: 'total-income',
  title: 'Total Income',
  description: 'Sum of all income for the selected year',
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
    SELECT currency, SUM(amount) * -1 AS amount
    FROM postings
    WHERE account_type = 'Income' AND year = :year
    GROUP BY currency
    HAVING amount != 0
    ORDER BY currency
  `,

  transform: (rows) => {
    return rows.map((row) => ({
      amount: Number(row.amount) || 0,
      currency: String(row.currency),
    }))
  },

  visualization: {
    type: 'kpi',
    icon: '\u2191',
    multiCurrency: true,
  },
}
