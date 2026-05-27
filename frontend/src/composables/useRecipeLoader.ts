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
import { resolveRecipeGenerators } from '@/recipes/functions'
import { validateJsonWidgetRecipe, validateJsonDashboardRecipe } from '@/composables/useRecipeValidator'
import { useNotifications } from '@/composables/useNotifications'

/**
 * Composable for loading JSON recipes from the backend API at runtime
 * and merging them with built-in TypeScript recipes.
 *
 * Generator references ({ "$gen": "name", ...args }) in JSON recipes
 * are automatically resolved at load time, with one exception: no-arg
 * `$gen` references used as `parameters[].default` are preserved as
 * templated sentinels so the UI can offer them as sticky dropdown options
 * (see resolveRecipeGenerators).
 */

const RECIPES_BASE_PATH = '/api/recipes'

export interface RecipeFileError {
  file: string
  kind: 'parse' | 'schema'
  errors: string[]
}

export interface RecipeIdConflict {
  id: string
  kind: 'widget' | 'dashboard' | 'inline-widget'
  files: [string, string]
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
const recipeIdConflicts = ref<RecipeIdConflict[]>([])

/**
 * Detect ID conflicts among user recipe files.
 * Checks: duplicate widget IDs, duplicate dashboard IDs, and standalone widget IDs
 * that clash with inline widget definitions inside dashboards.
 */
function detectConflicts(
  widgetResults: [JsonWidgetRecipe, string][],
  dashboardResults: [JsonDashboardRecipe, string][],
): RecipeIdConflict[] {
  const conflicts: RecipeIdConflict[] = []

  // Widget-Widget: two standalone widget files with same ID
  const widgetById = new Map<string, string>()
  for (const [w, path] of widgetResults) {
    const existing = widgetById.get(w.id)
    if (existing) {
      conflicts.push({ id: w.id, kind: 'widget', files: [existing, path] })
    } else {
      widgetById.set(w.id, path)
    }
  }

  // Dashboard-Dashboard: two dashboard files with same ID
  const dashboardById = new Map<string, string>()
  for (const [d, path] of dashboardResults) {
    const existing = dashboardById.get(d.id)
    if (existing) {
      conflicts.push({ id: d.id, kind: 'dashboard', files: [existing, path] })
    } else {
      dashboardById.set(d.id, path)
    }
  }

  // Inline-Widget: standalone widget ID matches an inline widget in a dashboard
  for (const [dashboard, dashPath] of dashboardResults) {
    if (!dashboard.widgets) continue
    for (const inlineWidget of dashboard.widgets) {
      const standalonePath = widgetById.get(inlineWidget.id)
      if (standalonePath) {
        conflicts.push({ id: inlineWidget.id, kind: 'inline-widget', files: [standalonePath, dashPath] })
      }
    }
  }

  return conflicts
}

/**
 * Check if a given recipe ID would conflict with any already-loaded recipe.
 * Excludes the file at `excludeManifestPath` (so editing a file doesn't conflict with itself).
 */
function checkIdConflict(
  id: string,
  type: 'widget' | 'dashboard',
  excludeManifestPath?: string,
): { conflictingFile: string; kind: RecipeIdConflict['kind'] } | null {
  if (type === 'widget') {
    // Check against other standalone widgets
    if (id in userWidgets.value) {
      const path = recipeManifestPaths.value[id]
      if (path && path !== excludeManifestPath) {
        return { conflictingFile: path, kind: 'widget' }
      }
    }
    // Check against inline widgets in dashboards
    for (const [dId, dashboard] of Object.entries(userDashboards.value)) {
      if (!dashboard.widgets) continue
      for (const inlineWidget of dashboard.widgets) {
        if (inlineWidget.id === id) {
          return { conflictingFile: recipeManifestPaths.value[dId], kind: 'inline-widget' }
        }
      }
    }
  } else {
    // Check against other dashboards
    if (id in userDashboards.value) {
      const path = recipeManifestPaths.value[id]
      if (path && path !== excludeManifestPath) {
        return { conflictingFile: path, kind: 'dashboard' }
      }
    }
  }
  return null
}

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
  recipeIdConflicts.value = []

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
      const widget = resolveRecipeGenerators(raw as JsonWidgetRecipe)
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
      const dashboard = resolveRecipeGenerators(raw as JsonDashboardRecipe)
      console.log(`[RecipeLoader] Loaded dashboard: ${dashboard.id}`)
      return [dashboard, path]
    })

    // Wait for all to load
    const widgetResults = (await Promise.all(widgetPromises)).filter(Boolean) as [JsonWidgetRecipe, string][]
    const dashboardResults = (await Promise.all(dashboardPromises)).filter(Boolean) as [JsonDashboardRecipe, string][]

    // Detect ID conflicts before storing (storing with Object.fromEntries silently drops duplicates)
    recipeIdConflicts.value = detectConflicts(widgetResults, dashboardResults)
    if (recipeIdConflicts.value.length > 0) {
      console.warn('[RecipeLoader] ID conflicts detected:', recipeIdConflicts.value)
    }

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
  recipeIdConflicts.value = []
  await loadUserRecipes()
}

export function useRecipeLoader() {
  return {
    // State
    isLoaded: readonly(isLoaded),
    isLoading: readonly(isLoading),
    loadError: readonly(loadError),
    recipeLoadErrors: readonly(recipeLoadErrors),
    recipeIdConflicts: readonly(recipeIdConflicts),
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
    checkIdConflict,
  }
}
