<template>
  <div class="space-y-4 bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-white/10 p-4">
    <div class="border-b border-gray-200 dark:border-white/10 pb-2">
      <h3 class="text-lg font-semibold text-gray-900 dark:text-white">Ledger Utilities</h3>
    </div>

    <div class="flex gap-3">
      <button
        @click="sortLedger"
        :disabled="isProcessing"
        class="flex items-center gap-2 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke-width="1.5"
          stroke="currentColor"
          class="w-4 h-4"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M3 7.5L7.5 3m0 0L12 7.5M7.5 3v13.5m13.5 0L16.5 21m0 0L12 16.5m4.5 4.5V7.5"
          />
        </svg>
        Sort Chronologically
      </button>

      <button
        @click="validateLedger"
        :disabled="isProcessing"
        class="flex items-center gap-2 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke-width="1.5"
          stroke="currentColor"
          class="w-4 h-4"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
        Validate Syntax
      </button>
    </div>

    <!-- Validation Results Panel -->
    <div v-if="validationResults" class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-white/10 p-4 space-y-3">
      <div class="flex items-center justify-between pb-2 border-b border-gray-200 dark:border-white/10">
        <span class="font-medium text-gray-900 dark:text-white">Validation Results</span>
        <span
          :class="
            validationResults.valid
              ? 'px-2 py-1 text-xs font-medium rounded bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200'
              : 'px-2 py-1 text-xs font-medium rounded bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200'
          "
        >
          {{ validationResults.summary }}
        </span>
      </div>

      <!-- Errors -->
      <div v-if="validationResults.errors.length > 0" class="space-y-2">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Errors</div>
        <div
          v-for="error in validationResults.errors"
          :key="error.line"
          class="flex flex-col gap-1 p-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800"
        >
          <span class="text-xs font-mono font-medium text-red-700 dark:text-red-300">Line {{ error.line }}</span>
          <span class="text-sm text-red-600 dark:text-red-400">{{ error.message }}</span>
        </div>
      </div>

      <!-- Warnings -->
      <div v-if="validationResults.warnings.length > 0" class="space-y-2">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Warnings</div>
        <div
          v-for="warning in validationResults.warnings"
          :key="warning.line"
          class="flex flex-col gap-1 p-2 rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800"
        >
          <span class="text-xs font-mono font-medium text-yellow-700 dark:text-yellow-300">Line {{ warning.line }}</span>
          <span class="text-sm text-yellow-600 dark:text-yellow-400">{{ warning.message }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { LedgerService } from '@/services/generated-api'
import type { LedgerValidationResponse } from '@/services/generated-api'
import { useToast } from '@/composables/useNotifications'

interface Props {
  content: string
}

interface Emits {
  (e: 'transformed', content: string): void
  (e: 'reload-requested'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()
const toast = useToast()

const isProcessing = ref(false)
const validationResults = ref<LedgerValidationResponse | null>(null)

async function sortLedger() {
  isProcessing.value = true
  validationResults.value = null

  try {
    const response = await LedgerService.sortLedgerApiLedgerSortPost({
      content: props.content,
    })

    if (response.success && response.data) {
      toast.success('Success', response.data.summary)
      // Backend saved the sorted content, so request reload
      emit('reload-requested')
    } else {
      toast.error('Error', response.error?.message || 'Failed to sort ledger')
    }
  } catch (error: any) {
    const errorMsg = error?.body?.error?.message || error?.message || 'Failed to sort ledger'
    toast.error('Error', errorMsg)
  } finally {
    isProcessing.value = false
  }
}

async function validateLedger() {
  isProcessing.value = true
  validationResults.value = null

  try {
    const response = await LedgerService.validateLedgerApiLedgerValidatePost({
      content: props.content,
    })

    if (response.success && response.data) {
      validationResults.value = response.data

      if (response.data.valid) {
        toast.success('Success', 'Ledger is valid!')
      } else {
        toast.warning('Validation Issues', response.data.summary)
      }
    } else {
      toast.error('Error', response.error?.message || 'Validation failed')
    }
  } catch (error: any) {
    const errorMsg = error?.body?.error?.message || error?.message || 'Validation failed'
    toast.error('Error', errorMsg)
  } finally {
    isProcessing.value = false
  }
}
</script>
