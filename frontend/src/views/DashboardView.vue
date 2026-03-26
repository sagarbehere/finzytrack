<template>
  <div class="h-full flex flex-col">
    <!-- Recipe load error banner -->
    <div
      v-if="recipeLoadErrors.length > 0 && !bannerDismissed"
      class="flex-shrink-0 bg-red-50 dark:bg-red-900/20 border-b border-red-200 dark:border-red-800 px-4 py-3"
    >
      <div class="flex items-start justify-between gap-4">
        <div class="flex-1 min-w-0">
          <p class="text-sm font-medium text-red-800 dark:text-red-300">
            {{ recipeLoadErrors.length }} recipe {{ recipeLoadErrors.length === 1 ? 'file' : 'files' }} failed to load
          </p>
          <ul class="mt-1 space-y-1">
            <li
              v-for="err in recipeLoadErrors"
              :key="err.file"
              class="text-xs text-red-700 dark:text-red-400"
            >
              <span class="font-mono">{{ err.file }}</span>
              <span class="text-red-500 dark:text-red-500"> — </span>
              <span v-if="err.kind === 'parse'">invalid JSON</span>
              <span v-else>{{ err.errors.length }} validation {{ err.errors.length === 1 ? 'error' : 'errors' }}</span>
              <ul class="ml-4 mt-0.5 space-y-0.5">
                <li v-for="msg in err.errors" :key="msg" class="text-red-600 dark:text-red-400 font-mono">
                  {{ msg }}
                </li>
              </ul>
            </li>
          </ul>
        </div>
        <button
          @click="bannerDismissed = true"
          class="flex-shrink-0 text-red-400 hover:text-red-600 dark:hover:text-red-300"
          aria-label="Dismiss"
        >
          <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Tab bar -->
    <DashboardTabs
      :tabs="tabs"
      :activeTabId="activeTabId"
      :reloading="isLoading"
      @select="handleTabSelect"
      @remove="removeTab"
      @add="showPicker = true"
      @reload="handleReloadRecipes"
    />

    <!-- Dashboard content -->
    <div class="flex-1 overflow-auto p-6">
      <RecipeDashboard
        v-if="activeDashboard"
        :dashboard="activeDashboard"
        :key="activeTabId ?? undefined"
        :initialParameters="initialDashboardParams"
        @update:parameters="handleDashboardParamsChange"
      />
      <div v-else class="flex flex-col items-center justify-center h-full text-gray-500 dark:text-gray-400">
        <div class="text-lg mb-2">No dashboard selected</div>
        <button
          @click="showPicker = true"
          class="text-indigo-600 dark:text-indigo-400 hover:underline"
        >
          Click here to add one
        </button>
      </div>
    </div>

    <!-- Picker modal -->
    <DashboardPicker
      :isOpen="showPicker"
      :availableDashboards="allDashboards"
      :selectedIds="selectedDashboardIds"
      @close="showPicker = false"
      @select="handleAddDashboard"
      @delete="handleDeleteDashboard"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import DashboardTabs from '@/components/dashboard/DashboardTabs.vue'
import DashboardPicker from '@/components/dashboard/DashboardPicker.vue'
import { useRecipeLoader } from '@/composables/useRecipeLoader'
import { useDashboardTabs } from '@/composables/useDashboardTabs'

const route = useRoute()
const router = useRouter()
const { loadUserRecipes, reloadUserRecipes, getAllDashboardIds, getDashboard, isUserRecipe, getManifestPath, recipeLoadErrors, isLoading } = useRecipeLoader()

const bannerDismissed = ref(false)
const { tabs, activeTabId, addTab, removeTab, setActiveTab, loadTabs, activeDashboard } = useDashboardTabs()

const showPicker = ref(false)

// Dashboard parameters extracted from URL (passed to RecipeDashboard on mount)
// Must be initialized synchronously during setup so RecipeDashboard gets correct
// values on first render — async onMounted runs too late (yields to microtask queue).
const initialDashboardParams = ref<Record<string, string | number>>(extractParamsFromQuery())

// Current dashboard parameters (updated from RecipeDashboard emit)
const currentDashboardParams = ref<Record<string, string | number>>({})

// Flag to prevent circular URL updates
let updatingFromUrl = false

// Computed: all available dashboards with metadata
const allDashboards = computed(() => {
  return getAllDashboardIds().map((id) => {
    const d = getDashboard(id)
    return {
      id,
      title: d?.title || id,
      description: d?.description,
      canDelete: isUserRecipe(id),
      manifestPath: getManifestPath(id),
    }
  })
})

// Computed: IDs of dashboards already in tabs
const selectedDashboardIds = computed(() => tabs.value.map((t) => t.id))

// Extract dashboard parameters from URL query (everything except 'tab')
function extractParamsFromQuery(): Record<string, string | number> {
  const params: Record<string, string | number> = {}
  for (const [key, value] of Object.entries(route.query)) {
    if (key !== 'tab' && value) {
      const str = String(value)
      const num = Number(str)
      params[key] = !isNaN(num) && String(num) === str ? num : str
    }
  }
  return params
}

// Update URL to reflect current dashboard state
function syncStateToUrl() {
  if (updatingFromUrl) return
  if (!activeTabId.value) return

  const query: Record<string, string> = { tab: activeTabId.value }
  for (const [key, value] of Object.entries(currentDashboardParams.value)) {
    query[key] = String(value)
  }
  router.replace({ query })
}

// Handle parameter changes emitted from RecipeDashboard
function handleDashboardParamsChange(params: Record<string, string | number>) {
  currentDashboardParams.value = params
  syncStateToUrl()
}

// Handle tab selection — wraps setActiveTab with URL sync
function handleTabSelect(tabId: string) {
  setActiveTab(tabId)
  // Clear old params; RecipeDashboard will emit new defaults on mount
  currentDashboardParams.value = {}
  syncStateToUrl()
}

// Reload all recipes without a full page refresh
async function handleReloadRecipes() {
  bannerDismissed.value = false
  await reloadUserRecipes()
  // Refresh the active tab's dashboard object in case it changed
  await loadTabs()
}

// Add dashboard handler
function handleAddDashboard(id: string) {
  const dashboard = getDashboard(id)
  if (dashboard) {
    addTab(id, dashboard.title)
    handleTabSelect(id)
  }
  showPicker.value = false
}

// Delete dashboard handler
import type { AvailableDashboard } from '@/components/dashboard/DashboardPicker.vue'

async function handleDeleteDashboard(dashboard: AvailableDashboard) {
  if (!dashboard.manifestPath) return
  try {
    const res = await fetch(`/api/recipes/${dashboard.manifestPath}`, { method: 'DELETE' })
    if (!res.ok) {
      const text = await res.text()
      console.error('[DashboardView] delete failed:', text)
      return
    }
    // Remove the tab if it's open
    removeTab(dashboard.id)
    // Reload recipes to refresh the picker list
    await reloadUserRecipes()
  } catch (err) {
    console.error('[DashboardView] delete error:', err)
  }
}

// Watch route query for back-button navigation
watch(() => route.query, (newQuery, oldQuery) => {
  if (route.name !== 'dashboard') return
  if (JSON.stringify(newQuery) === JSON.stringify(oldQuery)) return

  updatingFromUrl = true

  const tabFromUrl = newQuery.tab as string | undefined
  if (tabFromUrl && tabFromUrl !== activeTabId.value) {
    if (tabs.value.some((t) => t.id === tabFromUrl)) {
      setActiveTab(tabFromUrl)
    }
  }

  initialDashboardParams.value = extractParamsFromQuery()

  updatingFromUrl = false
})

// Initialize on mount
onMounted(async () => {
  bannerDismissed.value = false
  await loadUserRecipes()
  await loadTabs()

  // Restore active tab from URL if present
  const tabFromUrl = route.query.tab as string | undefined
  if (tabFromUrl && tabs.value.some((t) => t.id === tabFromUrl)) {
    setActiveTab(tabFromUrl)
  }

  // Set initial URL if no tab param was present (first visit)
  if (!route.query.tab && activeTabId.value) {
    syncStateToUrl()
  }
})
</script>
