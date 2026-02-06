import { ref, readonly } from 'vue'
import type {
  RecipeManifest,
  JsonWidgetRecipe,
  JsonDashboardRecipe,
  WidgetRecipe,
  DashboardRecipe,
  HybridRecipeRegistry,
} from '@/types/recipes'
import { recipeRegistry as builtInRegistry } from '@/recipes'
import { resolveGenerators } from '@/recipes/functions'

/**
 * Composable for loading user recipes from public/recipes/ at runtime
 * and merging them with built-in TypeScript recipes.
 *
 * Generator references ({ "$gen": "name", ...args }) in JSON recipes
 * are automatically resolved at load time.
 */

const RECIPES_BASE_PATH = '/recipes'

// Shared state across all component instances
const userWidgets = ref<Record<string, JsonWidgetRecipe>>({})
const userDashboards = ref<Record<string, JsonDashboardRecipe>>({})
const isLoaded = ref(false)
const isLoading = ref(false)
const loadError = ref<string | null>(null)

/**
 * Fetch and parse a JSON file
 */
async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.statusText}`)
  }
  return response.json()
}

/**
 * Load user recipes from the public/recipes/ directory
 */
async function loadUserRecipes(): Promise<void> {
  if (isLoaded.value || isLoading.value) return

  isLoading.value = true
  loadError.value = null

  try {
    // Try to fetch manifest
    let manifest: RecipeManifest
    try {
      manifest = await fetchJson<RecipeManifest>(`${RECIPES_BASE_PATH}/manifest.json`)
    } catch {
      // No manifest file - that's okay, just means no user recipes
      console.log('[RecipeLoader] No user recipes manifest found')
      isLoaded.value = true
      isLoading.value = false
      return
    }

    // Load widgets
    const widgetPromises = (manifest.widgets || []).map(async (path) => {
      try {
        const fullPath = path.startsWith('/') ? path : `${RECIPES_BASE_PATH}/${path}`
        const rawWidget = await fetchJson<JsonWidgetRecipe>(fullPath)
        // Resolve any generator references in the widget
        const widget = resolveGenerators(rawWidget)
        console.log(`[RecipeLoader] Loaded widget: ${widget.id}`)
        return widget
      } catch (err) {
        console.warn(`[RecipeLoader] Failed to load widget ${path}:`, err)
        return null
      }
    })

    // Load dashboards
    const dashboardPromises = (manifest.dashboards || []).map(async (path) => {
      try {
        const fullPath = path.startsWith('/') ? path : `${RECIPES_BASE_PATH}/${path}`
        const rawDashboard = await fetchJson<JsonDashboardRecipe>(fullPath)
        // Resolve any generator references in the dashboard
        const dashboard = resolveGenerators(rawDashboard)
        console.log(`[RecipeLoader] Loaded dashboard: ${dashboard.id}`)
        return dashboard
      } catch (err) {
        console.warn(`[RecipeLoader] Failed to load dashboard ${path}:`, err)
        return null
      }
    })

    // Wait for all to load
    const widgets = (await Promise.all(widgetPromises)).filter(Boolean) as JsonWidgetRecipe[]
    const dashboards = (await Promise.all(dashboardPromises)).filter(Boolean) as JsonDashboardRecipe[]

    // Store in state
    userWidgets.value = Object.fromEntries(widgets.map((w) => [w.id, w]))
    userDashboards.value = Object.fromEntries(dashboards.map((d) => [d.id, d]))

    console.log(
      `[RecipeLoader] Loaded ${widgets.length} user widgets, ${dashboards.length} user dashboards`
    )
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to load user recipes'
    loadError.value = message
    console.error('[RecipeLoader] Error:', err)
  } finally {
    isLoaded.value = true
    isLoading.value = false
  }
}

/**
 * Get merged registry of built-in and user recipes
 * User recipes with same ID override built-in ones
 */
function getMergedRegistry(): HybridRecipeRegistry {
  return {
    widgets: {
      ...builtInRegistry.widgets,
      ...userWidgets.value,
    },
    dashboards: {
      ...builtInRegistry.dashboards,
      ...userDashboards.value,
    },
  }
}

/**
 * Get a widget by ID (checks user recipes first, then built-in)
 */
function getWidget(id: string): WidgetRecipe | JsonWidgetRecipe | undefined {
  return userWidgets.value[id] || builtInRegistry.widgets[id]
}

/**
 * Get a dashboard by ID (checks user recipes first, then built-in)
 */
function getDashboard(id: string): DashboardRecipe | JsonDashboardRecipe | undefined {
  return userDashboards.value[id] || builtInRegistry.dashboards[id]
}

/**
 * Get list of all available widget IDs
 */
function getAllWidgetIds(): string[] {
  return [...new Set([...Object.keys(builtInRegistry.widgets), ...Object.keys(userWidgets.value)])]
}

/**
 * Get list of all available dashboard IDs
 */
function getAllDashboardIds(): string[] {
  return [
    ...new Set([...Object.keys(builtInRegistry.dashboards), ...Object.keys(userDashboards.value)]),
  ]
}

/**
 * Check if a recipe is a user recipe (JSON) vs built-in (TypeScript)
 */
function isUserRecipe(id: string): boolean {
  return id in userWidgets.value || id in userDashboards.value
}

/**
 * Reload user recipes (useful if files changed)
 */
async function reloadUserRecipes(): Promise<void> {
  isLoaded.value = false
  userWidgets.value = {}
  userDashboards.value = {}
  await loadUserRecipes()
}

export function useRecipeLoader() {
  return {
    // State
    isLoaded: readonly(isLoaded),
    isLoading: readonly(isLoading),
    loadError: readonly(loadError),
    userWidgets: readonly(userWidgets),
    userDashboards: readonly(userDashboards),

    // Methods
    loadUserRecipes,
    reloadUserRecipes,
    getMergedRegistry,
    getWidget,
    getDashboard,
    getAllWidgetIds,
    getAllDashboardIds,
    isUserRecipe,
  }
}
