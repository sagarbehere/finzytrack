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
 * Estimated Taxes KPI Widget
 *
 * Estimates taxes as 50% of gross income (Income:Gross* accounts).
 * This is a rough estimate assuming ~50% effective tax rate on gross income.
 */
export const estimatedTaxesWidget: WidgetRecipe = {
  id: 'estimated-taxes',
  title: 'Estimated Taxes',
  description: 'Estimated taxes (50% of gross income)',

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
    SELECT (SUM(amount) / 2.0) * -1 AS estimated_taxes
    FROM postings
    WHERE account_type = 'Income'
      AND LOWER(account) LIKE 'income:gross%'
      AND year = :year
  `,

  transform: (rows) => {
    if (rows.length === 0) return { value: 0 }
    return { value: Number(rows[0].estimated_taxes) || 0 }
  },

  visualization: {
    type: 'kpi',
    icon: '%',
    formatValue: formatCurrency,
  },
}
