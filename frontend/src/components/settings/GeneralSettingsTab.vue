<template>
  <div class="space-y-6">

    <!-- ── Data ──────────────────────────────────────────────────────────── -->
    <SettingsSection
      title="Data"
      description="Paths to your financial data files."
      :is-dirty="dataIsDirty"
      :is-saving="dataSaving"
      :error="dataError"
      @save="saveData"
      @reset="resetData"
    >
      <!-- Config file path (read-only) -->
      <div v-if="config?.config_file_path">
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Config File</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">The configuration file currently in use.</p>
        <p class="text-sm font-mono text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-900 px-3 py-2 rounded-md border border-gray-200 dark:border-white/10">
          {{ config.config_file_path }}
        </p>
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Ledger File</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Path to your Beancount ledger file. Can be absolute or relative to the directory the backend was started from.
        </p>
        <div class="flex gap-2">
          <input v-model="dataFields.ledger_file" type="text" placeholder="e.g. data/ledger.beancount" :class="[inputClass, 'flex-1']" />
          <button :class="browseButtonClass" @click="openFilePicker({ title: 'Select Ledger File', mode: 'file', extensions: ['.beancount', '.bean'], initialPath: dataFields.ledger_file, onSelect: p => dataFields.ledger_file = p })">Browse</button>
        </div>
      </div>
    </SettingsSection>

    <!-- ── Accounts ───────────────────────────────────────────────────────── -->
    <SettingsSection
      title="Accounts"
      description="Default account and currency settings."
      :is-dirty="accountsIsDirty"
      :is-saving="accountsSaving"
      :error="accountsError"
      @save="saveAccounts"
      @reset="resetAccounts"
    >
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Default Currency</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Currency code used when no currency is specified.</p>
        <input v-model="accountsFields.default_currency" type="text" placeholder="USD" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Default Unknown Account</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Beancount account used when a transaction cannot be categorized.</p>
        <input v-model="accountsFields.default_unknown_account" type="text" placeholder="Expenses:Unknown" :class="inputClass" />
      </div>
    </SettingsSection>

    <!-- ── AI ─────────────────────────────────────────────────────────────── -->
    <SettingsSection
      title="AI"
      description="Configure the AI model used across the app."
      :is-dirty="llmIsDirty"
      :is-saving="llmSaving"
      :error="llmError"
      @save="saveLlm"
      @reset="resetLlm"
    >
      <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
        <a href="https://finzytrack.app/docs/ai-data-sharing" target="_blank" rel="noopener noreferrer" class="text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 underline underline-offset-2">What data is shared with the AI model?</a>
      </p>

      <Listbox as="div" v-model="llmFields.provider">
        <ListboxLabel class="block text-sm/6 font-medium text-gray-900 dark:text-white">Provider</ListboxLabel>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Use <strong>OpenAI-compatible</strong> for local models (LM Studio, Ollama), OpenAI, or Groq.
          Use <strong>Anthropic</strong> for the Anthropic API directly.
        </p>
        <div class="relative">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ providerOptions.find(o => o.value === llmFields.provider)?.label ?? llmFields.provider }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in providerOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
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

      <div v-if="llmFields.provider === 'openai'">
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">API URL</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Base URL of the OpenAI-compatible endpoint, e.g. <code class="font-mono">http://127.0.0.1:1234</code> or <code class="font-mono">https://api.openai.com</code>.
        </p>
        <input v-model="llmFields.api_url" type="text" placeholder="http://127.0.0.1:1234" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">API Key</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Required for cloud providers. Leave empty for local models.</p>
        <div class="relative">
          <input
            v-model="llmFields.api_key"
            :type="showApiKey ? 'text' : 'password'"
            placeholder="sk-..."
            :class="[inputClass, 'pr-10']"
          />
          <button
            type="button"
            @click="showApiKey = !showApiKey"
            class="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            :aria-label="showApiKey ? 'Hide API key' : 'Show API key'"
          >
            <!-- Eye-off -->
            <svg v-if="showApiKey" class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
            </svg>
            <!-- Eye -->
            <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
            </svg>
          </button>
        </div>
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Model</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Model name, e.g. <code class="font-mono">gpt-4o</code>, <code class="font-mono">claude-sonnet-4-6</code>, <code class="font-mono">llama-3.1-8b-instruct</code>.
        </p>
        <input v-model="llmFields.model" type="text" placeholder="gpt-4o" :class="inputClass" />
      </div>

      <div class="grid grid-cols-3 gap-4">
        <div>
          <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Temperature</label>
          <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">0 = deterministic, 2 = very random.</p>
          <input v-model.number="llmFields.temperature" type="number" min="0" max="2" step="0.1" :class="inputClass" />
        </div>
        <div>
          <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Max Tokens</label>
          <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Max output tokens. 0 = model default.</p>
          <input v-model.number="llmFields.max_tokens" type="number" min="0" step="256" :class="inputClass" />
          <p
            v-if="llmFields.provider === 'anthropic' && !llmFields.max_tokens"
            class="mt-1 text-xs text-amber-600 dark:text-amber-400"
          >
            Anthropic requires a value. Defaults to 8192.
          </p>
        </div>
        <div>
          <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Max Tool Rounds</label>
          <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Tool-call round-trips per message.</p>
          <input v-model.number="llmFields.max_tool_rounds" type="number" min="1" max="50" step="1" :class="inputClass" />
        </div>
      </div>
    </SettingsSection>

    <!-- ── Categorization ─────────────────────────────────────────────────── -->
    <SettingsSection
      title="Categorization"
      description="Automatic transaction categorization settings."
      :is-dirty="categorizationIsDirty"
      :is-saving="categorizationSaving"
      :error="categorizationError"
      @save="saveCategorization"
      @reset="resetCategorization"
    >
      <div class="flex items-center justify-between">
        <span class="text-sm font-medium text-gray-900 dark:text-white">Enable auto-categorization</span>
        <div class="group relative inline-flex w-11 shrink-0 rounded-full bg-gray-200 p-0.5 inset-ring inset-ring-gray-900/5 outline-offset-2 outline-indigo-600 transition-colors duration-200 ease-in-out has-checked:bg-indigo-600 has-focus-visible:outline-2 dark:bg-white/5 dark:inset-ring-white/10 dark:outline-indigo-500 dark:has-checked:bg-indigo-500">
          <span class="size-5 rounded-full bg-white shadow-xs ring-1 ring-gray-900/5 transition-transform duration-200 ease-in-out group-has-checked:translate-x-5"></span>
          <input type="checkbox" v-model="categorizationFields.enabled" class="absolute inset-0 size-full appearance-none focus:outline-hidden" aria-label="Enable auto-categorization" />
        </div>
      </div>

      <Listbox as="div" v-model="categorizationFields.engine">
        <ListboxLabel class="block text-sm/6 font-medium text-gray-900 dark:text-white">Engine</ListboxLabel>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          <strong>Classifier</strong> trains on your existing ledger history.
          <strong>AI</strong> uses your configured language model (requires AI to be set up).
        </p>
        <div class="relative">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ categorizationEngineOptions.find(o => o.value === categorizationFields.engine)?.label ?? categorizationFields.engine }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in categorizationEngineOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
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

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Training Data File</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Path to a Beancount file used as training data for the classifier.</p>
        <div class="flex gap-2">
          <input v-model="categorizationFields.training_data_file" type="text" placeholder="data/training.beancount" :class="[inputClass, 'flex-1']" />
          <button :class="browseButtonClass" @click="openFilePicker({ title: 'Select Training Data File', mode: 'file', extensions: ['.beancount', '.bean'], initialPath: categorizationFields.training_data_file, onSelect: p => categorizationFields.training_data_file = p })">Browse</button>
        </div>
      </div>
    </SettingsSection>

    <!-- ── Email Import ────────────────────────────────────────────────── -->
    <SettingsSection
      title="Email Import"
      description="IMAP email fetching and rule-based transaction parsing."
      :is-dirty="emailIsDirty"
      :is-saving="emailSaving"
      :error="emailError"
      @save="saveEmail"
      @reset="resetEmail"
    >
      <div class="flex items-center justify-between">
        <span class="text-sm font-medium text-gray-900 dark:text-white">Enable email import</span>
        <div class="group relative inline-flex w-11 shrink-0 rounded-full bg-gray-200 p-0.5 inset-ring inset-ring-gray-900/5 outline-offset-2 outline-indigo-600 transition-colors duration-200 ease-in-out has-checked:bg-indigo-600 has-focus-visible:outline-2 dark:bg-white/5 dark:inset-ring-white/10 dark:outline-indigo-500 dark:has-checked:bg-indigo-500">
          <span class="size-5 rounded-full bg-white shadow-xs ring-1 ring-gray-900/5 transition-transform duration-200 ease-in-out group-has-checked:translate-x-5"></span>
          <input type="checkbox" v-model="emailFields.enabled" class="absolute inset-0 size-full appearance-none focus:outline-hidden" aria-label="Enable email import" />
        </div>
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Default Lookback Days</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Number of days to look back for emails when no date range is specified.</p>
        <input v-model.number="emailFields.default_lookback_days" type="number" min="1" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Max Emails</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Maximum emails to fetch per request. Truncates with a warning if exceeded.</p>
        <input v-model.number="emailFields.max_emails" type="number" min="1" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">IMAP Timeout (seconds)</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Socket timeout for IMAP operations. Set to 0 for no timeout.</p>
        <input v-model.number="emailFields.imap_timeout_secs" type="number" min="0" :class="inputClass" />
      </div>

      <Listbox as="div" v-model="emailFields.parsing_mode">
        <ListboxLabel class="block text-sm/6 font-medium text-gray-900 dark:text-white">Parsing Mode</ListboxLabel>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          <strong>Regex</strong> uses patterns defined in rule files.
          <strong>AI</strong> uses your configured language model (requires AI to be set up).
          Can be overridden per account or per rule.
        </p>
        <div class="relative">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ parsingModeOptions.find(o => o.value === emailFields.parsing_mode)?.label ?? emailFields.parsing_mode }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in parsingModeOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
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
    </SettingsSection>

    <!-- ── Analytics ─────────────────────────────────────────────────────── -->
    <SettingsSection
      title="Analytics"
      description="SQLite export settings used by the Analyze tab."
      :is-dirty="analyticsIsDirty"
      :is-saving="analyticsSaving"
      :error="analyticsError"
      @save="saveAnalytics"
      @reset="resetAnalytics"
    >
      <div class="flex items-center justify-between">
        <span class="text-sm font-medium text-gray-900 dark:text-white">Auto-sync on ledger changes</span>
        <div class="group relative inline-flex w-11 shrink-0 rounded-full bg-gray-200 p-0.5 inset-ring inset-ring-gray-900/5 outline-offset-2 outline-indigo-600 transition-colors duration-200 ease-in-out has-checked:bg-indigo-600 has-focus-visible:outline-2 dark:bg-white/5 dark:inset-ring-white/10 dark:outline-indigo-500 dark:has-checked:bg-indigo-500">
          <span class="size-5 rounded-full bg-white shadow-xs ring-1 ring-gray-900/5 transition-transform duration-200 ease-in-out group-has-checked:translate-x-5"></span>
          <input type="checkbox" v-model="analyticsFields.auto_sync_enabled" class="absolute inset-0 size-full appearance-none focus:outline-hidden" aria-label="Auto-sync on ledger changes" />
        </div>
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Sync Debounce (seconds)</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Delay before syncing after a ledger change, to avoid excessive writes.</p>
        <input v-model.number="analyticsFields.sync_debounce_seconds" type="number" min="0" step="0.5" :class="inputClass" />
      </div>

      <div class="flex items-center justify-between">
        <span class="text-sm font-medium text-gray-900 dark:text-white">Enable WAL mode (recommended for concurrent access)</span>
        <div class="group relative inline-flex w-11 shrink-0 rounded-full bg-gray-200 p-0.5 inset-ring inset-ring-gray-900/5 outline-offset-2 outline-indigo-600 transition-colors duration-200 ease-in-out has-checked:bg-indigo-600 has-focus-visible:outline-2 dark:bg-white/5 dark:inset-ring-white/10 dark:outline-indigo-500 dark:has-checked:bg-indigo-500">
          <span class="size-5 rounded-full bg-white shadow-xs ring-1 ring-gray-900/5 transition-transform duration-200 ease-in-out group-has-checked:translate-x-5"></span>
          <input type="checkbox" v-model="analyticsFields.enable_wal" class="absolute inset-0 size-full appearance-none focus:outline-hidden" aria-label="Enable WAL mode (recommended for concurrent access)" />
        </div>
      </div>
    </SettingsSection>

    <!-- ── Backup ─────────────────────────────────────────────────────────── -->
    <SettingsSection
      title="Backup"
      description="Automatic backup settings for your ledger and config files."
      :is-dirty="backupIsDirty"
      :is-saving="backupSaving"
      :error="backupError"
      @save="saveBackup"
      @reset="resetBackup"
    >
      <div class="flex items-center justify-between">
        <span class="text-sm font-medium text-gray-900 dark:text-white">Enable automatic backups</span>
        <div class="group relative inline-flex w-11 shrink-0 rounded-full bg-gray-200 p-0.5 inset-ring inset-ring-gray-900/5 outline-offset-2 outline-indigo-600 transition-colors duration-200 ease-in-out has-checked:bg-indigo-600 has-focus-visible:outline-2 dark:bg-white/5 dark:inset-ring-white/10 dark:outline-indigo-500 dark:has-checked:bg-indigo-500">
          <span class="size-5 rounded-full bg-white shadow-xs ring-1 ring-gray-900/5 transition-transform duration-200 ease-in-out group-has-checked:translate-x-5"></span>
          <input type="checkbox" v-model="backupFields.enabled" class="absolute inset-0 size-full appearance-none focus:outline-hidden" aria-label="Enable automatic backups" />
        </div>
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Retention Count</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Number of backup files to keep.</p>
        <input v-model.number="backupFields.retention_count" type="number" min="1" :class="inputClass" />
      </div>

      <div class="flex items-center justify-between">
        <span class="text-sm font-medium text-gray-900 dark:text-white">Automatically remove oldest backups when limit is exceeded</span>
        <div class="group relative inline-flex w-11 shrink-0 rounded-full bg-gray-200 p-0.5 inset-ring inset-ring-gray-900/5 outline-offset-2 outline-indigo-600 transition-colors duration-200 ease-in-out has-checked:bg-indigo-600 has-focus-visible:outline-2 dark:bg-white/5 dark:inset-ring-white/10 dark:outline-indigo-500 dark:has-checked:bg-indigo-500">
          <span class="size-5 rounded-full bg-white shadow-xs ring-1 ring-gray-900/5 transition-transform duration-200 ease-in-out group-has-checked:translate-x-5"></span>
          <input type="checkbox" v-model="backupFields.cleanup_on_exceed" class="absolute inset-0 size-full appearance-none focus:outline-hidden" aria-label="Automatically remove oldest backups when limit is exceeded" />
        </div>
      </div>
    </SettingsSection>

    <!-- ── Logging ────────────────────────────────────────────────────────── -->
    <SettingsSection
      title="Logging"
      description="Backend logging settings."
      :is-dirty="loggingIsDirty"
      :is-saving="loggingSaving"
      :error="loggingError"
      @save="saveLogging"
      @reset="resetLogging"
    >
      <Listbox as="div" v-model="loggingFields.level">
        <ListboxLabel class="block text-sm/6 font-medium text-gray-900 dark:text-white">Log Level</ListboxLabel>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Verbosity of the backend logs. Takes effect immediately.</p>
        <div class="relative">
          <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
            <span class="col-start-1 row-start-1 truncate pr-6">{{ logLevelOptions.find(o => o.value === loggingFields.level)?.label ?? loggingFields.level }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
            <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
              <ListboxOption v-for="opt in logLevelOptions" :key="opt.value" :value="opt.value" as="template" v-slot="{ active, selected }">
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

    </SettingsSection>

    <!-- ── Server ─────────────────────────────────────────────────────────── -->
    <SettingsSection
      title="Server"
      description="Backend server host and port."
      :requires-restart="true"
      :is-dirty="serverIsDirty"
      :is-saving="serverSaving"
      :error="serverError"
      @save="saveServer"
      @reset="resetServer"
    >
      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Host</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Address the server listens on. Use <code class="font-mono">127.0.0.1</code> for local-only access.
        </p>
        <input v-model="serverFields.host" type="text" placeholder="127.0.0.1" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Port</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Port the server listens on.</p>
        <input v-model.number="serverFields.port" type="number" min="1" max="65535" :class="inputClass" />
      </div>
    </SettingsSection>

    <!-- Shared file picker modal -->
    <FilePickerModal
      :open="filePicker.open"
      :title="filePicker.title"
      :mode="filePicker.mode"
      :extensions="filePicker.extensions"
      :initial-path="filePicker.initialPath"
      @select="(p: string) => { filePicker.onSelect(p); filePicker.open = false }"
      @close="filePicker.open = false"
    />

  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { Listbox, ListboxButton, ListboxLabel, ListboxOption, ListboxOptions } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
import { CheckIcon } from '@heroicons/vue/20/solid'
import SettingsSection from './SettingsSection.vue'
import FilePickerModal from '@/components/common/FilePickerModal.vue'
import { useConfig } from '@/composables/useConfig'
import { useAccounts } from '@/composables/useAccounts'
import { useCommodities } from '@/composables/useCommodities'
import { useToast } from '@/composables/useNotifications'
import { patchConfig } from '@/composables/useConfigPatch'

const emit = defineEmits<{ 'restart-required': [] }>()

const { config, updateConfig } = useConfig()
const { invalidateCache: invalidateAccounts } = useAccounts()
const { invalidateCache: invalidateCommodities } = useCommodities()
const toast = useToast()

// ─── Shared UI classes ────────────────────────────────────────────────────────

const inputClass = 'block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500'


// ─── File picker state ───────────────────────────────────────────────────────

const filePicker = reactive({
  open: false,
  title: 'Select File',
  mode: 'file' as 'file' | 'directory',
  extensions: undefined as string[] | undefined,
  initialPath: '',
  onSelect: (_path: string) => {},
})

function openFilePicker(opts: {
  title: string
  mode: 'file' | 'directory'
  extensions?: string[]
  initialPath?: string
  onSelect: (path: string) => void
}) {
  filePicker.title = opts.title
  filePicker.mode = opts.mode
  filePicker.extensions = opts.extensions
  filePicker.initialPath = opts.initialPath ?? ''
  filePicker.onSelect = opts.onSelect
  filePicker.open = true
}

const browseButtonClass = 'shrink-0 rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20'

// ─── Eye toggle for API key ───────────────────────────────────────────────────

const showApiKey = ref(false)

// ─── Select option constants ─────────────────────────────────────────────────

const providerOptions = [
  { value: 'openai', label: 'OpenAI-compatible' },
  { value: 'anthropic', label: 'Anthropic' },
] as const

const categorizationEngineOptions = [
  { value: 'classifier', label: 'Classifier (scikit-learn)' },
  { value: 'ai', label: 'AI' },
] as const

const parsingModeOptions = [
  { value: 'regex', label: 'Regex' },
  { value: 'ai', label: 'AI' },
] as const

const logLevelOptions = [
  { value: 'DEBUG', label: 'DEBUG' },
  { value: 'INFO', label: 'INFO' },
  { value: 'WARNING', label: 'WARNING' },
  { value: 'ERROR', label: 'ERROR' },
  { value: 'CRITICAL', label: 'CRITICAL' },
] as const

// ─── Section helpers ──────────────────────────────────────────────────────────

async function saveSection(
  patch: Record<string, unknown>,
  saving: { value: boolean },
  error: { value: string },
) {
  saving.value = true
  error.value = ''
  try {
    const previousLedger = config.value?.ledger_file
    const result = await patchConfig(patch)
    updateConfig(result.config)

    // If the ledger file changed (hot-switched), invalidate cached ledger data
    if (result.config.ledger_file !== previousLedger) {
      invalidateAccounts()
      invalidateCommodities()
      if (result.notice) {
        toast.info('New ledger created', result.notice)
      } else {
        toast.success('Ledger switched', 'Now using ' + result.config.ledger_file)
      }
    } else {
      toast.success('Saved', 'Settings saved successfully')
    }

    if (result.restart_required) emit('restart-required')
  } catch (e: any) {
    error.value = e.message ?? 'Failed to save'
  } finally {
    saving.value = false
  }
}

// ─── Data section ─────────────────────────────────────────────────────────────

const dataFields = reactive({
  ledger_file: config.value?.ledger_file ?? '',
})
const dataSaving = ref(false)
const dataError = ref('')

const dataIsDirty = computed(() =>
  dataFields.ledger_file !== (config.value?.ledger_file ?? '')
)

function initDataFields() {
  dataFields.ledger_file = config.value?.ledger_file ?? ''
}

async function saveData() {
  await saveSection({
    ledger_file: dataFields.ledger_file,
  }, dataSaving, dataError)
}

function resetData() { initDataFields(); dataError.value = '' }

// ─── Accounts section ─────────────────────────────────────────────────────────

const accountsFields = reactive({
  default_currency: config.value?.accounts?.default_currency ?? 'USD',
  default_unknown_account: config.value?.accounts?.default_unknown_account ?? 'Expenses:Unknown',
})
const accountsSaving = ref(false)
const accountsError = ref('')

const accountsIsDirty = computed(() =>
  accountsFields.default_currency !== (config.value?.accounts?.default_currency ?? 'USD') ||
  accountsFields.default_unknown_account !== (config.value?.accounts?.default_unknown_account ?? 'Expenses:Unknown')
)

function initAccountsFields() {
  accountsFields.default_currency = config.value?.accounts?.default_currency ?? 'USD'
  accountsFields.default_unknown_account = config.value?.accounts?.default_unknown_account ?? 'Expenses:Unknown'
}

async function saveAccounts() {
  await saveSection({ accounts: { ...accountsFields } }, accountsSaving, accountsError)
}

function resetAccounts() { initAccountsFields(); accountsError.value = '' }

// ─── LLM section ──────────────────────────────────────────────────────────────

const llmFields = reactive({
  provider: (config.value?.ai?.llm?.provider ?? 'openai') as string,
  api_url: config.value?.ai?.llm?.api_url ?? '',
  api_key: config.value?.ai?.llm?.api_key ?? '',
  model: config.value?.ai?.llm?.model ?? '',
  temperature: config.value?.ai?.llm?.temperature ?? 0.1,
  max_tokens: config.value?.ai?.llm?.max_tokens ?? 0,
  max_tool_rounds: config.value?.ai?.llm?.max_tool_rounds ?? 12,
})
const llmSaving = ref(false)
const llmError = ref('')

const llmIsDirty = computed(() =>
  llmFields.provider !== (config.value?.ai?.llm?.provider ?? 'openai') ||
  llmFields.api_url !== (config.value?.ai?.llm?.api_url ?? '') ||
  llmFields.api_key !== (config.value?.ai?.llm?.api_key ?? '') ||
  llmFields.model !== (config.value?.ai?.llm?.model ?? '') ||
  llmFields.temperature !== (config.value?.ai?.llm?.temperature ?? 0.1) ||
  llmFields.max_tokens !== (config.value?.ai?.llm?.max_tokens ?? 0) ||
  llmFields.max_tool_rounds !== (config.value?.ai?.llm?.max_tool_rounds ?? 12)
)

function initLlmFields() {
  llmFields.provider = config.value?.ai?.llm?.provider ?? 'openai'
  llmFields.api_url = config.value?.ai?.llm?.api_url ?? ''
  llmFields.api_key = config.value?.ai?.llm?.api_key ?? ''
  llmFields.model = config.value?.ai?.llm?.model ?? ''
  llmFields.temperature = config.value?.ai?.llm?.temperature ?? 0.1
  llmFields.max_tokens = config.value?.ai?.llm?.max_tokens ?? 0
  llmFields.max_tool_rounds = config.value?.ai?.llm?.max_tool_rounds ?? 12
}

async function saveLlm() {
  await saveSection({ ai: { llm: { ...llmFields } } }, llmSaving, llmError)
}

function resetLlm() { initLlmFields(); llmError.value = '' }

// ─── Categorization section ───────────────────────────────────────────────────

const categorizationFields = reactive({
  enabled: config.value?.ai?.categorization?.enabled ?? true,
  engine: config.value?.ai?.categorization?.engine ?? 'classifier',
  training_data_file: config.value?.ai?.categorization?.training_data_file ?? '',
})
const categorizationSaving = ref(false)
const categorizationError = ref('')

const categorizationIsDirty = computed(() =>
  categorizationFields.enabled !== (config.value?.ai?.categorization?.enabled ?? true) ||
  categorizationFields.engine !== (config.value?.ai?.categorization?.engine ?? 'classifier') ||
  categorizationFields.training_data_file !== (config.value?.ai?.categorization?.training_data_file ?? '')
)

function initCategorizationFields() {
  categorizationFields.enabled = config.value?.ai?.categorization?.enabled ?? true
  categorizationFields.engine = config.value?.ai?.categorization?.engine ?? 'classifier'
  categorizationFields.training_data_file = config.value?.ai?.categorization?.training_data_file ?? ''
}

async function saveCategorization() {
  await saveSection({
    ai: {
      categorization: {
        enabled: categorizationFields.enabled,
        engine: categorizationFields.engine,
        training_data_file: categorizationFields.training_data_file || null,
      },
    },
  }, categorizationSaving, categorizationError)
}

function resetCategorization() { initCategorizationFields(); categorizationError.value = '' }

// ─── Email import section ─────────────────────────────────────────────────────

const emailFields = reactive({
  enabled: config.value?.email_import?.enabled ?? false,
  default_lookback_days: config.value?.email_import?.default_lookback_days ?? 7,
  max_emails: config.value?.email_import?.max_emails ?? 500,
  imap_timeout_secs: config.value?.email_import?.imap_timeout_secs ?? 30,
  parsing_mode: config.value?.email_import?.parsing_mode ?? 'regex',
})
const emailSaving = ref(false)
const emailError = ref('')

const emailIsDirty = computed(() =>
  emailFields.enabled !== (config.value?.email_import?.enabled ?? false) ||
  emailFields.default_lookback_days !== (config.value?.email_import?.default_lookback_days ?? 7) ||
  emailFields.max_emails !== (config.value?.email_import?.max_emails ?? 500) ||
  emailFields.imap_timeout_secs !== (config.value?.email_import?.imap_timeout_secs ?? 30) ||
  emailFields.parsing_mode !== (config.value?.email_import?.parsing_mode ?? 'regex')
)

function initEmailFields() {
  emailFields.enabled = config.value?.email_import?.enabled ?? false
  emailFields.default_lookback_days = config.value?.email_import?.default_lookback_days ?? 7
  emailFields.max_emails = config.value?.email_import?.max_emails ?? 500
  emailFields.imap_timeout_secs = config.value?.email_import?.imap_timeout_secs ?? 30
  emailFields.parsing_mode = config.value?.email_import?.parsing_mode ?? 'regex'
}

async function saveEmail() {
  await saveSection({
    email_import: {
      enabled: emailFields.enabled,
      default_lookback_days: emailFields.default_lookback_days,
      max_emails: emailFields.max_emails,
      imap_timeout_secs: emailFields.imap_timeout_secs,
      parsing_mode: emailFields.parsing_mode,
    },
  }, emailSaving, emailError)
}

function resetEmail() { initEmailFields(); emailError.value = '' }

// ─── Analytics section ────────────────────────────────────────────────────────

const analyticsFields = reactive({
  auto_sync_enabled: config.value?.analytics?.sqlite?.auto_sync_enabled ?? true,
  sync_debounce_seconds: config.value?.analytics?.sqlite?.sync_debounce_seconds ?? 5.0,
  enable_wal: config.value?.analytics?.sqlite?.enable_wal ?? true,
})
const analyticsSaving = ref(false)
const analyticsError = ref('')

const analyticsIsDirty = computed(() =>
  analyticsFields.auto_sync_enabled !== (config.value?.analytics?.sqlite?.auto_sync_enabled ?? true) ||
  analyticsFields.sync_debounce_seconds !== (config.value?.analytics?.sqlite?.sync_debounce_seconds ?? 5.0) ||
  analyticsFields.enable_wal !== (config.value?.analytics?.sqlite?.enable_wal ?? true)
)

function initAnalyticsFields() {
  analyticsFields.auto_sync_enabled = config.value?.analytics?.sqlite?.auto_sync_enabled ?? true
  analyticsFields.sync_debounce_seconds = config.value?.analytics?.sqlite?.sync_debounce_seconds ?? 5.0
  analyticsFields.enable_wal = config.value?.analytics?.sqlite?.enable_wal ?? true
}

async function saveAnalytics() {
  await saveSection({
    analytics: { sqlite: { ...analyticsFields } },
  }, analyticsSaving, analyticsError)
}

function resetAnalytics() { initAnalyticsFields(); analyticsError.value = '' }

// ─── Backup section ───────────────────────────────────────────────────────────

const backupFields = reactive({
  enabled: config.value?.backup?.enabled ?? true,
  retention_count: config.value?.backup?.retention_count ?? 100,
  cleanup_on_exceed: config.value?.backup?.cleanup_on_exceed ?? true,
})
const backupSaving = ref(false)
const backupError = ref('')

const backupIsDirty = computed(() =>
  backupFields.enabled !== (config.value?.backup?.enabled ?? true) ||
  backupFields.retention_count !== (config.value?.backup?.retention_count ?? 100) ||
  backupFields.cleanup_on_exceed !== (config.value?.backup?.cleanup_on_exceed ?? true)
)

function initBackupFields() {
  backupFields.enabled = config.value?.backup?.enabled ?? true
  backupFields.retention_count = config.value?.backup?.retention_count ?? 100
  backupFields.cleanup_on_exceed = config.value?.backup?.cleanup_on_exceed ?? true
}

async function saveBackup() {
  await saveSection({ backup: { ...backupFields } }, backupSaving, backupError)
}

function resetBackup() { initBackupFields(); backupError.value = '' }

// ─── Logging section ──────────────────────────────────────────────────────────

const loggingFields = reactive({
  level: config.value?.logging?.level ?? 'INFO',
})
const loggingSaving = ref(false)
const loggingError = ref('')

const loggingIsDirty = computed(() =>
  loggingFields.level !== (config.value?.logging?.level ?? 'INFO')
)

function initLoggingFields() {
  loggingFields.level = config.value?.logging?.level ?? 'INFO'
}

async function saveLogging() {
  await saveSection({ logging: { ...loggingFields } }, loggingSaving, loggingError)
}

function resetLogging() { initLoggingFields(); loggingError.value = '' }

// ─── Server section ───────────────────────────────────────────────────────────

const serverFields = reactive({
  host: config.value?.server?.host ?? '127.0.0.1',
  port: config.value?.server?.port ?? 8000,
})
const serverSaving = ref(false)
const serverError = ref('')

const serverIsDirty = computed(() =>
  serverFields.host !== (config.value?.server?.host ?? '127.0.0.1') ||
  serverFields.port !== (config.value?.server?.port ?? 8000)
)

function initServerFields() {
  serverFields.host = config.value?.server?.host ?? '127.0.0.1'
  serverFields.port = config.value?.server?.port ?? 8000
}

async function saveServer() {
  await saveSection({ server: { ...serverFields } }, serverSaving, serverError)
}

function resetServer() { initServerFields(); serverError.value = '' }

// ─── Sync all sections when config is externally updated ─────────────────────
// (e.g. after saving via the YAML editor tab)

watch(config, () => {
  if (!dataIsDirty.value) initDataFields()
  if (!accountsIsDirty.value) initAccountsFields()
  if (!llmIsDirty.value) initLlmFields()
  if (!categorizationIsDirty.value) initCategorizationFields()
  if (!emailIsDirty.value) initEmailFields()
  if (!analyticsIsDirty.value) initAnalyticsFields()
  if (!backupIsDirty.value) initBackupFields()
  if (!loggingIsDirty.value) initLoggingFields()
  if (!serverIsDirty.value) initServerFields()
}, { deep: true })
</script>
