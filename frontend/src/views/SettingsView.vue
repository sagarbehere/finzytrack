<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">Application preferences and configuration</p>
    </div>

    <!-- Restart required banner — shown above all tabs once triggered -->
    <div
      v-if="restartRequired"
      class="mb-6 flex items-start gap-3 rounded-md bg-yellow-50 p-4 text-sm dark:bg-yellow-500/10 dark:outline dark:outline-yellow-500/15"
    >
      <svg class="mt-0.5 h-4 w-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
      </svg>
      <span>Some changes require an app restart to take effect. Please restart the application.</span>
    </div>

    <!-- Tab Navigation -->
    <div class="border-b border-gray-200 dark:border-white/10 mb-6">
      <nav class="-mb-px flex space-x-8">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="activeTab = tab.id"
          :class="[
            activeTab === tab.id
              ? 'border-indigo-500 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
            'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
          ]"
        >
          {{ tab.label }}
        </button>
      </nav>
    </div>

    <!-- Tab Content -->
    <div>

      <!-- General Tab -->
      <GeneralSettingsTab
        v-if="activeTab === 'general'"
        @restart-required="restartRequired = true"
      />

      <!-- Appearance Tab -->
      <div v-if="activeTab === 'appearance'" class="space-y-6">
        <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
          <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10">
            <h3 class="text-lg font-medium text-gray-900 dark:text-white">Appearance</h3>
            <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
              Customize the look and feel of the application
            </p>
          </div>
          <div class="p-6">
            <div class="space-y-4">
              <div>
                <label class="text-base font-medium text-gray-900 dark:text-white">Theme</label>
                <p class="text-sm text-gray-500 dark:text-gray-400">Choose your preferred color scheme</p>
                <fieldset class="mt-4">
                  <legend class="sr-only">Theme selection</legend>
                  <div class="space-y-4">
                    <div v-for="option in themeOptions" :key="option.id" class="flex items-center">
                      <input
                        :id="option.id"
                        name="theme"
                        type="radio"
                        :checked="option.id === 'theme-system'"
                        class="relative size-4 appearance-none rounded-full border border-gray-300 bg-white before:absolute before:inset-1 before:rounded-full before:bg-white not-checked:before:hidden checked:border-indigo-600 checked:bg-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:border-white/10 dark:bg-white/5 dark:checked:border-indigo-500 dark:checked:bg-indigo-500 dark:focus-visible:outline-indigo-500"
                      />
                      <label :for="option.id" class="ml-3 block text-sm/6 font-medium text-gray-900 dark:text-white">{{ option.label }}</label>
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
        <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 p-6">
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
            @restart-required="restartRequired = true"
            @error="handleError"
          />
        </div>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import FileEditor from '@/components/common/FileEditor.vue'
import GeneralSettingsTab from '@/components/settings/GeneralSettingsTab.vue'
import type { Config } from '@/services/generated-api'
import { useConfig } from '@/composables/useConfig'

const tabs = [
  { id: 'general', label: 'General' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'config', label: 'Configuration File' },
]

const activeTab = ref('general')

const themeOptions = [
  { id: 'theme-system', label: 'System (Auto)' },
  { id: 'theme-light', label: 'Light' },
  { id: 'theme-dark', label: 'Dark' },
]
const { updateConfig } = useConfig()
const restartRequired = ref(false)

function handleConfigSaved(updatedConfig: Config) {
  updateConfig(updatedConfig)
}

function handleError(errorMsg: string) {
  console.error('Config editor error:', errorMsg)
}
</script>
