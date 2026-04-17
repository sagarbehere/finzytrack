import { ref, type Ref } from 'vue'
import { LedgerService } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import type { RecipeParameterOption } from '@/types/recipes'

interface UseAvailableYearsReturn {
  readonly years: Readonly<Ref<RecipeParameterOption[]>>
  readonly hasBeenFetched: Readonly<Ref<boolean>>
  readonly isLoading: Readonly<Ref<boolean>>
  readonly error: Readonly<Ref<string | null>>
  fetchYears: (forceRefresh?: boolean) => Promise<void>
}

// Module-level state (singleton behavior)
const years = ref<RecipeParameterOption[]>([])
const hasBeenFetched = ref<boolean>(false)
const isLoading = ref<boolean>(false)
const error = ref<string | null>(null)

// Concurrent call protection
let activePromise: Promise<void> | null = null

const performFetch = async (): Promise<void> => {
  isLoading.value = true
  error.value = null

  try {
    const response = await LedgerService.executeQuery({
      query: 'SELECT DISTINCT year FROM postings ORDER BY year DESC',
    })
    if (response.data) {
      years.value = response.data.rows.map((row: Record<string, unknown>) => ({
        value: row.year as number,
        label: String(row.year),
      }))
      hasBeenFetched.value = true
    } else {
      throw new Error('No data received from query')
    }
  } catch (err) {
    error.value = 'Failed to fetch available years'
    errorHandler.display(err)
    throw err
  } finally {
    isLoading.value = false
  }
}

const fetchYears = async (forceRefresh = false): Promise<void> => {
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

export function useAvailableYears(): UseAvailableYearsReturn {
  return {
    years: years as Readonly<Ref<RecipeParameterOption[]>>,
    hasBeenFetched: hasBeenFetched as Readonly<Ref<boolean>>,
    isLoading: isLoading as Readonly<Ref<boolean>>,
    error: error as Readonly<Ref<string | null>>,
    fetchYears,
  }
}
