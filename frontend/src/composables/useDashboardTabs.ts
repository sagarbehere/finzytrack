import { ref, computed, readonly } from 'vue'
import type { DashboardRecipe, JsonDashboardRecipe } from '@/types/recipes'
import { useRecipeLoader } from './useRecipeLoader'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'

export interface DashboardTab {
  id: string
  title: string
}

interface StoredTabState {
  tabs: string[]
  activeTabId: string | null
}

const DEFAULT_DASHBOARD_IDS = ['financial-overview', 'year-summary', 'month-summary']

// Shared state across all component instances
const tabs = ref<DashboardTab[]>([])
const activeTabId = ref<string | null>(null)
const isInitialized = ref(false)

function loadFromStorage(): StoredTabState | null {
  return getStorageAdapter().get<StoredTabState>(STORAGE_KEYS.DASHBOARD_TABS)
}

function saveToStorage(): void {
  const state: StoredTabState = {
    tabs: tabs.value.map((t) => t.id),
    activeTabId: activeTabId.value,
  }
  getStorageAdapter().set(STORAGE_KEYS.DASHBOARD_TABS, state)
}

export function useDashboardTabs() {
  const { getDashboard, getAllDashboardIds } = useRecipeLoader()

  /**
   * Initialize tabs from storage or defaults.
   * Should be called after user recipes are loaded.
   */
  async function loadTabs(): Promise<void> {
    if (isInitialized.value) return

    const stored = loadFromStorage()
    const availableIds = new Set(getAllDashboardIds())

    if (stored && stored.tabs.length > 0) {
      // Filter out any tabs that reference non-existent dashboards
      const validTabs: DashboardTab[] = []
      for (const id of stored.tabs) {
        if (availableIds.has(id)) {
          const dashboard = getDashboard(id)
          if (dashboard) {
            validTabs.push({ id, title: dashboard.title })
          }
        }
      }

      if (validTabs.length > 0) {
        tabs.value = validTabs
        // Restore active tab if it's still valid
        const activeValid = validTabs.some((t) => t.id === stored.activeTabId)
        activeTabId.value = activeValid ? stored.activeTabId : validTabs[0].id
      } else {
        // All saved tabs were invalid, use default
        setDefaultTabs()
      }
    } else {
      // No saved state, use default
      setDefaultTabs()
    }

    isInitialized.value = true
  }

  /**
   * Set up default tabs (financial-overview)
   */
  function setDefaultTabs(): void {
    const defaultTabs: DashboardTab[] = []
    for (const id of DEFAULT_DASHBOARD_IDS) {
      const dashboard = getDashboard(id)
      if (dashboard) {
        defaultTabs.push({ id, title: dashboard.title })
      }
    }
    if (defaultTabs.length > 0) {
      tabs.value = defaultTabs
      activeTabId.value = defaultTabs[0].id
    } else {
      tabs.value = []
      activeTabId.value = null
    }
    saveToStorage()
  }

  /**
   * Add a new tab
   */
  function addTab(dashboardId: string, title: string): void {
    // Don't add duplicates
    if (tabs.value.some((t) => t.id === dashboardId)) {
      return
    }
    tabs.value.push({ id: dashboardId, title })
    saveToStorage()
  }

  /**
   * Remove a tab by dashboard ID
   */
  function removeTab(dashboardId: string): void {
    const index = tabs.value.findIndex((t) => t.id === dashboardId)
    if (index === -1) return

    tabs.value.splice(index, 1)

    // If we removed the active tab, switch to another one
    if (activeTabId.value === dashboardId) {
      if (tabs.value.length > 0) {
        // Prefer the tab at the same position, or the last one
        const newIndex = Math.min(index, tabs.value.length - 1)
        activeTabId.value = tabs.value[newIndex].id
      } else {
        activeTabId.value = null
      }
    }

    saveToStorage()
  }

  /**
   * Set the active tab
   */
  function setActiveTab(dashboardId: string): void {
    if (tabs.value.some((t) => t.id === dashboardId)) {
      activeTabId.value = dashboardId
      saveToStorage()
    }
  }

  /**
   * Get the active dashboard object
   */
  const activeDashboard = computed((): DashboardRecipe | JsonDashboardRecipe | null => {
    if (!activeTabId.value) return null
    return getDashboard(activeTabId.value) || null
  })

  return {
    // State (readonly to prevent direct mutation)
    tabs: readonly(tabs),
    activeTabId: readonly(activeTabId),
    isInitialized: readonly(isInitialized),

    // Computed
    activeDashboard,

    // Actions
    loadTabs,
    addTab,
    removeTab,
    setActiveTab,
  }
}
