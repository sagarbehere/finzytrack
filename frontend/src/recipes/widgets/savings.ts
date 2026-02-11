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
 * Savings KPI Widget
 *
 * Calculates estimated savings per currency as:
 * Total Income - Estimated Taxes - Total Expenses
 *
 * Where:
 * - Total Income = -1 * SUM(Income amounts)
 * - Estimated Taxes = (Gross Income / 2)
 * - Total Expenses = SUM(Expense amounts)
 */
export const savingsWidget: WidgetRecipe = {
  id: 'savings',
  title: 'Savings',
  description: 'Estimated savings (Income - Taxes - Expenses)',
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
      currency,
      (
        -- Total Income (negated since income is stored as negative)
        -1 * SUM(CASE WHEN account_type = 'Income' THEN amount ELSE 0 END)
      ) - (
        -- Estimated Taxes (50% of gross income)
        (SUM(CASE WHEN LOWER(account) LIKE 'income:gross%' THEN amount ELSE 0 END) / 2.0) * -1
      ) - (
        -- Total Expenses
        SUM(CASE WHEN account_type = 'Expenses' THEN amount ELSE 0 END)
      ) AS amount
    FROM postings
    WHERE year = :year
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
    icon: '$',
    multiCurrency: true,
  },
}
