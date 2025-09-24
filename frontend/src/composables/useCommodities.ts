import { ref, computed, type Ref } from 'vue'
import { CommoditiesService, type CommodityDetails } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

// TypeScript interface for the composable return value
interface UseCommoditiesReturn {
  // Full commodity data for CRUD operations and detailed displays
  readonly commodityDetails: Readonly<Ref<CommodityDetails[]>>
  
  // Commodity codes only for dropdowns and simple selectors
  readonly commodityCodes: Readonly<Ref<string[]>>
  
  // Cache and loading state
  readonly hasBeenFetched: Readonly<Ref<boolean>>
  readonly isLoading: Readonly<Ref<boolean>>
  readonly error: Readonly<Ref<string | null>>

  // Actions
  fetchCommodities: (forceRefresh?: boolean) => Promise<void>
  invalidateCache: () => void
}

// Module-level state (singleton behavior)
const commodityDetails = ref<CommodityDetails[]>([])
const hasBeenFetched = ref<boolean>(false)
const isLoading = ref<boolean>(false)
const error = ref<string | null>(null)

// Derived state for commodity codes
const commodityCodes = computed(() => 
  commodityDetails.value.map(commodity => commodity.code)
)

// Concurrent call protection
let activePromise: Promise<void> | null = null

// Core fetch implementation
const performFetch = async (): Promise<void> => {
  isLoading.value = true
  error.value = null

  try {
    const response = await CommoditiesService.listCommodities()
    if (response.data) {
      commodityDetails.value = response.data.commodities
      hasBeenFetched.value = true
    } else {
      throw new Error('No data received from commodities API')
    }
  } catch (err) {
    error.value = 'Failed to fetch commodities'
    errorHandler.display(err)
    throw err
  } finally {
    isLoading.value = false
  }
}

// Public API methods
const fetchCommodities = async (forceRefresh = false): Promise<void> => {
  if (!forceRefresh && hasBeenFetched.value) {
    return Promise.resolve()
  }

  if (activePromise) {
    return activePromise
  }

  activePromise = performFetch()
  
  try {
    await activePromise
  } finally {
    activePromise = null
  }
}

const invalidateCache = (): void => {
  hasBeenFetched.value = false
  error.value = null
  // Keep existing data until refresh for better UX
  // commodityDetails.value will be updated on next fetchCommodities() call
}

// Export composable function with proper TypeScript typing
export function useCommodities(): UseCommoditiesReturn {
  return {
    commodityDetails: commodityDetails as Readonly<Ref<CommodityDetails[]>>,
    commodityCodes: commodityCodes as Readonly<Ref<string[]>>,
    hasBeenFetched: hasBeenFetched as Readonly<Ref<boolean>>,
    isLoading: isLoading as Readonly<Ref<boolean>>,
    error: error as Readonly<Ref<string | null>>,
    fetchCommodities,
    invalidateCache
  }
}