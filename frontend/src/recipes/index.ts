/**
 * Recipe Registry
 *
 * Central export point for all recipes (widgets and dashboards).
 * Import from here to access any recipe by ID.
 */

import type { RecipeRegistry } from '@/types/recipes'

// Widgets
import { netWorthWidget } from './widgets/netWorth'
import { topSpendingCategoriesWidget } from './widgets/topSpendingCategories'

/**
 * Recipe registry for lookup by ID
 */
export const recipeRegistry: RecipeRegistry = {
  widgets: {
    [netWorthWidget.id]: netWorthWidget,
    [topSpendingCategoriesWidget.id]: topSpendingCategoriesWidget,
  },
  dashboards: {},
}

// Re-export individual recipes for direct imports
export { netWorthWidget } from './widgets/netWorth'
export { topSpendingCategoriesWidget } from './widgets/topSpendingCategories'
