<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Import Financial Data</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        Import transactions from OFX files, CSV files, or natural language
      </p>
    </div>

    <!-- Import tabs -->
    <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
      <div class="border-b border-gray-200 dark:border-gray-700">
        <nav class="-mb-px flex space-x-8 px-6" aria-label="Tabs">
          <button
            @click="activeTab = 'ofx'"
            :class="[
              activeTab === 'ofx'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            OFX Import
          </button>
          <button
            @click="activeTab = 'csv'"
            :class="[
              activeTab === 'csv'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            CSV Import
          </button>
          <button
            @click="activeTab = 'natural'"
            :class="[
              activeTab === 'natural'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
              'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium',
            ]"
          >
            Natural Language
          </button>
        </nav>
      </div>

      <div class="p-6">
        <!-- OFX Import Tab -->
        <div v-if="activeTab === 'ofx'">
          <OFXFilePicker
            @fileSelected="handleFileSelected"
            @fileCleared="handleFileCleared"
            @parseError="handleParseError"
          />
        </div>

        <!-- CSV Import Tab (placeholder) -->
        <div v-else-if="activeTab === 'csv'" class="text-center py-12">
          <div class="text-gray-400 dark:text-gray-500 text-6xl mb-4">📊</div>
          <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-2">CSV Import</h3>
          <p class="text-gray-600 dark:text-gray-400 mb-4">
            Import transactions from CSV files with flexible column mapping
          </p>
          <div class="text-sm text-gray-500 dark:text-gray-400">
            <p><strong>Coming Soon:</strong></p>
            <ul class="mt-2 text-left max-w-md mx-auto space-y-1">
              <li>• Auto-detect CSV format and encoding</li>
              <li>• Visual column mapping interface</li>
              <li>• Save mapping templates for repeated imports</li>
              <li>• Support for custom date formats</li>
            </ul>
          </div>
        </div>

        <!-- Natural Language Import Tab (placeholder) -->
        <div v-else-if="activeTab === 'natural'" class="text-center py-12">
          <div class="text-gray-400 dark:text-gray-500 text-6xl mb-4">🗣️</div>
          <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-2">
            Natural Language Import
          </h3>
          <p class="text-gray-600 dark:text-gray-400 mb-4">
            Enter transactions using natural language or voice input
          </p>
          <div class="text-sm text-gray-500 dark:text-gray-400">
            <p><strong>Coming Soon:</strong></p>
            <ul class="mt-2 text-left max-w-md mx-auto space-y-1">
              <li>• Voice-to-text transaction entry</li>
              <li>• Natural language parsing ("Coffee $5 yesterday")</li>
              <li>• Multi-transaction batch entry</li>
              <li>• Smart date and amount recognition</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
  import { ref } from 'vue'
  import OFXFilePicker from '@/components/import/OFXFilePicker.vue'

  // Tab state
  const activeTab = ref('ofx')

  // File handling state
  const selectedFileInfo = ref(null)

  // Event handlers
  const handleFileSelected = (data) => {
    selectedFileInfo.value = {
      fileName: data.file.name,
      fileSize: data.file.size,
      details: data.details,
    }
  }

  const handleFileCleared = () => {
    selectedFileInfo.value = null
  }

  const handleParseError = (error) => {
    console.error('Parse error:', error)
    selectedFileInfo.value = null
  }
</script>
