<template>
  <div class="w-full space-y-4">

    <!-- State A: email import not enabled in backend config -->
    <div v-if="!emailImportEnabled" class="rounded-lg border border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 p-4 space-y-2">
      <p class="text-sm font-medium text-amber-800 dark:text-amber-300">Email import is not enabled.</p>
      <p class="text-sm text-amber-700 dark:text-amber-400">
        Enable it under <strong>Email Import</strong> in <router-link to="/settings" class="underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-200">Settings</router-link>.
      </p>
    </div>

    <!-- State B: enabled but failed to load profiles -->
    <div v-else-if="profilesError && !isLoadingProfiles" class="rounded-lg border border-red-200 dark:border-red-700 bg-red-50 dark:bg-red-900/20 p-4 space-y-2">
      <p class="text-sm font-medium text-red-800 dark:text-red-300">Failed to load email profiles.</p>
      <p class="text-sm text-red-700 dark:text-red-400">
        Could not load email import profiles from the backend.
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

      <!-- Invalid profiles banner -->
      <div
        v-if="invalidProfiles.length > 0"
        class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700 rounded-lg p-4"
      >
        <div class="flex items-start gap-2">
          <svg class="h-5 w-5 text-yellow-600 dark:text-yellow-400 shrink-0 mt-0.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
          </svg>
          <div class="text-sm text-yellow-800 dark:text-yellow-200">
            <p class="font-medium mb-1">
              {{ invalidProfiles.length === 1 ? '1 profile file could not be loaded:' : `${invalidProfiles.length} profile files could not be loaded:` }}
            </p>
            <ul class="space-y-1">
              <li v-for="profile in invalidProfiles" :key="profile.filename">
                <span class="font-mono font-medium">{{ profile.filename }}</span>
                <span class="text-yellow-700 dark:text-yellow-300"> — {{ profile.error }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <div v-if="profiles.length === 0 && !isLoadingProfiles" class="text-sm text-gray-500 dark:text-gray-400">
        No account profiles configured. Add YAML rule files to
        the <code class="font-mono bg-gray-100 dark:bg-gray-400/10 px-1 rounded">config/email_rules/</code> directory.
      </div>

      <div v-else class="flex flex-wrap items-center gap-2">
        <!-- Account dropdown -->
        <Listbox as="div" v-model="selectedProfileId" class="flex-1 min-w-40">
          <ListboxLabel class="sr-only">Email profile</ListboxLabel>
          <div class="relative">
            <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
              <span class="col-start-1 row-start-1 truncate pr-6">{{ profileOptions.find(o => o.value === selectedProfileId)?.label || 'Select an account...' }}</span>
              <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
            </ListboxButton>
            <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
              <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
                <ListboxOption v-for="opt in profileOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
                  <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                    <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ opt.label }}</span>
                    <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                      <CheckIcon class="size-5" aria-hidden="true" />
                    </span>
                  </li>
                </ListboxOption>
              </ListboxOptions>
            </transition>
          </div>
        </Listbox>

        <!-- Date range -->
        <div class="flex items-center gap-2 border border-gray-200 dark:border-white/10 rounded-md px-2 h-9 shrink-0">
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
            custom-class="h-9 py-1.5"
          />
        </div>

        <!-- Parsing mode toggle -->
        <span class="isolate inline-flex rounded-md shadow-xs shrink-0">
          <button
            @click="parsingMode = 'regex'"
            class="relative inline-flex items-center rounded-l-md px-3 h-9 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
            :class="parsingMode === 'regex'
              ? 'bg-indigo-600 text-white inset-ring inset-ring-indigo-600 dark:bg-indigo-500 dark:inset-ring-indigo-500'
              : 'bg-white text-gray-900 inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:inset-ring-white/5 dark:hover:bg-white/20'"
          >Regex</button>
          <button
            @click="parsingMode = 'ai'"
            class="relative -ml-px inline-flex items-center rounded-r-md px-3 h-9 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
            :class="parsingMode === 'ai'
              ? 'bg-indigo-600 text-white inset-ring inset-ring-indigo-600 dark:bg-indigo-500 dark:inset-ring-indigo-500'
              : 'bg-white text-gray-900 inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:inset-ring-white/5 dark:hover:bg-white/20'"
          >AI</button>
        </span>

        <!-- Action buttons -->
        <button
          @click="handleTestConnection"
          :disabled="!selectedProfileId || isTestingConnection"
          class="h-9 rounded-md bg-white px-3 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        >
          {{ isTestingConnection ? 'Testing…' : 'Test Connection' }}
        </button>
        <button
          @click="handleReload"
          class="h-9 rounded-md bg-white px-3 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        >
          Reload
        </button>
        <button
          @click="handleFetch"
          :disabled="!selectedProfileId || !selectedCurrency || isFetching"
          class="h-9 flex items-center gap-2 shrink-0 rounded-md bg-indigo-600 px-4 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
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
        class="w-full bg-gray-200 dark:bg-gray-800 rounded-full h-1.5"
      >
        <div
          class="bg-indigo-600 h-1.5 rounded-full transition-all duration-200"
          :style="{ width: `${Math.round(((progressState.current ?? 0) / progressState.total) * 100)}%` }"
        />
      </div>
    </div>

    <!-- Debug summary bar (shown after a successful fetch) -->
    <div
      v-if="fetchResult && progressState.phase === 'complete'"
      class="bg-indigo-50 dark:bg-indigo-900/20 rounded-lg p-4 border border-indigo-200 dark:border-indigo-800"
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
            <tr class="text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
              <th class="text-left pb-1">From</th>
              <th class="text-left pb-1">Subject</th>
              <th class="text-left pb-1">Date</th>
              <th class="text-left pb-1">Reason</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, i) in fetchResult.unmatched_emails" :key="i" class="border-b dark:border-white/10">
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
            <tr class="text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
              <th class="text-left pb-1">Subject</th>
              <th class="text-left pb-1">Rule</th>
              <th class="text-left pb-1">Reason</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(err, i) in fetchResult.extraction_errors" :key="i" class="border-b dark:border-white/10">
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
  import { ref, computed, watch, onMounted } from 'vue'
  import { Listbox, ListboxButton, ListboxLabel, ListboxOption, ListboxOptions } from '@headlessui/vue'
  import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
  import { CheckIcon } from '@heroicons/vue/20/solid'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import { useEmailImporter } from '@/composables/useEmailImporter'
  import type { EmailParsedTransaction, EmailProfileInfo } from '@/composables/useEmailImporter'
  import { useToast, useNotifications } from '@/composables/useNotifications'
  import { useConfig } from '@/composables/useConfig'
  import { errorHandler } from '@/utils/ErrorHandler'

  const emit = defineEmits<{
    (e: 'proceedWithImport', payload: {
      transactions: EmailParsedTransaction[]
      account: string      // profile.beancount_account
      currency: string     // selectedCurrency (pre-filled from profile.default_currency)
    }): void
  }>()

  const toast = useToast()
  const { addNotification } = useNotifications()
  const { config } = useConfig()

  const {
    emailImportEnabled,
    profiles, invalidProfiles, profilesError, isLoadingProfiles, isFetching,
    fetchResult, fetchError, progressState,
    loadProfiles, reloadProfiles, testConnection, fetchTransactions,
  } = useEmailImporter()

  const selectedProfileId = ref('')

  const profileOptions = computed(() => [
    { value: '', label: 'Select an account...' },
    ...profiles.value.map((p: EmailProfileInfo) => ({
      value: p.profile_id,
      label: p.name,
    })),
  ])
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
  const parsingMode = ref(config.value?.email_import?.parsing_mode ?? 'regex')

  onMounted(async () => {
    await loadProfiles()
    if (profilesError.value) {
      toast.warning(
        'Email profiles failed to load',
        'Could not load email import profiles from the backend.',
      )
    }
  })

  // Re-load profiles when email import is enabled via Settings
  watch(emailImportEnabled, async (enabled) => {
    if (enabled) await loadProfiles()
  })

  const retryLoad = async () => {
    await loadProfiles()
    if (profilesError.value) {
      toast.warning('Still failing', 'Could not load email profiles. Check the backend logs.')
    }
  }

  watch(selectedProfileId, () => onProfileChange())

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
      connectionStatus.value = { ok: true, message: result.message || 'Connected successfully' }
    } catch (e) {
      errorHandler.display(e)
      connectionStatus.value = { ok: false, message: 'Connection test failed — see notifications' }
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
      toast.success('Rules reloaded', `${profiles.value.length} profile${profiles.value.length === 1 ? '' : 's'} loaded.`)
    } catch {
      toast.error('Reload failed', 'Could not reload email rules.')
      // Re-probe: if loadProfiles also fails, profilesError is set and the panel
      // transitions back to State B (showing the Retry button).
      await loadProfiles()
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
        parsingMode.value,
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
