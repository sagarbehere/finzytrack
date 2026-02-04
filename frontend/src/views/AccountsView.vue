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
        @update:filters="filters = $event"
        @update:date-filter="handleDateFilterChange"
        @create="showCreateModal = true"
      />
    </div>

    <!-- Loading State -->
    <div v-if="isLoading" class="flex justify-center items-center py-12">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
    </div>

    <!-- Table -->
    <div v-else class="bg-white dark:bg-gray-800 shadow rounded-lg border border-gray-200 dark:border-gray-700">
      <AccountsTable
        :display-nodes="displayNodes"
        :expanded-ids="expandedIds"
        @toggle="toggleExpand"
        @edit="handleEdit"
        @close="handleClose"
        @reopen="handleReopen"
        @delete="handleDelete"
        @show-balances="handleShowBalances"
        @view-transactions="handleViewTransactions"
      />
    </div>

    <!-- Expand/Collapse All Controls -->
    <div v-if="!isLoading && treeRoots.length > 0" class="mt-4 flex justify-end gap-2">
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
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AccountsFilterPanel from '@/components/accounts/AccountsFilterPanel.vue'
import AccountsTable from '@/components/accounts/AccountsTable.vue'
import AccountFormModal from '@/components/accounts/AccountFormModal.vue'
import AccountCloseModal from '@/components/accounts/AccountCloseModal.vue'
import AccountDeleteModal from '@/components/accounts/AccountDeleteModal.vue'
import BalanceBreakdownModal from '@/components/accounts/BalanceBreakdownModal.vue'
import { useAccounts, type AccountDateFilter } from '@/composables/useAccounts'
import { useAccountsTree } from '@/composables/useAccountsTree'
import { useToast } from '@/composables/useNotifications'
import type { AccountTreeNode, AccountFilters } from '@/types/accounts'

const router = useRouter()

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

// Filter state (for tree filtering - search, type, status)
const filters = ref<AccountFilters>({
  search: '',
  type: 'All',
  status: 'All'
})

// Date filter state (for balance computation - sent to backend)
const dateFilter = ref<AccountDateFilter>({
  startDate: null,
  endDate: null
})

// Modal state
const showCreateModal = ref(false)
const showEditModal = ref(false)
const showCloseModal = ref(false)
const showDeleteModal = ref(false)
const showBalanceModal = ref(false)

const editingAccount = ref<AccountTreeNode | null>(null)
const closingAccount = ref<AccountTreeNode | null>(null)
const deletingAccount = ref<AccountTreeNode | null>(null)
const deletingAccountTxCount = ref(0)
const viewingBalanceAccount = ref<AccountTreeNode | null>(null)

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

// Handle date filter changes
async function handleDateFilterChange(newDateFilter: AccountDateFilter) {
  dateFilter.value = newDateFilter
  await loadAccounts()
}

// Fetch accounts on mount (all-time by default)
onMounted(() => {
  loadAccounts()
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

function handleViewTransactions(node: AccountTreeNode) {
  // Navigate to Transactions tab with filters for this account
  const query: Record<string, string> = {
    accountContains: node.fullPath
  }

  // Include date range if set
  if (dateFilter.value.startDate) {
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
  } catch (error) {
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
  } catch (error) {
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
  } catch (error) {
    // Error is already displayed by the composable
  }
}

async function performReopen(node: AccountTreeNode) {
  try {
    await reopenAccount(node.fullPath)
    toast.success('Account Reopened', `Account "${node.fullPath}" reopened successfully.`)
    // Reload with current date filter
    await loadAccounts()
  } catch (error) {
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
  } catch (error) {
    // Error is already displayed by the composable
    showDeleteModal.value = false
  }
}
</script>
