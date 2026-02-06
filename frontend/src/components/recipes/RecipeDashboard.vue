<template>
  <div class="space-y-6">
    <!-- Dashboard header -->
    <div class="flex items-start justify-between">
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
        v-model="dashboardParameters"
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
      <RecipeWidget
        v-for="widgetLayout in dashboard.layout.widgets"
        :key="widgetLayout.widgetId"
        :recipe="getWidgetById(widgetLayout.widgetId)"
        :dashboardParameters="dashboardParameters"
        :dbType="dbType"
        :style="{ gridArea: widgetLayout.gridArea }"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type {
  DashboardRecipe,
  JsonDashboardRecipe,
  WidgetRecipe,
  JsonWidgetRecipe,
} from '@/types/recipes'
import { useRecipeLoader } from '@/composables/useRecipeLoader'
import RecipeWidget from './RecipeWidget.vue'
import RecipeParameters from './RecipeParameters.vue'

interface Props {
  dashboard: DashboardRecipe | JsonDashboardRecipe
  dbType?: 'duckdb' | 'sqlite'
}

const props = withDefaults(defineProps<Props>(), {
  dbType: 'sqlite',
})

const { getWidget } = useRecipeLoader()

// Dashboard-level parameters
const dashboardParameters = ref<Record<string, string | number>>({})

/**
 * Get widget by ID.
 * Resolution order:
 * 1. Dashboard's embedded widgets array
 * 2. Global registry (built-in TypeScript + user JSON widgets)
 *
 * This allows JSON dashboards to reference TypeScript widgets by ID.
 */
function getWidgetById(widgetId: string): WidgetRecipe | JsonWidgetRecipe {
  // First, check dashboard's embedded widgets
  const embeddedWidget = props.dashboard.widgets?.find((w) => w.id === widgetId)
  if (embeddedWidget) {
    return embeddedWidget
  }

  // Fall back to global registry (built-in + user recipes)
  const registryWidget = getWidget(widgetId)
  if (registryWidget) {
    return registryWidget
  }

  throw new Error(
    `Widget not found: "${widgetId}". Check that it exists in the dashboard's widgets array or in the recipe registry.`
  )
}

// Initialize dashboard parameters with defaults
function initializeParameters() {
  const params: Record<string, string | number> = {}
  if (props.dashboard.parameters) {
    for (const param of props.dashboard.parameters) {
      params[param.name] = param.default
    }
  }
  dashboardParameters.value = params
}

onMounted(() => {
  initializeParameters()
})
</script>

<style scoped>
.dashboard-grid {
  display: grid;
}
</style>
