<template>
  <div class="pb-4">
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Investments</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        Record buys, sales, dividends, and other investment activity, and manage the metadata and
        prices that power the investment dashboards.
      </p>
    </div>

    <!-- Investment tabs -->
    <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <div class="border-b border-gray-200 dark:border-white/10">
        <nav class="-mb-px flex space-x-4 overflow-x-auto px-4 sm:space-x-8 sm:px-6" aria-label="Tabs">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            @click="activeTab = tab.id"
            :class="[
              activeTab === tab.id
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            {{ tab.label }}
          </button>
        </nav>
      </div>

      <div class="p-4 sm:p-6">
        <!-- Setup & Metadata Tab — mounted on first visit, then kept so unsaved edits
             survive switching tabs. -->
        <div v-if="metadataVisited" v-show="activeTab === 'metadata'">
          <SetupMetadataPanel />
        </div>

        <!-- Quick Actions Tab -->
        <div v-if="activeTab === 'quick-actions'">
          <QuickActionForms @staged="stageDrafts" />
        </div>

        <!-- Import statement Tab — kept mounted so a long review survives switching tabs. -->
        <div v-if="statementVisited" v-show="activeTab === 'statement'">
          <StatementImportPanel />
        </div>

        <!-- Prices Tab -->
        <div v-else-if="activeTab === 'prices'">
          <PricesPanel @open-metadata="activeTab = 'metadata'" />
        </div>
      </div>
    </div>

    <!-- Review table for staged drafts (shared component; appears below the tabs) -->
    <div v-if="stagedTransactions.length > 0" class="mt-6">
      <div class="flex justify-end mb-4">
        <button
          @click="clearStaged($event)"
          :disabled="isLoading"
          class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Clear
        </button>
      </div>

      <div class="relative">
        <!-- Loading overlay -->
        <div v-if="isLoading" class="absolute inset-0 bg-white/50 dark:bg-gray-900/50 z-10 flex items-center justify-center backdrop-blur-sm">
          <div class="bg-white dark:bg-gray-800 rounded-lg shadow-lg p-6 flex items-center gap-3">
            <svg class="animate-spin h-6 w-6 text-indigo-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-gray-700 dark:text-gray-300 font-medium">Processing…</span>
          </div>
        </div>

        <TransactionTable
          ref="transactionTableRef"
          :transactions="stagedTransactions"
          :import-context="importContext"
          :show-search="false"
          :show-column-filters="false"
          :show-summary="true"
          @transactions-updated="handleTransactionsUpdated"
        />
      </div>

      <div class="flex justify-end mt-4">
        <button
          @click="registerTransactions"
          :disabled="isLoading"
          class="flex items-center gap-2 rounded-md bg-green-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-green-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-green-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-green-500 dark:shadow-none dark:hover:bg-green-400"
        >
          <svg v-if="isLoading" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{{ isLoading ? 'Registering…' : 'Register' }}</span>
        </button>
      </div>
    </div>

    <!-- Register confirmation modal (duplicates / unbalanced) -->
    <ConfirmDialog
      :is-open="registerConfirmOpen"
      title="Issues Detected"
      :message="registerConfirmMessage"
      confirm-text="Register Anyway"
      cancel-text="Go Back"
      variant="warning"
      @confirm="doRegisterTransactions"
      @cancel="registerConfirmOpen = false"
      @close="registerConfirmOpen = false"
    />
  </div>
</template>

<script setup lang="ts">
  import { ref, computed, nextTick, watch } from 'vue'
  import { useRoute } from 'vue-router'
  import QuickActionForms from '@/components/investments/QuickActionForms.vue'
  import SetupMetadataPanel from '@/components/investments/SetupMetadataPanel.vue'
  import PricesPanel from '@/components/investments/PricesPanel.vue'
  import StatementImportPanel from '@/components/investments/StatementImportPanel.vue'
  import TransactionTable from '@/components/common/TransactionTable.vue'
  import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
  import type { TransactionViewModel, ImportContext } from '@/types/transactions'
  import { useTransactionImporter } from '@/composables/useTransactionImporter'
  import { useServerNotices } from '@/composables/useServerNotices'
  import { useToast } from '@/composables/useNotifications'
  import { isTransactionBalanced } from '@/utils/transactions'

  defineOptions({ name: 'InvestmentsView' })

  // Function-scoped importer instance — staging is local to this view (composable-lifetimes rule).
  const { performCommit, isLoading } = useTransactionImporter()
  const { check: checkServerNotices } = useServerNotices()
  const { success: showSuccessToast } = useToast()

  const tabs = [
    { id: 'quick-actions', label: 'Quick Actions' },
    { id: 'statement', label: 'Import statement' },
    { id: 'metadata', label: 'Setup & Metadata' },
    { id: 'prices', label: 'Prices' },
  ] as const

  // `?tab=metadata` / `?tab=prices` / `?tab=statement` open that tab directly (the dashboards'
  // metadata hint and the Import view's CAS signpost link here).
  const route = useRoute()
  const activeTab = ref<string>(
    tabs.some(t => t.id === route.query.tab) ? String(route.query.tab) : 'quick-actions',
  )
  const metadataVisited = ref(false)
  const statementVisited = ref(false)
  watch(activeTab, tab => {
    if (tab === 'metadata') metadataVisited.value = true
    if (tab === 'statement') statementVisited.value = true
  }, { immediate: true })

  // Shared review-table state
  const stagedTransactions = ref<TransactionViewModel[]>([])
  const importContext = ref<Map<string, ImportContext>>(new Map())
  const transactionTableRef = ref<InstanceType<typeof TransactionTable> | null>(null)
  const registerConfirmOpen = ref(false)

  const pendingUnbalancedCount = computed(() =>
    stagedTransactions.value.filter(t => !isTransactionBalanced(t)).length
  )

  const registerConfirmMessage = computed(() => {
    const lines: string[] = ['The following issues were detected:']
    if (pendingUnbalancedCount.value > 0) {
      const n = pendingUnbalancedCount.value
      lines.push(`• ${n} unbalanced transaction${n !== 1 ? 's' : ''}`)
    }
    lines.push('\nDo you want to register anyway?')
    return lines.join('\n')
  })

  /**
   * Stage assembled draft(s) into the shared review table. This is the integration
   * seam the Quick Action forms and the AI paths call in later slices; each draft is
   * a TransactionViewModel produced by the assembler → converter.
   */
  function stageDrafts(drafts: TransactionViewModel[]) {
    if (!drafts.length) return
    stagedTransactions.value = [...stagedTransactions.value, ...drafts]
    drafts.forEach(d => importContext.value.set(d.id, { is_duplicate: false }))
    nextTick(() => {
      transactionTableRef.value?.reinitializeBaselines()
      transactionTableRef.value?.scrollToTable()
    })
  }
  // Exposed so parent slices / tests can drive staging; referenced to satisfy the seam.
  defineExpose({ stageDrafts })

  const handleTransactionsUpdated = (updated: TransactionViewModel[]) => {
    stagedTransactions.value = updated
    const validIds = new Set(updated.map(t => t.id))
    for (const id of importContext.value.keys()) {
      if (!validIds.has(id)) importContext.value.delete(id)
    }
  }

  const clearStaged = (event?: Event) => {
    stagedTransactions.value = []
    importContext.value.clear()
    if (event?.target) (event.target as HTMLElement).blur()
  }

  const registerTransactions = () => {
    if (!stagedTransactions.value.length) return
    if (pendingUnbalancedCount.value > 0) {
      registerConfirmOpen.value = true
      return
    }
    doRegisterTransactions()
  }

  const doRegisterTransactions = async () => {
    registerConfirmOpen.value = false
    try {
      const result = await performCommit(stagedTransactions.value)
      if (result.success) {
        showSuccessToast('Registered', `Successfully registered ${result.count} transaction${result.count !== 1 ? 's' : ''}`)
        await nextTick()
        stagedTransactions.value = []
        importContext.value.clear()
        checkServerNotices()
      }
    } catch (_error) {
      // Error already surfaced via errorHandler.display() in the composable
    }
  }
</script>
