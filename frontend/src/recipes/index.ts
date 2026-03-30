/**
 * Recipe Registry
 *
 * All built-in recipes have been migrated to JSON files served by the backend API.
 * This registry is kept for future TypeScript recipes that require functions
 * (custom transforms, click handlers, etc.) that cannot be expressed in JSON.
 */

import type { RecipeRegistry } from '@/types/recipes'

export const recipeRegistry: RecipeRegistry = {
  widgets: {},
  dashboards: {},
}
