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
          @click="activeTab = 'appearance'"
          :class="[
            activeTab === 'appearance'
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
            'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors',
          ]"
        >
          Appearance
        </button>

        <button
          @click="activeTab = 'config'"
          :class="[
            activeTab === 'config'
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
            'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors',
          ]"
        >
          Configuration File
        </button>

        <button
          @click="activeTab = 'advanced'"
          :class="[
            activeTab === 'advanced'
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
            'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors',
          ]"
        >
          Advanced
        </button>
      </nav>
    </div>

    <!-- Tab Content -->
    <div class="tab-content">
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

      <!-- Advanced Tab -->
      <div v-if="activeTab === 'advanced'">
        <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 p-6">
          <div class="text-center py-12">
            <div class="text-gray-400 dark:text-gray-500 text-4xl mb-4">⚙️</div>
            <p class="text-gray-500 dark:text-gray-400">
              Advanced settings coming soon
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import FileEditor from '@/components/common/FileEditor.vue'
import type { Config } from '@/services/generated-api'
import { useConfig } from '@/composables/useConfig'
import { useToast } from '@/composables/useNotifications'

const activeTab = ref('config')
const { updateConfig } = useConfig()
const toast = useToast()

function handleConfigSaved(updatedConfig: Config) {
  // Update global config cache (used throughout the app)
  updateConfig(updatedConfig)

  // Toast already shown by FileEditor, but we could add additional logic here if needed
}

function handleRestartRequired(reason: string) {
  // Show warning about restart
  toast.warning('Restart Required', `Config saved. ${reason}. Please restart the backend.`)
}

function handleError(errorMsg: string) {
  // Error toast already shown by FileEditor
  console.error('Config editor error:', errorMsg)
}
</script>
