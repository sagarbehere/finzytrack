import { ref, computed, readonly } from 'vue'
import type { DashboardRecipe, JsonDashboardRecipe } from '@/types/recipes'
import { useRecipeLoader } from './useRecipeLoader'

export interface DashboardTab {
  id: string
  title: string
}

interface StoredTabState {
  tabs: string[]
  activeTabId: string | null
}

const STORAGE_KEY = 'finzytrack:dashboard-tabs'
const DEFAULT_DASHBOARD_ID = 'financial-overview'

// Shared state across all component instances
const tabs = ref<DashboardTab[]>([])
const activeTabId = ref<string | null>(null)
const isInitialized = ref(false)

/**
 * Load saved state from localStorage
 */
function loadFromStorage(): StoredTabState | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      return JSON.parse(stored)
    }
  } catch (err) {
    console.warn('[DashboardTabs] Failed to load from localStorage:', err)
  }
  return null
}

/**
 * Save current state to localStorage
 */
function saveToStorage(): void {
  try {
    const state: StoredTabState = {
      tabs: tabs.value.map((t) => t.id),
      activeTabId: activeTabId.value,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch (err) {
    console.warn('[DashboardTabs] Failed to save to localStorage:', err)
  }
}

export function useDashboardTabs() {
  const { getDashboard, getAllDashboardIds } = useRecipeLoader()

  /**
   * Initialize tabs from localStorage or defaults
   * Should be called after user recipes are loaded
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
    const defaultDashboard = getDashboard(DEFAULT_DASHBOARD_ID)
    if (defaultDashboard) {
      tabs.value = [{ id: DEFAULT_DASHBOARD_ID, title: defaultDashboard.title }]
      activeTabId.value = DEFAULT_DASHBOARD_ID
    } else {
      // No default dashboard available
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
