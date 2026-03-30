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
import { validateJsonWidgetRecipe, validateJsonDashboardRecipe } from '@/composables/useRecipeValidator'
import { useNotifications } from '@/composables/useNotifications'

/**
 * Composable for loading JSON recipes from the backend API at runtime
 * and merging them with built-in TypeScript recipes.
 *
 * Generator references ({ "$gen": "name", ...args }) in JSON recipes
 * are automatically resolved at load time.
 */

const RECIPES_BASE_PATH = '/api/recipes'

export interface RecipeFileError {
  file: string
  kind: 'parse' | 'schema'
  errors: string[]
}

// Shared state across all component instances
const userWidgets = ref<Record<string, JsonWidgetRecipe>>({})
const userDashboards = ref<Record<string, JsonDashboardRecipe>>({})
// Maps recipe ID → manifest path (e.g. "net-worth" → "dashboards/net-worth.json")
const recipeManifestPaths = ref<Record<string, string>>({})
const isLoaded = ref(false)
const isLoading = ref(false)
const loadError = ref<string | null>(null)
const recipeLoadErrors = ref<RecipeFileError[]>([])

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
 * Load user recipes from the backend API (/api/recipes)
 */
async function loadUserRecipes(): Promise<void> {
  if (isLoaded.value || isLoading.value) return

  isLoading.value = true
  loadError.value = null
  recipeLoadErrors.value = []

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

    const { addNotification } = useNotifications()

    const reportFileError = (file: string, kind: 'parse' | 'schema', messages: string[]) => {
      recipeLoadErrors.value.push({ file, kind, errors: messages })
      const summary = kind === 'parse'
        ? 'File could not be parsed as JSON. See notification panel for details.'
        : `${messages.length} validation ${messages.length === 1 ? 'error' : 'errors'} — see notification panel for details.`
      addNotification({
        type: 'error',
        title: `Recipe error: ${file}`,
        message: summary,
        errorDetails: { file, kind, errors: messages },
        isPersistent: true,
      })
      console.error(`[RecipeLoader] ${kind} error in ${file}:`, messages)
    }

    // Load widgets — returns [recipe, manifestPath] tuples
    const widgetPromises = (manifest.widgets || []).map(async (path: string): Promise<[JsonWidgetRecipe, string] | null> => {
      const fullPath = path.startsWith('/') ? path : `${RECIPES_BASE_PATH}/${path}`
      let raw: unknown
      try {
        raw = await fetchJson<unknown>(fullPath)
      } catch (err) {
        reportFileError(path, 'parse', [err instanceof Error ? err.message : 'Failed to fetch or parse file'])
        return null
      }
      const validationErrors = validateJsonWidgetRecipe(raw)
      if (validationErrors.length > 0) {
        reportFileError(path, 'schema', validationErrors.map((e) => `${e.field}: ${e.message}`))
        return null
      }
      const widget = resolveGenerators(raw as JsonWidgetRecipe)
      console.log(`[RecipeLoader] Loaded widget: ${widget.id}`)
      return [widget, path]
    })

    // Load dashboards — returns [recipe, manifestPath] tuples
    const dashboardPromises = (manifest.dashboards || []).map(async (path: string): Promise<[JsonDashboardRecipe, string] | null> => {
      const fullPath = path.startsWith('/') ? path : `${RECIPES_BASE_PATH}/${path}`
      let raw: unknown
      try {
        raw = await fetchJson<unknown>(fullPath)
      } catch (err) {
        reportFileError(path, 'parse', [err instanceof Error ? err.message : 'Failed to fetch or parse file'])
        return null
      }
      const validationErrors = validateJsonDashboardRecipe(raw)
      if (validationErrors.length > 0) {
        reportFileError(path, 'schema', validationErrors.map((e) => `${e.field}: ${e.message}`))
        return null
      }
      const dashboard = resolveGenerators(raw as JsonDashboardRecipe)
      console.log(`[RecipeLoader] Loaded dashboard: ${dashboard.id}`)
      return [dashboard, path]
    })

    // Wait for all to load
    const widgetResults = (await Promise.all(widgetPromises)).filter(Boolean) as [JsonWidgetRecipe, string][]
    const dashboardResults = (await Promise.all(dashboardPromises)).filter(Boolean) as [JsonDashboardRecipe, string][]

    // Store in state
    userWidgets.value = Object.fromEntries(widgetResults.map(([w]) => [w.id, w]))
    userDashboards.value = Object.fromEntries(dashboardResults.map(([d]) => [d.id, d]))

    // Build ID → manifest path mapping
    const paths: Record<string, string> = {}
    for (const [w, path] of widgetResults) paths[w.id] = path
    for (const [d, path] of dashboardResults) paths[d.id] = path
    recipeManifestPaths.value = paths

    console.log(
      `[RecipeLoader] Loaded ${widgetResults.length} user widgets, ${dashboardResults.length} user dashboards`
    )
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to load user recipes'
    loadError.value = message
    const { addNotification } = useNotifications()
    addNotification({
      type: 'error',
      title: 'Recipe loading failed',
      message,
      isPersistent: true,
    })
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
/**
 * Get the manifest path for a recipe ID (e.g. "dashboards/year-summary.json")
 */
function getManifestPath(id: string): string | undefined {
  return recipeManifestPaths.value[id]
}

async function reloadUserRecipes(): Promise<void> {
  isLoaded.value = false
  userWidgets.value = {}
  userDashboards.value = {}
  recipeManifestPaths.value = {}
  await loadUserRecipes()
}

export function useRecipeLoader() {
  return {
    // State
    isLoaded: readonly(isLoaded),
    isLoading: readonly(isLoading),
    loadError: readonly(loadError),
    recipeLoadErrors: readonly(recipeLoadErrors),
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
    getManifestPath,
  }
}
