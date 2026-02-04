import { ref, computed, readonly, type Ref } from 'vue'
import {
  AccountsService,
  type AccountDetails,
  type AccountCreateRequest,
  type AccountUpdateRequest,
  type AccountCloseRequest
} from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

// Date filter parameters for fetching accounts
export interface AccountDateFilter {
  startDate: string | null  // YYYY-MM-DD format
  endDate: string | null    // YYYY-MM-DD format
}

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
  fetchAccountsFiltered: (dateFilter: AccountDateFilter) => Promise<void>
  invalidateCache: () => void

  // CRUD operations
  createAccount: (data: AccountCreateRequest) => Promise<void>
  updateAccount: (name: string, data: AccountUpdateRequest) => Promise<void>
  closeAccount: (name: string, data: AccountCloseRequest) => Promise<void>
  reopenAccount: (name: string) => Promise<void>
  deleteAccount: (name: string, deleteTransactions?: boolean) => Promise<{ transactionsDeleted?: number }>

  // Helper to get transaction count for an account
  getAccountTransactionCount: (name: string) => number
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

// Fetch with date filtering (for Accounts page with period selection)
const fetchAccountsFiltered = async (dateFilter: AccountDateFilter): Promise<void> => {
  isLoading.value = true
  error.value = null

  try {
    const response = await AccountsService.listAccounts(
      dateFilter.startDate ?? undefined,
      dateFilter.endDate ?? undefined
    )
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

// CRUD operations

const createAccount = async (data: AccountCreateRequest): Promise<void> => {
  try {
    await AccountsService.createAccount(data)
    invalidateCache()
    await fetchAccounts(true)
  } catch (err) {
    errorHandler.display(err)
    throw err
  }
}

const updateAccount = async (name: string, data: AccountUpdateRequest): Promise<void> => {
  try {
    await AccountsService.updateAccount(name, data)
    invalidateCache()
    await fetchAccounts(true)
  } catch (err) {
    errorHandler.display(err)
    throw err
  }
}

const closeAccount = async (name: string, data: AccountCloseRequest): Promise<void> => {
  try {
    await AccountsService.closeAccount(name, data)
    invalidateCache()
    await fetchAccounts(true)
  } catch (err) {
    errorHandler.display(err)
    throw err
  }
}

const reopenAccount = async (name: string): Promise<void> => {
  try {
    await AccountsService.reopenAccount(name)
    invalidateCache()
    await fetchAccounts(true)
  } catch (err) {
    errorHandler.display(err)
    throw err
  }
}

const deleteAccount = async (name: string, deleteTransactions = true): Promise<{ transactionsDeleted?: number }> => {
  try {
    const response = await AccountsService.deleteAccount(name, deleteTransactions)
    invalidateCache()
    await fetchAccounts(true)
    return { transactionsDeleted: response.data?.transactions_deleted ?? undefined }
  } catch (err) {
    errorHandler.display(err)
    throw err
  }
}

const getAccountTransactionCount = (name: string): number => {
  const account = accountDetails.value.find(a => a.name === name)
  if (!account) return 0
  return account.currencies.reduce((sum, c) => sum + c.transaction_count, 0)
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
    fetchAccountsFiltered,
    invalidateCache,
    createAccount,
    updateAccount,
    closeAccount,
    reopenAccount,
    deleteAccount,
    getAccountTransactionCount
  }
}