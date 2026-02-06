/**
 * Recipe Registry
 *
 * Central export point for all recipes (widgets and dashboards).
 * Import from here to access any recipe by ID.
 */

import type { RecipeRegistry } from '@/types/recipes'

// Widgets
import { netWorthWidget } from './widgets/netWorth'
import { monthlyIncomeExpensesWidget } from './widgets/monthlyIncomeExpenses'
import { topSpendingCategoriesWidget } from './widgets/topSpendingCategories'
import { totalIncomeWidget } from './widgets/totalIncome'
import { totalExpensesWidget } from './widgets/totalExpenses'
import { estimatedTaxesWidget } from './widgets/estimatedTaxes'
import { savingsWidget } from './widgets/savings'
import { expensesPivotTableWidget } from './widgets/expensesPivotTable'

// Dashboards
import { financialOverviewDashboard } from './dashboards/financialOverview'

/**
 * Recipe registry for lookup by ID
 */
export const recipeRegistry: RecipeRegistry = {
  widgets: {
    [netWorthWidget.id]: netWorthWidget,
    [monthlyIncomeExpensesWidget.id]: monthlyIncomeExpensesWidget,
    [topSpendingCategoriesWidget.id]: topSpendingCategoriesWidget,
    [totalIncomeWidget.id]: totalIncomeWidget,
    [totalExpensesWidget.id]: totalExpensesWidget,
    [estimatedTaxesWidget.id]: estimatedTaxesWidget,
    [savingsWidget.id]: savingsWidget,
    [expensesPivotTableWidget.id]: expensesPivotTableWidget,
  },
  dashboards: {
    [financialOverviewDashboard.id]: financialOverviewDashboard,
  },
}

// Re-export individual recipes for direct imports
export { netWorthWidget } from './widgets/netWorth'
export { monthlyIncomeExpensesWidget } from './widgets/monthlyIncomeExpenses'
export { topSpendingCategoriesWidget } from './widgets/topSpendingCategories'
export { totalIncomeWidget } from './widgets/totalIncome'
export { totalExpensesWidget } from './widgets/totalExpenses'
export { estimatedTaxesWidget } from './widgets/estimatedTaxes'
export { savingsWidget } from './widgets/savings'
export { expensesPivotTableWidget } from './widgets/expensesPivotTable'
export { financialOverviewDashboard } from './dashboards/financialOverview'
