import { ref, computed, readonly, type Ref } from 'vue'
import { AccountsService, type AccountDetails } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

// TypeScript interface for the composable return value
interface UseAccountsReturn {
  // Full account data for CRUD operations and hierarchical displays
  readonly accountDetails: Readonly<Ref<AccountDetails[]>>
  
  // Account names only for dropdowns and simple selectors
  readonly accountNames: Readonly<Ref<string[]>>
  
  // Cache and loading state
  readonly hasBeenFetched: Readonly<Ref<boolean>>
  readonly isLoading: Readonly<Ref<boolean>>
  readonly error: Readonly<Ref<string | null>>

  // Actions
  fetchAccounts: (forceRefresh?: boolean) => Promise<void>
  invalidateCache: () => void
}

// Module-level state (singleton behavior)
const accountDetails = ref<AccountDetails[]>([])
const hasBeenFetched = ref<boolean>(false)
const isLoading = ref<boolean>(false)
const error = ref<string | null>(null)

// Derived state for account names
const accountNames = computed(() => 
  accountDetails.value.map(account => account.name)
)

// Concurrent call protection
let activePromise: Promise<void> | null = null

// Core fetch implementation
const performFetch = async (): Promise<void> => {
  isLoading.value = true
  error.value = null

  try {
    const response = await AccountsService.listAccounts()
    if (response.data) {
      accountDetails.value = response.data.accounts
      hasBeenFetched.value = true
    } else {
      throw new Error('No data received from accounts API')
    }
  } catch (err) {
    error.value = 'Failed to fetch accounts'
    errorHandler.display(err)
    throw err
  } finally {
    isLoading.value = false
  }
}

// Public API methods
const fetchAccounts = async (forceRefresh = false): Promise<void> => {
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
  // accountDetails.value will be updated on next fetchAccounts() call
}

// Export composable function with proper TypeScript typing
export function useAccounts(): UseAccountsReturn {
  return {
    accountDetails: accountDetails as Readonly<Ref<AccountDetails[]>>,
    accountNames: accountNames as Readonly<Ref<string[]>>,
    hasBeenFetched: hasBeenFetched as Readonly<Ref<boolean>>,
    isLoading: isLoading as Readonly<Ref<boolean>>,
    error: error as Readonly<Ref<string | null>>,
    fetchAccounts,
    invalidateCache
  }
}