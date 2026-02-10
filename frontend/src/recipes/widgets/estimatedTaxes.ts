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
 * Estimated Taxes KPI Widget
 *
 * Estimates taxes as 50% of gross income (Income:Gross* accounts), per currency.
 * This is a rough estimate assuming ~50% effective tax rate on gross income.
 */
export const estimatedTaxesWidget: WidgetRecipe = {
  id: 'estimated-taxes',
  title: 'Estimated Taxes',
  description: 'Estimated taxes (50% of gross income)',
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
    SELECT currency, (SUM(amount) / 2.0) * -1 AS amount
    FROM postings
    WHERE account_type = 'Income'
      AND LOWER(account) LIKE 'income:gross%'
      AND year = :year
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
    icon: '%',
    multiCurrency: true,
    helpText: 'Estimated as 50% of Income:Gross',
  },
}
