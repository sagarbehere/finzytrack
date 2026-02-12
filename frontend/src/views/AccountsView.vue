<template>
  <div class="accounts-view">
    <!-- Header -->
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Accounts</h1>
      <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
        Manage your Beancount accounts
      </p>
    </div>

    <!-- Filter Panel -->
    <div class="bg-white dark:bg-gray-800 shadow rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6">
      <AccountsFilterPanel
        :filters="filters"
        :date-filter="dateFilter"
        :active-preset="activePreset"
        @update:filters="filters = $event"
        @update:date-filter="handleDateFilterChange"
        @update:active-preset="activePreset = $event"
        @create="showCreateModal = true"
      />
    </div>

    <!-- Loading State -->
    <div v-if="isLoading" class="flex justify-center items-center py-12">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
    </div>

    <template v-else>
      <!-- Expand/Collapse All Controls (above table) -->
      <div v-if="treeRoots.length > 0" class="mb-2 flex justify-end gap-2">
        <button
          @click="expandAll(filteredTree)"
          class="text-sm text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
        >
          Expand All
        </button>
        <span class="text-gray-300 dark:text-gray-600">|</span>
        <button
          @click="collapseAll"
          class="text-sm text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
        >
          Collapse All
        </button>
      </div>

      <!-- Table -->
      <div class="bg-white dark:bg-gray-800 shadow rounded-lg border border-gray-200 dark:border-gray-700">
        <AccountsTable
          :display-nodes="displayNodes"
          :expanded-ids="expandedIds"
          @toggle="toggleExpand"
          @edit="handleEdit"
          @close="handleClose"
          @reopen="handleReopen"
          @delete="handleDelete"
          @show-balances="handleShowBalances"
          @show-balance-directives="handleShowBalanceDirectives"
          @show-statement="handleShowStatement"
          @view-transactions="handleViewTransactions"
        />
      </div>

      <!-- Expand/Collapse All Controls (below table) -->
      <div v-if="treeRoots.length > 0" class="mt-4 flex justify-end gap-2">
        <button
          @click="expandAll(filteredTree)"
          class="text-sm text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
        >
          Expand All
        </button>
        <span class="text-gray-300 dark:text-gray-600">|</span>
        <button
          @click="collapseAll"
          class="text-sm text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
        >
          Collapse All
        </button>
      </div>
    </template>

    <!-- Create/Edit Modal -->
    <AccountFormModal
      v-model:open="showCreateModal"
      mode="create"
      @submit="handleCreateSubmit"
    />
    <AccountFormModal
      v-model:open="showEditModal"
      mode="edit"
      :account="editingAccount"
      @submit="handleEditSubmit"
    />

    <!-- Close Modal -->
    <AccountCloseModal
      v-model:open="showCloseModal"
      :account="closingAccount"
      @submit="handleCloseSubmit"
    />

    <!-- Delete Modal -->
    <AccountDeleteModal
      v-model:open="showDeleteModal"
      :account="deletingAccount"
      :transaction-count="deletingAccountTxCount"
      @submit="handleDeleteSubmit"
    />

    <!-- Balance Breakdown Modal -->
    <BalanceBreakdownModal
      v-model:open="showBalanceModal"
      :account="viewingBalanceAccount"
    />

    <!-- Balance Directives Modal -->
    <BalanceDirectivesModal
      v-model:open="showBalanceDirectivesModal"
      :account="balanceDirectivesAccount"
    />

    <!-- Account Statement Modal -->
    <AccountStatementModal
      v-model:open="showStatementModal"
      :account="statementAccount"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AccountsFilterPanel from '@/components/accounts/AccountsFilterPanel.vue'
import AccountsTable from '@/components/accounts/AccountsTable.vue'
import AccountFormModal from '@/components/accounts/AccountFormModal.vue'
import AccountCloseModal from '@/components/accounts/AccountCloseModal.vue'
import AccountDeleteModal from '@/components/accounts/AccountDeleteModal.vue'
import BalanceBreakdownModal from '@/components/accounts/BalanceBreakdownModal.vue'
import BalanceDirectivesModal from '@/components/accounts/BalanceDirectivesModal.vue'
import AccountStatementModal from '@/components/accounts/AccountStatementModal.vue'
import { useAccounts, type AccountDateFilter } from '@/composables/useAccounts'
import { useAccountsTree } from '@/composables/useAccountsTree'
import { useToast } from '@/composables/useNotifications'
import type { AccountTreeNode, AccountFilters } from '@/types/accounts'

const router = useRouter()
const route = useRoute()

// Composables
const {
  accountDetails,
  isLoading,
  fetchAccounts,
  fetchAccountsFiltered,
  createAccount,
  updateAccount,
  closeAccount,
  reopenAccount,
  deleteAccount,
  getAccountTransactionCount
} = useAccounts()

const {
  expandedIds,
  buildTree,
  flattenForDisplay,
  filterTree,
  toggleExpand,
  expandAll,
  collapseAll
} = useAccountsTree()

const toast = useToast()

// Date helpers for YTD default
function getFirstDayOfYear(): string {
  return `${new Date().getFullYear()}-01-01`
}

function getToday(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

// Parse URL query params into filter state
function getFiltersFromQuery(): { filters: AccountFilters; dateFilter: AccountDateFilter; preset: string | null } {
  const query = route.query
  return {
    filters: {
      search: query.search ? String(query.search) : '',
      type: query.type ? String(query.type) as AccountFilters['type'] : 'All',
      status: query.status ? String(query.status) as AccountFilters['status'] : 'All'
    },
    dateFilter: {
      // Default to YTD only on fresh load (no preset in URL).
      // If a preset is present but dates aren't, keep null — the preset governs intent.
      startDate: query.startDate ? String(query.startDate) : (query.preset ? null : getFirstDayOfYear()),
      endDate: query.endDate ? String(query.endDate) : (query.preset ? null : getToday()),
    },
    preset: query.preset ? String(query.preset) : null
  }
}

// Update URL to reflect current filter state (without adding to history)
function updateUrlFromFilters() {
  const query: Record<string, string> = {}

  // Only include non-default values
  if (filters.value.search) query.search = filters.value.search
  if (filters.value.type !== 'All') query.type = filters.value.type
  if (filters.value.status !== 'All') query.status = filters.value.status
  if (dateFilter.value.startDate) query.startDate = dateFilter.value.startDate
  if (dateFilter.value.endDate) query.endDate = dateFilter.value.endDate
  if (activePreset.value) query.preset = activePreset.value

  // Use replace to avoid polluting browser history on every filter change
  router.replace({ query })
}

// Filter state (for tree filtering - search, type, status)
const initialState = getFiltersFromQuery()
const filters = ref<AccountFilters>(initialState.filters)

// Date filter state (for balance computation - sent to backend)
const dateFilter = ref<AccountDateFilter>(initialState.dateFilter)

// Active date preset (for highlighting the correct button)
// Default to 'YTD' on fresh load (no query params); otherwise use what the URL says
const activePreset = ref<string | null>(initialState.preset ?? (Object.keys(route.query).length === 0 ? 'YTD' : null))

// Modal state
const showCreateModal = ref(false)
const showEditModal = ref(false)
const showCloseModal = ref(false)
const showDeleteModal = ref(false)
const showBalanceModal = ref(false)
const showBalanceDirectivesModal = ref(false)
const showStatementModal = ref(false)

const editingAccount = ref<AccountTreeNode | null>(null)
const closingAccount = ref<AccountTreeNode | null>(null)
const deletingAccount = ref<AccountTreeNode | null>(null)
const deletingAccountTxCount = ref(0)
const viewingBalanceAccount = ref<AccountTreeNode | null>(null)
const balanceDirectivesAccount = ref<AccountTreeNode | null>(null)
const statementAccount = ref<AccountTreeNode | null>(null)

// Computed: Build tree from account details
const treeRoots = computed(() => buildTree(accountDetails.value))

// Computed: Filter tree based on current filters
const filteredTree = computed(() => filterTree(treeRoots.value, filters.value))

// Computed: Flatten for display based on expansion state
const displayNodes = computed(() => flattenForDisplay(filteredTree.value, expandedIds.value))

// Fetch accounts with current date filter
async function loadAccounts() {
  if (dateFilter.value.startDate || dateFilter.value.endDate) {
    await fetchAccountsFiltered(dateFilter.value)
  } else {
    await fetchAccounts(true)
  }
}

// Derive the earliest account open date from loaded data
function getEarliestAccountDate(): string | null {
  const dates = accountDetails.value
    .map(a => a.open_date)
    .filter(d => d)
    .sort()
  return dates.length > 0 ? dates[0] : null
}

// Handle date filter changes
async function handleDateFilterChange(newDateFilter: AccountDateFilter) {
  dateFilter.value = newDateFilter
  updateUrlFromFilters()
  await loadAccounts()

  // After "All Time" fetch, populate date pickers with actual ledger range
  if (activePreset.value === 'All Time' && accountDetails.value.length > 0) {
    const earliest = getEarliestAccountDate()
    if (earliest) {
      dateFilter.value = { startDate: earliest, endDate: getToday() }
      updateUrlFromFilters()
    }
  }
}

// Fetch accounts on mount
onMounted(() => {
  loadAccounts()
})

// Watch filters and update URL when they change
watch(filters, () => {
  updateUrlFromFilters()
}, { deep: true })

// Watch route query for back/forward navigation
watch(() => route.query, (newQuery, oldQuery) => {
  // Only react if we're on the accounts route and query actually changed
  if (route.name !== 'accounts') return
  if (JSON.stringify(newQuery) === JSON.stringify(oldQuery)) return

  const newState = getFiltersFromQuery()

  // Update filters (this won't trigger loadAccounts, just tree filtering)
  filters.value = newState.filters

  // Update active preset
  activePreset.value = newState.preset

  // Update date filter and reload if it changed
  const dateChanged = newState.dateFilter.startDate !== dateFilter.value.startDate ||
                      newState.dateFilter.endDate !== dateFilter.value.endDate
  if (dateChanged) {
    dateFilter.value = newState.dateFilter
    loadAccounts()
  }
})

// Modal handlers
function handleEdit(node: AccountTreeNode) {
  editingAccount.value = node
  showEditModal.value = true
}

function handleClose(node: AccountTreeNode) {
  closingAccount.value = node
  showCloseModal.value = true
}

function handleReopen(node: AccountTreeNode) {
  performReopen(node)
}

function handleDelete(node: AccountTreeNode) {
  deletingAccount.value = node
  deletingAccountTxCount.value = getAccountTransactionCount(node.fullPath)
  showDeleteModal.value = true
}

function handleShowBalances(node: AccountTreeNode) {
  viewingBalanceAccount.value = node
  showBalanceModal.value = true
}

function handleShowBalanceDirectives(node: AccountTreeNode) {
  balanceDirectivesAccount.value = node
  showBalanceDirectivesModal.value = true
}

function handleShowStatement(node: AccountTreeNode) {
  statementAccount.value = node
  showStatementModal.value = true
}

function handleViewTransactions(node: AccountTreeNode) {
  // Navigate to Transactions tab with filters for this account
  const query: Record<string, string> = {
    accountContains: node.fullPath
  }

  // Balance sheet accounts (Assets, Liabilities, Equity) show cumulative balance
  // from ledger start to end date - so don't include startDate for these
  const isIncomeStatement = node.type === 'Income' || node.type === 'Expenses'

  // Only include start date for income statement accounts
  if (isIncomeStatement && dateFilter.value.startDate) {
    query.dateFrom = dateFilter.value.startDate
  }
  if (dateFilter.value.endDate) {
    query.dateTo = dateFilter.value.endDate
  }

  router.push({ name: 'transactions', query })
}

// Form submission handlers
async function handleCreateSubmit(data: { name: string; openDate: string; currencies: string[]; description: string }) {
  try {
    await createAccount({
      name: data.name,
      open_date: data.openDate,
      currencies: data.currencies,
      description: data.description || undefined
    })
    showCreateModal.value = false
    toast.success('Account Created', `Account "${data.name}" created successfully.`)
    // Reload with current date filter
    await loadAccounts()
  } catch (_error) {
    // Error is already displayed by the composable
  }
}

async function handleEditSubmit(data: { name: string; openDate: string; currencies: string[]; description: string }) {
  if (!editingAccount.value) return

  try {
    await updateAccount(editingAccount.value.fullPath, {
      open_date: data.openDate,
      currencies: data.currencies,
      metadata: data.description ? { description: data.description } : undefined
    })
    showEditModal.value = false
    toast.success('Account Updated', `Account "${editingAccount.value.fullPath}" updated successfully.`)
    // Reload with current date filter
    await loadAccounts()
  } catch (_error) {
    // Error is already displayed by the composable
  }
}

async function handleCloseSubmit(data: { closeDate: string; reason?: string }) {
  if (!closingAccount.value) return

  try {
    await closeAccount(closingAccount.value.fullPath, {
      close_date: data.closeDate,
      reason: data.reason
    })
    showCloseModal.value = false
    toast.success('Account Closed', `Account "${closingAccount.value.fullPath}" closed successfully.`)
    // Reload with current date filter
    await loadAccounts()
  } catch (_error) {
    // Error is already displayed by the composable
  }
}

async function performReopen(node: AccountTreeNode) {
  try {
    await reopenAccount(node.fullPath)
    toast.success('Account Reopened', `Account "${node.fullPath}" reopened successfully.`)
    // Reload with current date filter
    await loadAccounts()
  } catch (_error) {
    // Error is already displayed by the composable
  }
}

async function handleDeleteSubmit(data: { deleteTransactions: boolean }) {
  if (!deletingAccount.value) return

  try {
    const result = await deleteAccount(deletingAccount.value.fullPath, data.deleteTransactions)
    showDeleteModal.value = false

    let message = `Account "${deletingAccount.value.fullPath}" deleted successfully.`
    if (result.transactionsDeleted && result.transactionsDeleted > 0) {
      message += ` ${result.transactionsDeleted} transaction(s) also deleted.`
    }
    toast.success('Account Deleted', message)
    // Reload with current date filter
    await loadAccounts()
  } catch (_error) {
    // Error is already displayed by the composable
    showDeleteModal.value = false
  }
}
</script>
