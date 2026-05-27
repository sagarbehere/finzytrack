<template>
  <div class="space-y-6">
    <!-- Dashboard header -->
    <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
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
          :style="{ gridArea: widgetLayout.gridArea }"
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
              <span class="text-xs text-gray-500 dark:text-gray-400 mt-1 block">Check that it is defined in the dashboard's widgets array or in the recipe manifest.</span>
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
import { useRecipeLoader } from '@/composables/useRecipeLoader'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import { resolveParameterValues } from '@/recipes/functions'
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

const { getWidget } = useRecipeLoader()

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

/**
 * Get widget by ID.
 * Resolution order:
 * 1. Dashboard's embedded widgets array
 * 2. Global registry (built-in TypeScript + user JSON widgets)
 *
 * This allows JSON dashboards to reference TypeScript widgets by ID.
 */
function getWidgetById(widgetId: string): WidgetRecipe | JsonWidgetRecipe | undefined {
  return props.dashboard.widgets?.find((w) => w.id === widgetId) ?? getWidget(widgetId)
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

// Initialize selections synchronously during setup so child RecipeWidgets
// receive correct dashboardParameters on first render (before their onMounted fires).
initializeParameters()

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
