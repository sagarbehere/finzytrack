<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Raw Ledger Data</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        View and edit your Beancount ledger file with syntax validation and utilities
      </p>
    </div>

    <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 p-6">
      <FileEditor
        file-type="ledger"
        :allow-edit="true"
        :show-utilities="true"
        @saved="handleLedgerSaved"
        @error="handleError"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import FileEditor from '@/components/common/FileEditor.vue'

function handleLedgerSaved() {
  // Toast already shown by FileEditor

  // TODO: Consider reloading other parts of the app that depend on ledger
  // For now, user should refresh page to see changes reflected in other views
  console.log('Ledger saved successfully')
}

function handleError(error: string) {
  // Error toast already shown by FileEditor
  console.error('Ledger editor error:', error)
}
</script>
