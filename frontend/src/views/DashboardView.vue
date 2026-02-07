<template>
  <div class="h-full flex flex-col">
    <!-- Tab bar -->
    <DashboardTabs
      :tabs="tabs"
      :activeTabId="activeTabId"
      @select="handleTabSelect"
      @remove="removeTab"
      @add="showPicker = true"
    />

    <!-- Dashboard content -->
    <div class="flex-1 overflow-auto p-6">
      <RecipeDashboard
        v-if="activeDashboard"
        :dashboard="activeDashboard"
        :key="activeTabId ?? undefined"
        :initialParameters="initialDashboardParams"
        db-type="sqlite"
        @update:parameters="handleDashboardParamsChange"
      />
      <div v-else class="flex flex-col items-center justify-center h-full text-gray-500 dark:text-gray-400">
        <div class="text-lg mb-2">No dashboard selected</div>
        <button
          @click="showPicker = true"
          class="text-blue-600 dark:text-blue-400 hover:underline"
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
const { loadUserRecipes, getAllDashboardIds, getDashboard } = useRecipeLoader()
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

// Add dashboard handler
function handleAddDashboard(id: string) {
  const dashboard = getDashboard(id)
  if (dashboard) {
    addTab(id, dashboard.title)
    handleTabSelect(id)
  }
  showPicker.value = false
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
