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
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Config File</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">The configuration file currently in use.</p>
        <p class="text-sm font-mono text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-900 px-3 py-2 rounded-md border border-gray-200 dark:border-gray-700">
          {{ config.config_file_path }}
        </p>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Ledger File</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Path to your Beancount ledger file. Can be absolute or relative to the directory the backend was started from.
        </p>
        <input v-model="dataFields.ledger_file" type="text" placeholder="e.g. data/ledger.beancount" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">OFX Mappings File</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Path to the YAML file containing OFX account mappings. Leave empty to disable.</p>
        <input v-model="dataFields.ofx_mappings_file" type="text" placeholder="e.g. config/ofx_mappings.yaml" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">CSV Rules Directory</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Directory containing CSV import rule YAML files. Leave empty to disable.</p>
        <input v-model="dataFields.csv_rules_dir" type="text" placeholder="e.g. config/csv_rules/" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">XLS Rules Directory</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Directory containing XLS import rule YAML files. Leave empty to disable.</p>
        <input v-model="dataFields.xls_rules_dir" type="text" placeholder="e.g. config/xls_rules/" :class="inputClass" />
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
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default Currency</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Currency code used when no currency is specified.</p>
        <input v-model="accountsFields.default_currency" type="text" placeholder="USD" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default Unknown Account</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Beancount account used when a transaction cannot be categorized.</p>
        <input v-model="accountsFields.default_unknown_account" type="text" placeholder="Expenses:Unknown" :class="inputClass" />
      </div>
    </SettingsSection>

    <!-- ── AI / LLM ───────────────────────────────────────────────────────── -->
    <SettingsSection
      title="AI / LLM"
      description="Language model settings for natural language transaction entry and query generation."
      :is-dirty="llmIsDirty"
      :is-saving="llmSaving"
      :error="llmError"
      @save="saveLlm"
      @reset="resetLlm"
    >
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Provider</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Use <strong>OpenAI-compatible</strong> for local models (LM Studio, Ollama), OpenAI, or Groq.
          Use <strong>Anthropic</strong> for the Anthropic API directly.
        </p>
        <select v-model="llmFields.provider" :class="inputClass">
          <option value="openai">OpenAI-compatible</option>
          <option value="anthropic">Anthropic</option>
        </select>
      </div>

      <div v-if="llmFields.provider === 'openai'">
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API URL</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Base URL of the OpenAI-compatible endpoint, e.g. <code class="font-mono">http://127.0.0.1:1234</code> or <code class="font-mono">https://api.openai.com</code>.
        </p>
        <input v-model="llmFields.api_url" type="text" placeholder="http://127.0.0.1:1234" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
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
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Model name, e.g. <code class="font-mono">gpt-4o</code>, <code class="font-mono">claude-sonnet-4-6</code>, <code class="font-mono">llama-3.1-8b-instruct</code>.
        </p>
        <input v-model="llmFields.model" type="text" placeholder="gpt-4o" :class="inputClass" />
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Temperature</label>
          <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">0 = deterministic, 2 = very random.</p>
          <input v-model.number="llmFields.temperature" type="number" min="0" max="2" step="0.1" :class="inputClass" />
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Tokens</label>
          <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Maximum tokens in the LLM response.</p>
          <input v-model.number="llmFields.max_tokens" type="number" min="1" step="256" :class="inputClass" />
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
      <div class="flex items-center gap-3">
        <input id="cat-enabled" v-model="categorizationFields.enabled" type="checkbox" :class="checkboxClass" />
        <label for="cat-enabled" class="text-sm font-medium text-gray-700 dark:text-gray-300">Enable auto-categorization</label>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Engine</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          <strong>Classifier</strong> trains on your existing ledger history.
          <strong>LLM</strong> uses your configured language model (requires AI / LLM to be set up).
        </p>
        <select v-model="categorizationFields.engine" :class="inputClass">
          <option value="classifier">Classifier (scikit-learn)</option>
          <option value="llm">LLM</option>
        </select>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Training Data File</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Path to a Beancount file used as training data for the classifier.</p>
        <input v-model="categorizationFields.training_data_file" type="text" placeholder="data/training.beancount" :class="inputClass" />
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
      <div class="flex items-center gap-3">
        <input id="email-enabled" v-model="emailFields.enabled" type="checkbox" :class="checkboxClass" />
        <label for="email-enabled" class="text-sm font-medium text-gray-700 dark:text-gray-300">Enable email import</label>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Rules Directory</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Directory containing per-account YAML rule files.</p>
        <input v-model="emailFields.rules_directory" type="text" placeholder="./config/email_rules/" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default Lookback Days</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Number of days to look back for emails when no date range is specified.</p>
        <input v-model.number="emailFields.default_lookback_days" type="number" min="1" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Emails</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Maximum emails to fetch per request. Truncates with a warning if exceeded.</p>
        <input v-model.number="emailFields.max_emails" type="number" min="1" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">IMAP Timeout (seconds)</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Socket timeout for IMAP operations. Set to 0 for no timeout.</p>
        <input v-model.number="emailFields.imap_timeout_secs" type="number" min="0" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Parsing Mode</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          <strong>Regex</strong> uses patterns defined in rule files.
          <strong>LLM</strong> uses your configured language model (requires AI / LLM to be set up).
          Can be overridden per account or per rule.
        </p>
        <select v-model="emailFields.parsing_mode" :class="inputClass">
          <option value="regex">Regex</option>
          <option value="llm">LLM</option>
        </select>
      </div>
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
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">SQLite Export Path</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Path to the SQLite database file exported from your ledger.</p>
        <input v-model="analyticsFields.export_path" type="text" placeholder="data/ledger.db" :class="inputClass" />
      </div>

      <div class="flex items-center gap-3">
        <input id="sqlite-autosync" v-model="analyticsFields.auto_sync_enabled" type="checkbox" :class="checkboxClass" />
        <label for="sqlite-autosync" class="text-sm font-medium text-gray-700 dark:text-gray-300">Auto-sync on ledger changes</label>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Sync Debounce (seconds)</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Delay before syncing after a ledger change, to avoid excessive writes.</p>
        <input v-model.number="analyticsFields.sync_debounce_seconds" type="number" min="0" step="0.5" :class="inputClass" />
      </div>

      <div class="flex items-center gap-3">
        <input id="sqlite-wal" v-model="analyticsFields.enable_wal" type="checkbox" :class="checkboxClass" />
        <label for="sqlite-wal" class="text-sm font-medium text-gray-700 dark:text-gray-300">Enable WAL mode (recommended for concurrent access)</label>
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
      <div class="flex items-center gap-3">
        <input id="backup-enabled" v-model="backupFields.enabled" type="checkbox" :class="checkboxClass" />
        <label for="backup-enabled" class="text-sm font-medium text-gray-700 dark:text-gray-300">Enable automatic backups</label>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Backup Directory
          <span class="ml-1 text-xs font-medium text-amber-600 dark:text-amber-400">⚠ restart required</span>
        </label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Directory where backup files are stored.</p>
        <input v-model="backupFields.backup_dir" type="text" placeholder="data/backups" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Retention Count</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Number of backup files to keep.</p>
        <input v-model.number="backupFields.retention_count" type="number" min="1" :class="inputClass" />
      </div>

      <div class="flex items-center gap-3">
        <input id="backup-cleanup" v-model="backupFields.cleanup_on_exceed" type="checkbox" :class="checkboxClass" />
        <label for="backup-cleanup" class="text-sm font-medium text-gray-700 dark:text-gray-300">Automatically remove oldest backups when limit is exceeded</label>
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
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Log Level</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Verbosity of the backend logs. Takes effect immediately.</p>
        <select v-model="loggingFields.level" :class="inputClass">
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Log File
          <span class="ml-1 text-xs font-medium text-amber-600 dark:text-amber-400">⚠ restart required</span>
        </label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Path to the log file.</p>
        <input v-model="loggingFields.file" type="text" placeholder="logs/finzytrack.log" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Log Format</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Python log format string (read-only — edit in the config file directly).</p>
        <p class="text-sm font-mono text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-900 px-3 py-2 rounded-md border border-gray-200 dark:border-gray-700 break-all">
          {{ config?.logging?.format ?? '' }}
        </p>
      </div>
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
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Host</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
          Address the server listens on. Use <code class="font-mono">127.0.0.1</code> for local-only access.
        </p>
        <input v-model="serverFields.host" type="text" placeholder="127.0.0.1" :class="inputClass" />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Port</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">Port the server listens on.</p>
        <input v-model.number="serverFields.port" type="number" min="1" max="65535" :class="inputClass" />
      </div>
    </SettingsSection>

    <!-- ── Security ───────────────────────────────────────────────────────── -->
    <SettingsSection
      title="Security"
      description="CORS and access control settings."
      :requires-restart="true"
      :is-dirty="securityIsDirty"
      :is-saving="securitySaving"
      :error="securityError"
      @save="saveSecurity"
      @reset="resetSecurity"
    >
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Allowed CORS Origins</label>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">One URL per line. The frontend origin must be listed here when running in development.</p>
        <textarea
          v-model="securityFields.cors_origins"
          rows="4"
          placeholder="http://localhost:3000&#10;http://127.0.0.1:3000"
          :class="[inputClass, 'font-mono']"
        />
      </div>
    </SettingsSection>

  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import SettingsSection from './SettingsSection.vue'
import { useConfig } from '@/composables/useConfig'
import { useToast } from '@/composables/useNotifications'
import { patchConfig } from '@/composables/useConfigPatch'

const emit = defineEmits<{ 'restart-required': [] }>()

const { config, updateConfig } = useConfig()
const toast = useToast()

// ─── Shared UI classes ────────────────────────────────────────────────────────

const inputClass = 'w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm'
const checkboxClass = 'h-4 w-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500'

// ─── Eye toggle for API key ───────────────────────────────────────────────────

const showApiKey = ref(false)

// ─── Section helpers ──────────────────────────────────────────────────────────

async function saveSection(
  patch: Record<string, unknown>,
  saving: { value: boolean },
  error: { value: string },
) {
  saving.value = true
  error.value = ''
  try {
    const result = await patchConfig(patch)
    updateConfig(result.config)
    if (result.restart_required) emit('restart-required')
    toast.success('Saved', 'Settings saved successfully')
  } catch (e: any) {
    error.value = e.message ?? 'Failed to save'
  } finally {
    saving.value = false
  }
}

// ─── Data section ─────────────────────────────────────────────────────────────

const dataFields = reactive({
  ledger_file: config.value?.ledger_file ?? '',
  ofx_mappings_file: config.value?.ofx_mappings_file ?? '',
  csv_rules_dir: config.value?.csv_rules_dir ?? '',
  xls_rules_dir: config.value?.xls_rules_dir ?? '',
})
const dataSaving = ref(false)
const dataError = ref('')

const dataIsDirty = computed(() =>
  dataFields.ledger_file !== (config.value?.ledger_file ?? '') ||
  dataFields.ofx_mappings_file !== (config.value?.ofx_mappings_file ?? '') ||
  dataFields.csv_rules_dir !== (config.value?.csv_rules_dir ?? '') ||
  dataFields.xls_rules_dir !== (config.value?.xls_rules_dir ?? '')
)

function initDataFields() {
  dataFields.ledger_file = config.value?.ledger_file ?? ''
  dataFields.ofx_mappings_file = config.value?.ofx_mappings_file ?? ''
  dataFields.csv_rules_dir = config.value?.csv_rules_dir ?? ''
  dataFields.xls_rules_dir = config.value?.xls_rules_dir ?? ''
}

async function saveData() {
  await saveSection({
    ledger_file: dataFields.ledger_file,
    ofx_mappings_file: dataFields.ofx_mappings_file || null,
    csv_rules_dir: dataFields.csv_rules_dir || null,
    xls_rules_dir: dataFields.xls_rules_dir || null,
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
  max_tokens: config.value?.ai?.llm?.max_tokens ?? 2048,
})
const llmSaving = ref(false)
const llmError = ref('')

const llmIsDirty = computed(() =>
  llmFields.provider !== (config.value?.ai?.llm?.provider ?? 'openai') ||
  llmFields.api_url !== (config.value?.ai?.llm?.api_url ?? '') ||
  llmFields.api_key !== (config.value?.ai?.llm?.api_key ?? '') ||
  llmFields.model !== (config.value?.ai?.llm?.model ?? '') ||
  llmFields.temperature !== (config.value?.ai?.llm?.temperature ?? 0.1) ||
  llmFields.max_tokens !== (config.value?.ai?.llm?.max_tokens ?? 2048)
)

function initLlmFields() {
  llmFields.provider = config.value?.ai?.llm?.provider ?? 'openai'
  llmFields.api_url = config.value?.ai?.llm?.api_url ?? ''
  llmFields.api_key = config.value?.ai?.llm?.api_key ?? ''
  llmFields.model = config.value?.ai?.llm?.model ?? ''
  llmFields.temperature = config.value?.ai?.llm?.temperature ?? 0.1
  llmFields.max_tokens = config.value?.ai?.llm?.max_tokens ?? 2048
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
  rules_directory: config.value?.email_import?.rules_directory ?? './config/email_rules/',
  default_lookback_days: config.value?.email_import?.default_lookback_days ?? 7,
  max_emails: config.value?.email_import?.max_emails ?? 500,
  imap_timeout_secs: config.value?.email_import?.imap_timeout_secs ?? 30,
  parsing_mode: config.value?.email_import?.parsing_mode ?? 'regex',
})
const emailSaving = ref(false)
const emailError = ref('')

const emailIsDirty = computed(() =>
  emailFields.enabled !== (config.value?.email_import?.enabled ?? false) ||
  emailFields.rules_directory !== (config.value?.email_import?.rules_directory ?? './config/email_rules/') ||
  emailFields.default_lookback_days !== (config.value?.email_import?.default_lookback_days ?? 7) ||
  emailFields.max_emails !== (config.value?.email_import?.max_emails ?? 500) ||
  emailFields.imap_timeout_secs !== (config.value?.email_import?.imap_timeout_secs ?? 30) ||
  emailFields.parsing_mode !== (config.value?.email_import?.parsing_mode ?? 'regex')
)

function initEmailFields() {
  emailFields.enabled = config.value?.email_import?.enabled ?? false
  emailFields.rules_directory = config.value?.email_import?.rules_directory ?? './config/email_rules/'
  emailFields.default_lookback_days = config.value?.email_import?.default_lookback_days ?? 7
  emailFields.max_emails = config.value?.email_import?.max_emails ?? 500
  emailFields.imap_timeout_secs = config.value?.email_import?.imap_timeout_secs ?? 30
  emailFields.parsing_mode = config.value?.email_import?.parsing_mode ?? 'regex'
}

async function saveEmail() {
  await saveSection({
    email_import: {
      enabled: emailFields.enabled,
      rules_directory: emailFields.rules_directory,
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
  export_path: config.value?.analytics?.sqlite?.export_path ?? '',
  auto_sync_enabled: config.value?.analytics?.sqlite?.auto_sync_enabled ?? true,
  sync_debounce_seconds: config.value?.analytics?.sqlite?.sync_debounce_seconds ?? 5.0,
  enable_wal: config.value?.analytics?.sqlite?.enable_wal ?? true,
})
const analyticsSaving = ref(false)
const analyticsError = ref('')

const analyticsIsDirty = computed(() =>
  analyticsFields.export_path !== (config.value?.analytics?.sqlite?.export_path ?? '') ||
  analyticsFields.auto_sync_enabled !== (config.value?.analytics?.sqlite?.auto_sync_enabled ?? true) ||
  analyticsFields.sync_debounce_seconds !== (config.value?.analytics?.sqlite?.sync_debounce_seconds ?? 5.0) ||
  analyticsFields.enable_wal !== (config.value?.analytics?.sqlite?.enable_wal ?? true)
)

function initAnalyticsFields() {
  analyticsFields.export_path = config.value?.analytics?.sqlite?.export_path ?? ''
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
  backup_dir: config.value?.backup?.backup_dir ?? '',
  retention_count: config.value?.backup?.retention_count ?? 100,
  cleanup_on_exceed: config.value?.backup?.cleanup_on_exceed ?? true,
})
const backupSaving = ref(false)
const backupError = ref('')

const backupIsDirty = computed(() =>
  backupFields.enabled !== (config.value?.backup?.enabled ?? true) ||
  backupFields.backup_dir !== (config.value?.backup?.backup_dir ?? '') ||
  backupFields.retention_count !== (config.value?.backup?.retention_count ?? 100) ||
  backupFields.cleanup_on_exceed !== (config.value?.backup?.cleanup_on_exceed ?? true)
)

function initBackupFields() {
  backupFields.enabled = config.value?.backup?.enabled ?? true
  backupFields.backup_dir = config.value?.backup?.backup_dir ?? ''
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
  file: config.value?.logging?.file ?? '',
})
const loggingSaving = ref(false)
const loggingError = ref('')

const loggingIsDirty = computed(() =>
  loggingFields.level !== (config.value?.logging?.level ?? 'INFO') ||
  loggingFields.file !== (config.value?.logging?.file ?? '')
)

function initLoggingFields() {
  loggingFields.level = config.value?.logging?.level ?? 'INFO'
  loggingFields.file = config.value?.logging?.file ?? ''
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

// ─── Security section ─────────────────────────────────────────────────────────

const securityFields = reactive({
  cors_origins: (config.value?.security?.cors_origins ?? []).join('\n'),
})
const securitySaving = ref(false)
const securityError = ref('')

const securityIsDirty = computed(() =>
  securityFields.cors_origins !== (config.value?.security?.cors_origins ?? []).join('\n')
)

function initSecurityFields() {
  securityFields.cors_origins = (config.value?.security?.cors_origins ?? []).join('\n')
}

async function saveSecurity() {
  const cors_origins = securityFields.cors_origins.split('\n').map(s => s.trim()).filter(Boolean)
  await saveSection({ security: { cors_origins } }, securitySaving, securityError)
}

function resetSecurity() { initSecurityFields(); securityError.value = '' }

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
  if (!securityIsDirty.value) initSecurityFields()
}, { deep: true })
</script>
