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
 * Savings KPI Widget
 *
 * Calculates estimated savings as:
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
      (
        -- Total Income (negated since income is stored as negative)
        -1 * SUM(CASE WHEN account_type = 'Income' THEN amount ELSE 0 END)
      ) - (
        -- Estimated Taxes (50% of gross income)
        (SUM(CASE WHEN LOWER(account) LIKE 'income:gross%' THEN amount ELSE 0 END) / 2.0) * -1
      ) - (
        -- Total Expenses
        SUM(CASE WHEN account_type = 'Expenses' THEN amount ELSE 0 END)
      ) AS estimated_savings
    FROM postings
    WHERE year = :year
  `,

  transform: (rows) => {
    if (rows.length === 0) return { value: 0 }
    return { value: Number(rows[0].estimated_savings) || 0 }
  },

  visualization: {
    type: 'kpi',
    icon: '$',
    formatValue: formatCurrency,
  },
}
