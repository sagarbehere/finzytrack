<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">Application preferences and configuration</p>
    </div>

    <!-- Restart required banner -->
    <div
      v-if="restartRequired"
      class="mb-6 flex items-start gap-3 rounded-md bg-yellow-50 p-4 text-sm dark:bg-yellow-500/10 dark:outline dark:outline-yellow-500/15"
    >
      <svg class="mt-0.5 h-4 w-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
      </svg>
      <span>Some changes require an app restart to take effect. Please restart the application.</span>
    </div>

    <GeneralSettingsTab @restart-required="restartRequired = true" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import GeneralSettingsTab from '@/components/settings/GeneralSettingsTab.vue'

const restartRequired = ref(false)
</script>
