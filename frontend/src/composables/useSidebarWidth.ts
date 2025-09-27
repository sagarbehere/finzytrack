import { ref, computed, watch } from 'vue'

const STORAGE_KEY = 'finzytrack_sidebar_width'
const DEFAULT_WIDTH = 288 // 18rem = 288px (equivalent to w-72)
const MIN_WIDTH = 200 // Minimum width in pixels
const MAX_WIDTH = 500 // Maximum width in pixels

export function useSidebarWidth() {
  // Load persisted width from localStorage
  const loadPersistedWidth = (): number => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        const width = parseInt(saved, 10)
        return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, width))
      }
    } catch {
      // Ignore localStorage errors
    }
    return DEFAULT_WIDTH
  }

  // Reactive sidebar width
  const sidebarWidth = ref<number>(loadPersistedWidth())

  // Computed CSS values
  const sidebarWidthPx = computed(() => `${sidebarWidth.value}px`)
  const sidebarWidthRem = computed(() => `${sidebarWidth.value / 16}rem`)

  // CSS custom properties for dynamic styling
  const sidebarStyles = computed(() => ({
    '--sidebar-width': sidebarWidthPx.value,
  }))

  // Update sidebar width with bounds checking
  const setSidebarWidth = (width: number) => {
    sidebarWidth.value = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, width))
  }

  // Reset to default width
  const resetSidebarWidth = () => {
    sidebarWidth.value = DEFAULT_WIDTH
  }

  // Watch for changes and persist to localStorage
  watch(sidebarWidth, (newWidth) => {
    try {
      localStorage.setItem(STORAGE_KEY, newWidth.toString())
    } catch (error) {
      console.warn('Failed to save sidebar width to localStorage:', error)
    }
  })

  return {
    sidebarWidth,
    sidebarWidthPx,
    sidebarWidthRem,
    sidebarStyles,
    setSidebarWidth,
    resetSidebarWidth,
    MIN_WIDTH,
    MAX_WIDTH,
    DEFAULT_WIDTH
  }
}