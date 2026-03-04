import { ref, computed, watch } from 'vue'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'

const DEFAULT_WIDTH = 288 // 18rem = 288px (equivalent to w-72)
const MIN_WIDTH = 200 // Minimum width in pixels
const MAX_WIDTH = 500 // Maximum width in pixels

export function useSidebarWidth() {
  // Load persisted width via storage adapter
  const loadPersistedWidth = (): number => {
    const saved = getStorageAdapter().get<number>(STORAGE_KEYS.SIDEBAR_WIDTH)
    if (saved !== null) {
      return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, saved))
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

  // Persist changes via storage adapter
  watch(sidebarWidth, (newWidth) => {
    getStorageAdapter().set(STORAGE_KEYS.SIDEBAR_WIDTH, newWidth)
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
    DEFAULT_WIDTH,
  }
}
