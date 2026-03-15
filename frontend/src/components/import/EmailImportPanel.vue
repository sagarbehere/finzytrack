<template>
  <div class="w-full space-y-6">

    <!-- Account profile selector & options -->
    <div class="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
      <h3 class="text-lg font-semibold text-gray-900 dark:text-white mb-3">Email Account</h3>

      <div v-if="profiles.length === 0 && !isLoadingProfiles" class="text-sm text-gray-500 dark:text-gray-400">
        No account profiles configured. Add YAML files to
        <code class="font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded">email_service/config/email_rules/</code>.
      </div>

      <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Account</label>
          <select
            v-model="selectedProfileId"
            @change="onProfileChange"
            class="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-3 py-2 text-sm"
          >
            <option value="">Select an account…</option>
            <option v-for="p in profiles" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">From date</label>
          <input
            v-model="sinceDate"
            type="date"
            class="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Until date</label>
          <input
            v-model="untilDate"
            type="date"
            class="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-3 py-2 text-sm"
          />
        </div>
      </div>

      <div class="mt-3 flex gap-3 flex-wrap items-center">
        <button
          @click="handleTestConnection"
          :disabled="!selectedProfileId || isTestingConnection"
          class="px-3 py-2 text-sm bg-gray-200 dark:bg-gray-600 text-gray-800 dark:text-gray-100 rounded-md hover:bg-gray-300 dark:hover:bg-gray-500 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ isTestingConnection ? 'Testing…' : 'Test Connection' }}
        </button>
        <button
          @click="handleReload"
          class="px-3 py-2 text-sm bg-gray-200 dark:bg-gray-600 text-gray-800 dark:text-gray-100 rounded-md hover:bg-gray-300 dark:hover:bg-gray-500"
        >
          Reload Rules
        </button>
        <span v-if="connectionStatus"
          :class="connectionStatus.ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'"
          class="text-sm self-center">
          {{ connectionStatus.message }}
        </span>
      </div>
    </div>

    <!-- Currency selector (pre-filled from profile) -->
    <div class="bg-white dark:bg-gray-800 rounded-lg p-4 border dark:border-gray-700">
      <h3 class="text-lg font-semibold text-gray-900 dark:text-white mb-3">Currency</h3>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <CommodityDropdown
          v-model="selectedCurrency"
          label="Currency"
          :allow-custom="true"
          placeholder="Select or type currency…"
        />
      </div>
      <p v-if="selectedBeancountAccount" class="mt-2 text-xs text-gray-500 dark:text-gray-400">
        Target account: <span class="font-mono">{{ selectedBeancountAccount }}</span>
      </p>
    </div>

    <!-- Fetch button -->
    <div class="flex justify-end">
      <button
        @click="handleFetch"
        :disabled="!selectedProfileId || !selectedCurrency || isFetching"
        class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
      >
        <svg v-if="isFetching" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.824 3 7.938l3-2.647z"/>
        </svg>
        {{ isFetching ? 'Fetching…' : 'Fetch Transactions' }}
      </button>
    </div>

    <!-- Compact progress display (shown while fetching or just after) -->
    <div v-if="isFetching || progressState.phase === 'complete'" class="space-y-2">
      <p class="text-sm text-gray-600 dark:text-gray-400">{{ progressState.message }}</p>

      <!-- Progress bar: fetching and parsing stages -->
      <div
        v-if="(progressState.phase === 'fetching' || progressState.phase === 'parsing') && progressState.total"
        class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5"
      >
        <div
          class="bg-blue-600 h-1.5 rounded-full transition-all duration-200"
          :style="{ width: `${Math.round(((progressState.current ?? 0) / progressState.total) * 100)}%` }"
        />
      </div>
    </div>

    <!-- Debug summary bar (shown after a successful fetch) -->
    <div
      v-if="fetchResult && progressState.phase === 'complete'"
      class="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-4 border border-blue-200 dark:border-blue-800"
    >
      <div class="flex flex-wrap gap-6 text-sm">
        <span>
          <span class="text-gray-500 dark:text-gray-400">Emails fetched:</span>
          <span class="ml-1 font-medium text-gray-900 dark:text-white">{{ fetchResult.stats.emails_fetched }}</span>
        </span>
        <span>
          <span class="text-gray-500 dark:text-gray-400">Transactions parsed:</span>
          <span class="ml-1 font-medium text-gray-900 dark:text-white">{{ fetchResult.stats.transactions_parsed }}</span>
        </span>
        <button
          v-if="fetchResult.stats.unmatched > 0"
          @click="showUnmatched = !showUnmatched"
          class="text-amber-600 dark:text-amber-400 underline"
        >
          Unmatched: {{ fetchResult.stats.unmatched }}
        </button>
        <button
          v-if="fetchResult.stats.extraction_errors > 0"
          @click="showErrors = !showErrors"
          class="text-red-600 dark:text-red-400 underline"
        >
          Errors: {{ fetchResult.stats.extraction_errors }}
        </button>
        <span v-if="fetchResult.stats.truncated" class="text-amber-600 dark:text-amber-400 text-xs">
          ⚠ Max email limit reached — narrow date range to see more
        </span>
      </div>

      <!-- Collapsible unmatched panel -->
      <div v-if="showUnmatched && fetchResult.unmatched_emails.length > 0" class="mt-4">
        <h4 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Unmatched emails</h4>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-gray-500 dark:text-gray-400 border-b dark:border-gray-600">
              <th class="text-left pb-1">From</th>
              <th class="text-left pb-1">Subject</th>
              <th class="text-left pb-1">Date</th>
              <th class="text-left pb-1">Reason</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, i) in fetchResult.unmatched_emails" :key="i" class="border-b dark:border-gray-700">
              <td class="py-1 pr-2 text-gray-700 dark:text-gray-300">{{ item.from_address }}</td>
              <td class="py-1 pr-2 text-gray-700 dark:text-gray-300 max-w-xs truncate">{{ item.subject }}</td>
              <td class="py-1 pr-2 text-gray-500 dark:text-gray-400">{{ formatDate(item.date) }}</td>
              <td class="py-1 text-gray-500 dark:text-gray-400">{{ item.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Collapsible extraction errors panel -->
      <div v-if="showErrors && fetchResult.extraction_errors.length > 0" class="mt-4">
        <h4 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Extraction errors</h4>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-gray-500 dark:text-gray-400 border-b dark:border-gray-600">
              <th class="text-left pb-1">Subject</th>
              <th class="text-left pb-1">Rule</th>
              <th class="text-left pb-1">Reason</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(err, i) in fetchResult.extraction_errors" :key="i" class="border-b dark:border-gray-700">
              <td class="py-1 pr-2 text-gray-700 dark:text-gray-300 max-w-xs truncate">{{ err.subject }}</td>
              <td class="py-1 pr-2 text-gray-500 dark:text-gray-400">{{ err.rule_matched }}</td>
              <td class="py-1 text-red-600 dark:text-red-400">{{ err.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Error display -->
    <div v-if="fetchError" class="bg-red-50 dark:bg-red-900/20 rounded-lg p-4 border border-red-200 dark:border-red-800">
      <p class="text-sm text-red-700 dark:text-red-300">{{ fetchError }}</p>
    </div>

  </div>
</template>

<script setup lang="ts">
  import { ref, onMounted } from 'vue'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import { useEmailImporter } from '@/composables/useEmailImporter'
  import type { EmailParsedTransaction, EmailProfileInfo } from '@/composables/useEmailImporter'

  defineProps<{
    emailServiceUrl: string
  }>()

  const emit = defineEmits<{
    (e: 'proceedWithImport', payload: {
      transactions: EmailParsedTransaction[]
      account: string      // profile.beancount_account
      currency: string     // selectedCurrency (pre-filled from profile.default_currency)
    }): void
  }>()

  const {
    profiles, isLoadingProfiles, isFetching,
    fetchResult, fetchError, progressState,
    loadProfiles, reloadProfiles, testConnection, fetchTransactions,
  } = useEmailImporter()

  const selectedProfileId = ref('')
  const selectedBeancountAccount = ref('')
  const selectedCurrency = ref('')
  // Default date range: today - 7 days to today
  const today = new Date().toISOString().split('T')[0]
  const defaultSince = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0]
  const sinceDate = ref(defaultSince)
  const untilDate = ref(today)
  const isTestingConnection = ref(false)
  const connectionStatus = ref<{ ok: boolean; message: string } | null>(null)
  const showUnmatched = ref(false)
  const showErrors = ref(false)

  onMounted(() => loadProfiles())

  const onProfileChange = () => {
    const profile = profiles.value.find((p: EmailProfileInfo) => p.profile_id === selectedProfileId.value)
    if (profile) {
      selectedBeancountAccount.value = profile.beancount_account
      selectedCurrency.value = profile.default_currency
      // Compute default since_date from profile's lookback_days (or fallback to 7)
      const days = profile.lookback_days ?? 7
      sinceDate.value = new Date(Date.now() - days * 86400000).toISOString().split('T')[0]
      untilDate.value = new Date().toISOString().split('T')[0]
    } else {
      selectedBeancountAccount.value = ''
      selectedCurrency.value = ''
    }
    connectionStatus.value = null
  }

  const handleTestConnection = async () => {
    if (!selectedProfileId.value) return
    isTestingConnection.value = true
    connectionStatus.value = null
    try {
      const result = await testConnection(selectedProfileId.value)
      connectionStatus.value = result.success
        ? { ok: true, message: result.message || 'Connected successfully' }
        : { ok: false, message: result.error || 'Connection failed' }
    } finally {
      isTestingConnection.value = false
    }
  }

  const handleReload = async () => {
    await reloadProfiles()
    // If selected profile no longer exists after reload, clear selection
    if (selectedProfileId.value && !profiles.value.find(p => p.profile_id === selectedProfileId.value)) {
      selectedProfileId.value = ''
      selectedBeancountAccount.value = ''
      selectedCurrency.value = ''
    }
  }

  const handleFetch = async () => {
    if (!selectedProfileId.value || !selectedCurrency.value) return
    showUnmatched.value = false
    showErrors.value = false
    try {
      const result = await fetchTransactions(
        selectedProfileId.value,
        sinceDate.value,
        untilDate.value,
      )
      if (result.transactions.length > 0) {
        emit('proceedWithImport', {
          transactions: result.transactions,
          account: selectedBeancountAccount.value,
          currency: selectedCurrency.value,
        })
      }
    } catch {
      // fetchError is already set by the composable
    }
  }

  const formatDate = (iso: string) =>
    new Date(iso).toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
    })
</script>
