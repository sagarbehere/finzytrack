<template>
  <div class="w-full space-y-4">

    <!-- State A: email service not configured in backend config -->
    <div v-if="!emailServiceUrl" class="rounded-lg border border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 p-4 space-y-2">
      <p class="text-sm font-medium text-amber-800 dark:text-amber-300">Email import is not configured.</p>
      <p class="text-sm text-amber-700 dark:text-amber-400">
        Add the following to <code class="font-mono bg-amber-100 dark:bg-amber-800/50 px-1 rounded">backend/config/config.yaml</code> and restart the backend:
      </p>
      <pre class="text-xs font-mono bg-amber-100 dark:bg-amber-800/40 rounded p-2 text-amber-900 dark:text-amber-200">email_service:
  base_url: "http://localhost:8100"</pre>
      <p class="text-sm text-amber-700 dark:text-amber-400">
        Then start the email microservice (<code class="font-mono bg-amber-100 dark:bg-amber-800/50 px-1 rounded">email_service/</code>) and reload this page.
        See <code class="font-mono bg-amber-100 dark:bg-amber-800/50 px-1 rounded">dev-docs/email-import.md</code> for setup instructions.
      </p>
    </div>

    <!-- State B: service configured but unreachable -->
    <div v-else-if="profilesError && !isLoadingProfiles" class="rounded-lg border border-red-200 dark:border-red-700 bg-red-50 dark:bg-red-900/20 p-4 space-y-2">
      <p class="text-sm font-medium text-red-800 dark:text-red-300">Email service is not reachable.</p>
      <p class="text-sm text-red-700 dark:text-red-400">
        Could not connect to <code class="font-mono bg-red-100 dark:bg-red-800/50 px-1 rounded">{{ emailServiceUrl }}</code>
        — make sure the email microservice is running.
      </p>
      <p class="text-xs text-red-600 dark:text-red-500 font-mono">{{ profilesError }}</p>
      <button
        @click="retryLoad"
        :disabled="isLoadingProfiles"
        class="mt-1 px-3 py-1.5 text-sm bg-red-100 dark:bg-red-800/50 text-red-700 dark:text-red-300 rounded hover:bg-red-200 dark:hover:bg-red-700/50 disabled:opacity-50"
      >
        {{ isLoadingProfiles ? 'Retrying…' : 'Retry' }}
      </button>
    </div>

    <!-- State C: normal operation -->
    <!-- Compact control row -->
    <div v-else class="space-y-2">
      <div v-if="profiles.length === 0 && !isLoadingProfiles" class="text-sm text-gray-500 dark:text-gray-400">
        No account profiles configured. Add YAML files to
        <code class="font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded">email_service/config/email_rules/</code>.
      </div>

      <div v-else class="flex flex-wrap items-center gap-2">
        <!-- Account dropdown -->
        <select
          v-model="selectedProfileId"
          @change="onProfileChange"
          class="flex-1 min-w-40 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-3 py-2 text-sm"
        >
          <option value="">Select an account…</option>
          <option v-for="p in profiles" :key="p.profile_id" :value="p.profile_id">
            {{ p.name }}
          </option>
        </select>

        <!-- Date range -->
        <div class="flex items-center gap-2 border border-gray-200 dark:border-gray-600 rounded-md px-2 py-1.5 shrink-0">
          <span class="text-sm text-gray-500 dark:text-gray-400">From:</span>
          <input
            v-model="sinceDate"
            type="date"
            class="px-1.5 py-1 text-sm bg-transparent border-none focus:outline-none focus:ring-0 text-gray-900 dark:text-white"
          />
          <span class="text-sm text-gray-500 dark:text-gray-400">To:</span>
          <input
            v-model="untilDate"
            type="date"
            class="px-1.5 py-1 text-sm bg-transparent border-none focus:outline-none focus:ring-0 text-gray-900 dark:text-white"
          />
        </div>

        <!-- Currency -->
        <div class="w-32 shrink-0">
          <CommodityDropdown
            v-model="selectedCurrency"
            :allow-custom="true"
            placeholder="Currency…"
          />
        </div>

        <!-- Action buttons -->
        <button
          @click="handleTestConnection"
          :disabled="!selectedProfileId || isTestingConnection"
          class="px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-md hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
        >
          {{ isTestingConnection ? 'Testing…' : 'Test Connection' }}
        </button>
        <button
          @click="handleReload"
          class="px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-md hover:bg-gray-200 dark:hover:bg-gray-600 shrink-0"
        >
          Reload
        </button>
        <button
          @click="handleFetch"
          :disabled="!selectedProfileId || !selectedCurrency || isFetching"
          class="px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 shrink-0"
        >
          <svg v-if="isFetching" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.824 3 7.938l3-2.647z"/>
          </svg>
          {{ isFetching ? 'Fetching…' : 'Fetch' }}
        </button>
      </div>

      <!-- Status line: connection test result + target account -->
      <div v-if="connectionStatus || selectedBeancountAccount" class="flex flex-wrap gap-4 text-xs text-gray-500 dark:text-gray-400 pl-1">
        <span v-if="connectionStatus"
          :class="connectionStatus.ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'">
          {{ connectionStatus.message }}
        </span>
        <span v-if="selectedBeancountAccount">
          Target: <span class="font-mono">{{ selectedBeancountAccount }}</span>
        </span>
      </div>
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
  import { useToast, useNotifications } from '@/composables/useNotifications'

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

  const toast = useToast()
  const { addNotification } = useNotifications()

  const {
    emailServiceUrl,
    profiles, profilesError, isLoadingProfiles, isFetching,
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

  onMounted(async () => {
    await loadProfiles()
    if (profilesError.value) {
      toast.warning(
        'Email service unreachable',
        `Could not connect to ${emailServiceUrl.value} — make sure the email microservice is running.`,
      )
    }
  })

  const retryLoad = async () => {
    await loadProfiles()
    if (profilesError.value) {
      toast.warning('Still unreachable', 'Email service did not respond. Check that it is running.')
    }
  }

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
    try {
      await reloadProfiles()
      if (selectedProfileId.value && !profiles.value.find(p => p.profile_id === selectedProfileId.value)) {
        selectedProfileId.value = ''
        selectedBeancountAccount.value = ''
        selectedCurrency.value = ''
      }
    } catch {
      toast.error('Reload failed', 'Could not reload email rules — is the service running?')
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
        toast.success(
          'Email fetch complete',
          `${result.stats.transactions_parsed} transaction${result.stats.transactions_parsed !== 1 ? 's' : ''} parsed from ${result.stats.emails_fetched} emails.`,
        )
        emit('proceedWithImport', {
          transactions: result.transactions,
          account: selectedBeancountAccount.value,
          currency: selectedCurrency.value,
        })
      } else {
        toast.info('Email fetch complete', 'No transactions found in the selected date range.')
      }
    } catch (e) {
      const msg = fetchError.value || (e instanceof Error ? e.message : String(e))
      addNotification({ type: 'error', title: 'Fetch failed', message: msg, isPersistent: true })
    }
  }

  const formatDate = (iso: string) =>
    new Date(iso).toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
    })
</script>
