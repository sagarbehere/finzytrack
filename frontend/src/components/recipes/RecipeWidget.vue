<template>
  <div
    :class="[
      'overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col h-full',
      rendererRef?.hasKPIClickLink() ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors' : '',
    ]"
    @click="rendererRef?.hasKPIClickLink() && rendererRef?.handleKPIClick()"
  >
    <!-- Header -->
    <div
      class="px-3 py-2 sm:px-4 sm:py-3 border-b border-gray-200 dark:border-white/10 flex flex-wrap items-center justify-between gap-1"
    >
      <h3 class="text-sm font-medium text-gray-900 dark:text-white flex items-center gap-1.5">
        {{ recipe.title }}
        <span v-if="getHelpText()" class="group relative">
          <span class="text-gray-400 dark:text-gray-500 text-xs cursor-help select-none">ⓘ</span>
          <span class="invisible group-hover:visible absolute left-0 top-full mt-1 z-10 px-2.5 py-1.5 text-xs font-normal text-white bg-gray-800 dark:bg-gray-900 rounded shadow-lg w-64">
            {{ getHelpText() }}
          </span>
        </span>
      </h3>
      <!-- Widget-level parameters (those not already provided by dashboard) -->
      <RecipeParameters
        v-if="widgetOnlyParameters.length > 0"
        :parameters="widgetOnlyParameters"
        v-model="localParameters"
      />
    </div>

    <!-- Content -->
    <div class="flex-1 p-2 sm:p-4 min-h-0 overflow-hidden">
      <!-- Loading state -->
      <div
        v-if="isLoading"
        class="h-full flex items-center justify-center text-gray-400 dark:text-gray-500"
      >
        <svg
          class="animate-spin h-8 w-8"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            class="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            stroke-width="4"
          ></circle>
          <path
            class="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          ></path>
        </svg>
      </div>

      <!-- Error state -->
      <div
        v-else-if="error"
        class="h-full flex flex-col items-center justify-center gap-2 px-4"
      >
        <p class="text-sm font-medium text-red-600 dark:text-red-400">Query failed</p>
        <pre class="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900 rounded px-3 py-2 max-w-full overflow-auto whitespace-pre-wrap break-all text-left">{{ error }}</pre>
        <button
          @click="executeQuery"
          class="text-xs text-indigo-600 hover:text-indigo-800 dark:text-indigo-400"
        >
          Retry
        </button>
      </div>

      <!-- Visualization -->
      <RecipeWidgetRenderer
        v-else-if="data !== null"
        ref="rendererRef"
        :recipe="recipe"
        :data="data"
        :mergedParameters="mergedParameters"
      />

      <!-- Empty state -->
      <div
        v-else
        class="h-full flex items-center justify-center text-gray-400 dark:text-gray-500"
      >
        <p class="text-sm">No data</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import {
  useRecipeExecutor,
  type AnyWidgetRecipe,
} from '@/composables/useRecipeExecutor'
import RecipeWidgetRenderer from './RecipeWidgetRenderer.vue'
import RecipeParameters from './RecipeParameters.vue'

interface Props {
  recipe: AnyWidgetRecipe
  dashboardParameters?: Record<string, string | number>
}

const props = defineProps<Props>()

const { executeRecipe, getDefaultParameters, isLoading, error } = useRecipeExecutor()

const rendererRef = ref<InstanceType<typeof RecipeWidgetRenderer>>()

// Local parameters (for widget-level params not controlled by dashboard)
const localParameters = ref<Record<string, string | number>>({})

// Widget parameters that are NOT provided by the dashboard (shown in widget header)
const widgetOnlyParameters = computed(() => {
  if (!props.recipe.parameters) return []
  if (!props.dashboardParameters) return props.recipe.parameters
  return props.recipe.parameters.filter((p) => !(p.name in props.dashboardParameters!))
})

// Merged parameters: dashboard params override local params
const mergedParameters = computed(() => ({
  ...getDefaultParameters(props.recipe),
  ...localParameters.value,
  ...(props.dashboardParameters || {}),
}))

// Data from query execution
const data = ref<unknown>(null)

// Execute query
async function executeQuery() {
  try {
    data.value = await executeRecipe(props.recipe, mergedParameters.value, props.recipe.dbType)
  } catch {
    // Error is already set by useRecipeExecutor
  }
}

// Get widget help text
function getHelpText(): string | undefined {
  return props.recipe.helpText
}

// Re-execute when parameters change
watch(mergedParameters, () => executeQuery(), { deep: true })

// Persist widget-level parameter selections when they change
watch(localParameters, (newParams) => {
  if (!props.recipe.id || !props.recipe.parameters?.length) return
  const all = getStorageAdapter().get<Record<string, Record<string, string | number>>>(STORAGE_KEYS.WIDGET_SETTINGS) ?? {}
  all[props.recipe.id] = newParams
  getStorageAdapter().set(STORAGE_KEYS.WIDGET_SETTINGS, all)
}, { deep: true })

// Initial execution — restore saved parameters if available
onMounted(() => {
  const defaults = getDefaultParameters(props.recipe)
  if (props.recipe.id && props.recipe.parameters?.length) {
    const all = getStorageAdapter().get<Record<string, Record<string, string | number>>>(STORAGE_KEYS.WIDGET_SETTINGS) ?? {}
    const saved = all[props.recipe.id]
    localParameters.value = saved ? { ...defaults, ...saved } : defaults
  } else {
    localParameters.value = defaults
  }
  executeQuery()
})
</script>
