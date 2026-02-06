import type { DashboardRecipe } from '@/types/recipes'
import { netWorthWidget } from '../widgets/netWorth'
import { monthlyIncomeExpensesWidget } from '../widgets/monthlyIncomeExpenses'
import { topSpendingCategoriesWidget } from '../widgets/topSpendingCategories'
import { expensesPivotTableWidget } from '../widgets/expensesPivotTable'

/**
 * Generate year options for the select parameter
 */
function generateYearOptions() {
  const currentYear = new Date().getFullYear()
  const years = []
  for (let year = currentYear; year >= currentYear - 5; year--) {
    years.push({ value: year, label: String(year) })
  }
  return years
}

/**
 * Financial Overview Dashboard
 *
 * Main dashboard showing key financial metrics:
 * - Net Worth KPI
 * - Monthly Income vs Expenses chart
 * - Top Spending Categories chart
 * - Expenses Pivot Table
 *
 * Layout (12-column grid):
 * - Row 1: Net Worth (cols 1-4), Monthly chart (cols 5-12)
 * - Row 2: Monthly chart continues
 * - Rows 3-4: Top spending categories (full width)
 * - Rows 5-7: Expenses pivot table (full width, taller for table)
 */
export const financialOverviewDashboard: DashboardRecipe = {
  id: 'financial-overview',
  title: 'Financial Overview',
  description: 'Overview of your financial status',
  parameters: [
    {
      name: 'year',
      label: 'Year',
      type: 'select',
      default: new Date().getFullYear(),
      options: generateYearOptions(),
    },
  ],
  layout: {
    columns: 12,
    gap: '1.5rem',
    rowHeight: '180px',
    widgets: [
      {
        widgetId: 'net-worth',
        gridArea: '1 / 1 / 2 / 5', // row 1, cols 1-4
      },
      {
        widgetId: 'monthly-income-expenses',
        gridArea: '1 / 5 / 3 / 13', // rows 1-2, cols 5-12
      },
      {
        widgetId: 'top-spending-categories',
        gridArea: '3 / 1 / 5 / 13', // rows 3-4, cols 1-12
      },
      {
        widgetId: 'expenses-pivot-table',
        gridArea: '5 / 1 / 8 / 13', // rows 5-7, cols 1-12 (full width, taller)
      },
    ],
  },
  widgets: [
    netWorthWidget,
    monthlyIncomeExpensesWidget,
    topSpendingCategoriesWidget,
    expensesPivotTableWidget,
  ],
}
