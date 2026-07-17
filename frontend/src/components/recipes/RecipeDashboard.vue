<template>
  <div class="space-y-6">
    <!-- Dashboard header. Stacked below md (title/description full width, params
         wrap below); side-by-side at md+ with flex-wrap so params drop below
         instead of overflowing (never a horizontal scrollbar). -->
    <div class="flex flex-col gap-x-6 gap-y-3 md:flex-row md:flex-wrap md:items-start md:justify-between">
      <!-- At md+ the block grows to fill but keeps a basis floor (~20rem) so line
           layout reserves it a readable width: the params wrap *below* once the
           description can't keep that width, rather than the description squishing
           to a sliver (basis, not the description's max-content length, drives the
           break — so a long description no longer forces params below at every width). -->
      <div class="md:min-w-0 md:grow md:basis-80">
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white">
          {{ dashboard.title }}
        </h1>
        <p
          v-if="dashboard.description"
          class="mt-1 text-gray-600 dark:text-gray-400"
        >
          {{ dashboard.description }}
        </p>
      </div>
      <!-- Dashboard-level parameters -->
      <RecipeParameters
        v-if="dashboard.parameters && dashboard.parameters.length > 0"
        :parameters="dashboard.parameters"
        v-model="dashboardSelections"
      />
    </div>

    <!-- Dashboard grid -->
    <div
      class="dashboard-grid"
      :style="{
        gridTemplateColumns: `repeat(${dashboard.layout.columns}, 1fr)`,
        gap: dashboard.layout.gap || '1.5rem',
        gridAutoRows: dashboard.layout.rowHeight || '200px',
      }"
    >
      <template v-for="widgetLayout in dashboard.layout.widgets" :key="widgetLayout.widgetId">
        <RecipeWidget
          v-if="getWidgetById(widgetLayout.widgetId)"
          :recipe="getWidgetById(widgetLayout.widgetId)!"
          :dashboardParameters="resolvedDashboardParameters"
          :dashboardSteps="dashboardStepOutputs"
          :dashboardStepsLoading="widgetDependsOnShared(widgetLayout.widgetId) && sharedStepsLoading"
          :dashboardStepsError="widgetDependsOnShared(widgetLayout.widgetId) ? sharedStepsError : null"
          :style="{ gridArea: widgetLayout.gridArea }"
          @select="onWidgetSelect"
        />
        <!-- Shown when a widgetId in the layout has no matching widget definition -->
        <div
          v-else
          :style="{ gridArea: widgetLayout.gridArea }"
          class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-red-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-red-500/30 flex flex-col h-full"
        >
          <div class="px-4 py-3 border-b border-red-200 dark:border-red-800">
            <h3 class="text-sm font-medium text-red-600 dark:text-red-400">Widget not found</h3>
          </div>
          <div class="flex-1 p-4 flex items-center justify-center">
            <p class="text-sm text-red-500 dark:text-red-400 text-center">
              No widget with id <code class="font-mono bg-red-50 dark:bg-red-900/30 px-1 rounded">{{ widgetLayout.widgetId }}</code> found.<br/>
              <span class="text-xs text-gray-500 dark:text-gray-400 mt-1 block">Check that it is defined in the dashboard's widgets array.</span>
            </p>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type {
  DashboardRecipe,
  JsonDashboardRecipe,
  WidgetRecipe,
  JsonWidgetRecipe,
} from '@/types/recipes'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import { useRecipeExecutor, isStepError, type StepError } from '@/composables/useRecipeExecutor'
import { resolveParameterValues } from '@/recipes/functions'
import { stepRefs } from '@/recipes/templating'
import RecipeWidget from './RecipeWidget.vue'
import RecipeParameters from './RecipeParameters.vue'

interface Props {
  dashboard: DashboardRecipe | JsonDashboardRecipe
  initialParameters?: Record<string, string | number>
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'update:parameters': [params: Record<string, string | number>]
}>()


/**
 * The user's parameter *selections* — what they picked in the dropdowns.
 * Values may be literal scalars OR generator sentinels like "$gen:currentMonth"
 * (which re-evaluate on each load). Selections are persisted to localStorage
 * and emitted upward for URL sync.
 *
 * The resolved scalars (what queries and widgets actually consume) live in
 * `resolvedDashboardParameters` below.
 */
const dashboardSelections = ref<Record<string, string | number>>({})

const resolvedDashboardParameters = computed(() =>
  resolveParameterValues(dashboardSelections.value),
)

// Dashboard shared steps (§3.3): run once per dashboard render, re-run on
// dashboard-param change, and feed every widget via {{dashboard.steps.<id>}}.
const { executeSharedSteps } = useRecipeExecutor()
const dashboardStepOutputs = ref<Record<string, unknown>>({})
const sharedStepsLoading = ref(false)
const sharedStepsError = ref<StepError | null>(null)

const sharedSteps = computed(() => (props.dashboard as JsonDashboardRecipe).steps ?? [])

/** Does a widget reference any {{dashboard.steps.*}} — i.e. depend on a shared
 * step? Only dependents are gated on / affected by shared-step load & failure
 * (§4.9); independent widgets render immediately regardless. */
function widgetDependsOnShared(widgetId: string): boolean {
  if (sharedSteps.value.length === 0) return false
  const widget = getWidgetById(widgetId) as JsonWidgetRecipe | undefined
  return (widget?.steps ?? []).some((s) => stepRefs(s).some((r) => r.scope === 'dashboard.steps'))
}

// Monotonic token so a slow/stale run can never overwrite a newer one's
// result or error (param changes re-run the graph; async completions can race).
let sharedStepsRunId = 0

async function runSharedSteps() {
  const runId = ++sharedStepsRunId
  if (sharedSteps.value.length === 0) {
    dashboardStepOutputs.value = {}
    sharedStepsError.value = null
    sharedStepsLoading.value = false
    return
  }
  sharedStepsLoading.value = true
  sharedStepsError.value = null
  try {
    const outputs = await executeSharedSteps(sharedSteps.value, resolvedDashboardParameters.value)
    if (runId !== sharedStepsRunId) return // a newer run superseded this one
    dashboardStepOutputs.value = outputs
  } catch (e) {
    if (runId !== sharedStepsRunId) return // a newer run superseded this one
    // A failed shared step errors only its *dependent* widgets (§4.9); widgets
    // with no {{dashboard.steps.*}} reference are unaffected.
    dashboardStepOutputs.value = {}
    sharedStepsError.value = isStepError(e)
      ? e
      : { stepId: 'dashboard.steps', kind: 'graph', message: e instanceof Error ? e.message : 'Shared step failed' }
  } finally {
    if (runId === sharedStepsRunId) sharedStepsLoading.value = false
  }
}

// Initialize parameter selections BEFORE registering the shared-steps watch, so
// its `immediate` run sees resolved $gen defaults (not empty params). Otherwise
// the first run fires with no monthStart/currency and 400s. See initializeParameters.
initializeParameters()

watch(resolvedDashboardParameters, () => runSharedSteps(), { immediate: true, deep: true })

/**
 * A widget "select" click (master-detail drill-down): merge the resolved params
 * into the dashboard's selections. Only keys that are actual dashboard
 * parameters are applied, so a widget can't invent unknown params. The existing
 * `dashboardSelections` watch persists + emits, and the resolved-params watch
 * re-runs shared steps + dependent widgets — same path as a dropdown change.
 */
function onWidgetSelect(params: Record<string, string>) {
  const known = new Set((props.dashboard.parameters ?? []).map((p) => p.name))
  const next = { ...dashboardSelections.value }
  let changed = false
  for (const [k, v] of Object.entries(params)) {
    if (known.has(k) && next[k] !== v) {
      next[k] = v
      changed = true
    }
  }
  if (changed) dashboardSelections.value = next
}

/**
 * Get widget by ID from the dashboard's own inline widgets[]. Widgets are
 * inline-only — there is no standalone-widget registry to fall back to
 * (refactored-dashboard-recipes.md §4.10b). A missing widgetId is an authoring
 * error surfaced in place.
 */
function getWidgetById(widgetId: string): WidgetRecipe | JsonWidgetRecipe | undefined {
  return props.dashboard.widgets?.find((w) => w.id === widgetId)
}

/**
 * Initialize parameter selections. Precedence (later overrides earlier):
 *   1. Recipe defaults (may be sentinels for templated $gen defaults).
 *   2. Saved selections from localStorage (per-dashboard, by id).
 *   3. Initial parameters from the URL (treated as explicit literals).
 *
 * URL wins over localStorage because shared/bookmarked URLs are the user's
 * most explicit intent for a particular session.
 */
function initializeParameters() {
  const params: Record<string, string | number> = {}
  if (props.dashboard.parameters) {
    for (const param of props.dashboard.parameters) {
      // After resolveRecipeGenerators, `default` is always a scalar — either
      // a literal or a "$gen:name" sentinel for no-arg templated defaults.
      params[param.name] = param.default as string | number
    }
  }
  if (props.dashboard.id) {
    const all = getStorageAdapter().get<Record<string, Record<string, string | number>>>(
      STORAGE_KEYS.DASHBOARD_SETTINGS,
    ) ?? {}
    const saved = all[props.dashboard.id]
    if (saved) {
      for (const [k, v] of Object.entries(saved)) {
        if (k in params) params[k] = v
      }
    }
  }
  if (props.initialParameters) {
    for (const [key, value] of Object.entries(props.initialParameters)) {
      if (key in params) params[key] = value
    }
  }
  dashboardSelections.value = params
}

// (Selections are initialized above, before the shared-steps watch — see the
// initializeParameters() call there. Doing it synchronously during setup also
// ensures child RecipeWidgets receive correct dashboardParameters on first
// render, before their onMounted fires.)

// Persist selections (sentinels included) and emit upward for URL sync.
watch(dashboardSelections, (newSelections) => {
  if (props.dashboard.id && props.dashboard.parameters?.length) {
    const all = getStorageAdapter().get<Record<string, Record<string, string | number>>>(
      STORAGE_KEYS.DASHBOARD_SETTINGS,
    ) ?? {}
    all[props.dashboard.id] = newSelections
    getStorageAdapter().set(STORAGE_KEYS.DASHBOARD_SETTINGS, all)
  }
  emit('update:parameters', { ...newSelections })
}, { deep: true })
</script>

<style scoped>
.dashboard-grid {
  display: grid;
}

/* On mobile, collapse the multi-column grid to single column */
@media (max-width: 767px) {
  .dashboard-grid {
    grid-template-columns: 1fr !important;
    grid-auto-rows: auto !important;
  }

  .dashboard-grid > * {
    grid-area: auto !important;
    min-height: 200px;
  }
}
</style>
