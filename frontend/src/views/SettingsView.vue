<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">Application preferences and configuration</p>
    </div>

    <!-- Tab Navigation -->
    <div class="border-b border-gray-200 dark:border-gray-700 mb-6">
      <nav class="-mb-px flex space-x-8">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="activeTab = tab.id"
          :class="[
            activeTab === tab.id
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
            'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors',
          ]"
        >
          {{ tab.label }}
        </button>
      </nav>
    </div>

    <!-- Tab Content -->
    <div class="tab-content">

      <!-- General Tab -->
      <div v-if="activeTab === 'general'" class="space-y-6">

        <!-- Data section -->
        <SettingsSection
          title="Data"
          description="Configure where your financial data is stored."
          :is-dirty="ledgerIsDirty"
          :is-saving="ledgerIsSaving"
          :error="ledgerError"
          @save="saveLedgerSection"
          @reset="resetLedgerSection"
        >
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Ledger File
            </label>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
              Path to your Beancount ledger file. Can be absolute or relative to the directory
              the backend was started from.
            </p>
            <input
              v-model="ledgerFile"
              type="text"
              placeholder="e.g. data/ledger.beancount"
              class="w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm font-mono"
            />
          </div>
        </SettingsSection>

      </div>

      <!-- Appearance Tab -->
      <div v-if="activeTab === 'appearance'" class="space-y-6">
        <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
          <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h3 class="text-lg font-medium text-gray-900 dark:text-white">Appearance</h3>
            <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
              Customize the look and feel of the application
            </p>
          </div>
          <div class="p-6">
            <div class="space-y-4">
              <div>
                <label class="text-base font-medium text-gray-900 dark:text-white"> Theme </label>
                <p class="text-sm text-gray-500 dark:text-gray-400">
                  Choose your preferred color scheme
                </p>
                <fieldset class="mt-4">
                  <legend class="sr-only">Theme selection</legend>
                  <div class="space-y-4">
                    <div class="flex items-center">
                      <input
                        id="theme-system"
                        name="theme"
                        type="radio"
                        checked
                        class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-gray-300"
                      />
                      <label
                        for="theme-system"
                        class="ml-3 block text-sm font-medium text-gray-700 dark:text-gray-300"
                      >
                        System (Auto)
                      </label>
                    </div>
                    <div class="flex items-center">
                      <input
                        id="theme-light"
                        name="theme"
                        type="radio"
                        class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-gray-300"
                      />
                      <label
                        for="theme-light"
                        class="ml-3 block text-sm font-medium text-gray-700 dark:text-gray-300"
                      >
                        Light
                      </label>
                    </div>
                    <div class="flex items-center">
                      <input
                        id="theme-dark"
                        name="theme"
                        type="radio"
                        class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-gray-300"
                      />
                      <label
                        for="theme-dark"
                        class="ml-3 block text-sm font-medium text-gray-700 dark:text-gray-300"
                      >
                        Dark
                      </label>
                    </div>
                  </div>
                </fieldset>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Configuration File Tab -->
      <div v-if="activeTab === 'config'">
        <!-- Restart required banner — sticky once shown -->
        <div
          v-if="restartRequired"
          class="mb-4 flex items-start gap-3 rounded-lg border border-amber-300 dark:border-amber-600 bg-amber-50 dark:bg-amber-900/30 px-4 py-3 text-sm text-amber-800 dark:text-amber-300"
        >
          <svg class="mt-0.5 h-4 w-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
          </svg>
          <span>Some changes require an app restart to take effect. Please restart the application.</span>
        </div>

        <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 p-6">
          <div class="mb-4">
            <h3 class="text-lg font-medium text-gray-900 dark:text-white">Configuration File</h3>
            <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
              Edit your configuration file directly. Changes to most settings take effect immediately.
              Some settings (server, security) require a restart.
            </p>
          </div>

          <FileEditor
            file-type="config"
            :allow-edit="true"
            @saved="handleConfigSaved"
            @restart-required="handleRestartRequired"
            @error="handleError"
          />
        </div>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import FileEditor from '@/components/common/FileEditor.vue'
import SettingsSection from '@/components/settings/SettingsSection.vue'
import type { Config } from '@/services/generated-api'
import { useConfig } from '@/composables/useConfig'
import { useToast } from '@/composables/useNotifications'
import { patchConfig } from '@/composables/useConfigPatch'

const tabs = [
  { id: 'general', label: 'General' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'config', label: 'Configuration File' },
]

const activeTab = ref('general')
const { config, updateConfig } = useConfig()
const toast = useToast()
const restartRequired = ref(false)

// ─── Data section (ledger file) ───────────────────────────────────────────────

const ledgerFile = ref(config.value?.ledger_file ?? '')
const ledgerIsSaving = ref(false)
const ledgerError = ref('')

const ledgerIsDirty = computed(() => ledgerFile.value !== (config.value?.ledger_file ?? ''))

// Keep the field in sync if config changes externally (e.g. via YAML editor save)
watch(() => config.value?.ledger_file, (newVal) => {
  if (!ledgerIsDirty.value) ledgerFile.value = newVal ?? ''
})

async function saveLedgerSection() {
  ledgerIsSaving.value = true
  ledgerError.value = ''
  try {
    const result = await patchConfig({ ledger_file: ledgerFile.value })
    updateConfig(result.config)
    if (result.restart_required) restartRequired.value = true
    toast.success('Saved', 'Settings saved successfully')
  } catch (e: any) {
    ledgerError.value = e.message ?? 'Failed to save'
  } finally {
    ledgerIsSaving.value = false
  }
}

function resetLedgerSection() {
  ledgerFile.value = config.value?.ledger_file ?? ''
  ledgerError.value = ''
}

// ─── Config file editor handlers ──────────────────────────────────────────────

function handleConfigSaved(updatedConfig: Config) {
  updateConfig(updatedConfig)
}

function handleRestartRequired(_reason: string) {
  restartRequired.value = true
}

function handleError(errorMsg: string) {
  console.error('Config editor error:', errorMsg)
}
</script>
