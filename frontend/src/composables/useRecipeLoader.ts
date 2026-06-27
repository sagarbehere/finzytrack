import { ref, readonly } from 'vue'
import type {
  RecipeManifest,
  JsonDashboardRecipe,
  DashboardRecipe,
  HybridRecipeRegistry,
} from '@/types/recipes'
import { recipeRegistry as builtInRegistry } from '@/recipes'
import { resolveRecipeGenerators } from '@/recipes/functions'
import { validateJsonDashboardRecipe } from '@/composables/useRecipeValidator'
import { useNotifications } from '@/composables/useNotifications'

/**
 * Composable for loading JSON dashboard recipes from the backend API at runtime
 * and merging them with any built-in TypeScript dashboards.
 *
 * The dashboard is the only recipe type — widgets live inline inside dashboards
 * (refactored-dashboard-recipes.md §3.0/§4.10b). There is no widgets/ manifest
 * entry, no standalone-widget registry, and no cross-type ID namespace.
 *
 * Generator references ({ "$gen": "name", ...args }) are resolved at load time,
 * with one exception: no-arg `$gen` used as `parameters[].default` is preserved
 * as a templated sentinel so the UI can offer it as a sticky dropdown option
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
  kind: 'dashboard'
  files: [string, string]
}

// Shared state across all component instances
const userDashboards = ref<Record<string, JsonDashboardRecipe>>({})
// Maps recipe ID → manifest path (e.g. "net-worth" → "dashboards/net-worth.json")
const recipeManifestPaths = ref<Record<string, string>>({})
const isLoaded = ref(false)
const isLoading = ref(false)
const loadError = ref<string | null>(null)
const recipeLoadErrors = ref<RecipeFileError[]>([])
const recipeIdConflicts = ref<RecipeIdConflict[]>([])

/**
 * Detect duplicate dashboard IDs among user recipe files (storing with
 * Object.fromEntries would silently drop duplicates).
 */
function detectConflicts(
  dashboardResults: [JsonDashboardRecipe, string][],
): RecipeIdConflict[] {
  const conflicts: RecipeIdConflict[] = []
  const dashboardById = new Map<string, string>()
  for (const [d, path] of dashboardResults) {
    const existing = dashboardById.get(d.id)
    if (existing) {
      conflicts.push({ id: d.id, kind: 'dashboard', files: [existing, path] })
    } else {
      dashboardById.set(d.id, path)
    }
  }
  return conflicts
}

/**
 * Check if a dashboard ID would conflict with an already-loaded dashboard.
 * Excludes the file at `excludeManifestPath` (so editing a file doesn't conflict
 * with itself).
 */
function checkIdConflict(
  id: string,
  excludeManifestPath?: string,
): { conflictingFile: string; kind: RecipeIdConflict['kind'] } | null {
  if (id in userDashboards.value) {
    const path = recipeManifestPaths.value[id]
    if (path && path !== excludeManifestPath) {
      return { conflictingFile: path, kind: 'dashboard' }
    }
  }
  return null
}

/**
 * Fetch and parse a recipe JSON file.
 *
 * Sanctioned `fetch()` exception per frontend/CLAUDE.md: the recipe endpoints
 * (`/api/recipes/manifest.json`, `/api/recipes/{path}`) intentionally return
 * raw JSON via `JSONResponse(content=...)` — not the `ApiResponse[T]` envelope —
 * so the AI tools (`write_recipe`, etc.) and this loader read the same shape
 * for `$gen` template processing. The generated client wraps everything in
 * `ApiResponse[T]` and can't represent the unwrapped envelope.
 */
async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.statusText}`)
  }
  return response.json()
}

/**
 * Load user dashboards from the backend API (/api/recipes)
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

    const dashboardResults = (await Promise.all(dashboardPromises)).filter(Boolean) as [JsonDashboardRecipe, string][]

    // Detect ID conflicts before storing
    recipeIdConflicts.value = detectConflicts(dashboardResults)
    if (recipeIdConflicts.value.length > 0) {
      console.warn('[RecipeLoader] ID conflicts detected:', recipeIdConflicts.value)
    }

    // Store in state
    userDashboards.value = Object.fromEntries(dashboardResults.map(([d]) => [d.id, d]))

    // Build ID → manifest path mapping
    const paths: Record<string, string> = {}
    for (const [d, path] of dashboardResults) paths[d.id] = path
    recipeManifestPaths.value = paths

    console.log(`[RecipeLoader] Loaded ${dashboardResults.length} user dashboards`)
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
 * Get merged registry of built-in and user dashboards.
 * User dashboards with the same ID override built-in ones.
 */
function getMergedRegistry(): HybridRecipeRegistry {
  return {
    dashboards: {
      ...builtInRegistry.dashboards,
      ...userDashboards.value,
    },
  }
}

/**
 * Get a dashboard by ID (checks user recipes first, then built-in)
 */
function getDashboard(id: string): DashboardRecipe | JsonDashboardRecipe | undefined {
  return userDashboards.value[id] || builtInRegistry.dashboards[id]
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
 * Check if a recipe ID is a user recipe (JSON) vs built-in (TypeScript)
 */
function isUserRecipe(id: string): boolean {
  return id in userDashboards.value
}

/**
 * Get the manifest path for a recipe ID (e.g. "dashboards/year-summary.json")
 */
function getManifestPath(id: string): string | undefined {
  return recipeManifestPaths.value[id]
}

async function reloadUserRecipes(): Promise<void> {
  isLoaded.value = false
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
    userDashboards: readonly(userDashboards),

    // Methods
    loadUserRecipes,
    reloadUserRecipes,
    getMergedRegistry,
    getDashboard,
    getAllDashboardIds,
    isUserRecipe,
    getManifestPath,
    checkIdConflict,
  }
}
