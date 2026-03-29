<template>
  <div>
    <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
      Describe a transaction in plain language and let it be parsed automatically, or add a blank row to fill in manually.
    </p>

    <!-- Natural language input -->
    <div class="mb-4">
      <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
        Describe a transaction
      </label>
      <p class="text-xs text-gray-400 dark:text-gray-500 mb-1">
        Use $ or ₹ to hint the currency, or select one below for the default.
        AI can make mistakes — review output carefully.
        <a href="https://finzytrack.app/docs/ai-data-sharing" target="_blank" rel="noopener noreferrer" class="text-indigo-500 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 underline underline-offset-2">Data shared with AI</a>
      </p>
      <p v-if="!config?.ai?.llm?.model" class="text-xs text-amber-600 dark:text-amber-400 mb-1">
        AI not configured — basic parsing only. Set a model under Settings → AI to enable full natural language parsing.
      </p>
      <textarea
        v-model="nlText"
        placeholder="e.g. Paid $45 for dinner at Olive Garden on chase"
        rows="2"
        class="w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        @keydown.meta.enter="handleParseAndAdd"
        @keydown.ctrl.enter="handleParseAndAdd"
      />
      <button
        @click="handleParseAndAdd"
        :disabled="!nlText.trim() || isParsing"
        class="mt-2 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
      >
        <svg v-if="isParsing" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>{{ isParsing ? 'Parsing...' : 'Parse & Add' }}</span>
      </button>
      <!-- Validation warnings -->
      <div v-if="parseWarnings.length" class="mt-2 rounded-md border border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-3 py-2">
        <p class="text-xs font-medium text-amber-800 dark:text-amber-300">Review the transaction below — the AI result had issues:</p>
        <ul class="mt-1 list-disc list-inside text-xs text-amber-700 dark:text-amber-400">
          <li v-for="(w, i) in parseWarnings" :key="i">{{ w }}</li>
        </ul>
      </div>
    </div>

    <div class="text-center text-gray-400 text-sm my-3">or add a blank row</div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
      <AccountDropdown
        v-model="account"
        label="Source Account"
        :account-types="['Assets', 'Liabilities']"
        :allow-custom="true"
        placeholder="Select or type account name..."
      />

      <CommodityDropdown
        v-model="currency"
        label="Currency"
        :allow-custom="true"
        placeholder="Select or type currency..."
      />
    </div>

    <button
      @click="addTransaction"
      :disabled="!account || !currency"
      class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      Add Transaction
    </button>
  </div>
</template>

<script setup lang="ts">
  import { ref } from 'vue'
  import AccountDropdown from '@/components/common/AccountDropdown.vue'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import { parseNaturalLanguageTransaction, type ParsedTransaction } from '@/services/nlParser'
  import { useToast } from '@/composables/useNotifications'
  import { useConfig } from '@/composables/useConfig'

  const { error: showErrorToast } = useToast()
  const { config } = useConfig()

  const emit = defineEmits<{
    addTransaction: [payload: { account: string; currency: string; parsed?: ParsedTransaction; scrollToResult: boolean }]
  }>()

  const account = ref('')
  const currency = ref('')
  const nlText = ref('')
  const isParsing = ref(false)
  const parseWarnings = ref<string[]>([])

  const handleParseAndAdd = async () => {
    if (!nlText.value.trim() || isParsing.value) return

    isParsing.value = true
    parseWarnings.value = []
    try {
      const result = await parseNaturalLanguageTransaction(
        nlText.value.trim(),
        currency.value || undefined,
      )
      parseWarnings.value = result.warnings
      emit('addTransaction', {
        account: account.value,
        currency: currency.value,
        parsed: result.transaction,
        scrollToResult: false,
      })
      nlText.value = ''
    } catch (e: any) {
      showErrorToast('Parse Failed', e.message || 'Failed to parse transaction')
    } finally {
      isParsing.value = false
    }
  }

  const addTransaction = () => {
    if (account.value && currency.value) {
      emit('addTransaction', { account: account.value, currency: currency.value, scrollToResult: true })
    }
  }
</script>
