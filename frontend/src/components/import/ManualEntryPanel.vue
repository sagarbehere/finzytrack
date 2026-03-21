<template>
  <div>
    <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
      Describe a transaction in plain language and let it be parsed automatically, or add a blank row to fill in manually.
    </p>

    <!-- Natural language input -->
    <div class="mb-4">
      <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
        Describe a transaction
      </label>
      <p class="text-xs text-gray-400 dark:text-gray-500 mb-1">
        Use $ or ₹ to hint the currency, or select one below for the default.
      </p>
      <p v-if="!config?.ai?.llm?.api_url" class="text-xs text-amber-600 dark:text-amber-400 mb-1">
        LLM not configured — basic parsing only. Set <code class="font-mono">ai.llm.api_url</code> in <code class="font-mono">config.yaml</code> to enable full natural language parsing.
      </p>
      <textarea
        v-model="nlText"
        placeholder="e.g. Paid $45 for dinner at Olive Garden on chase"
        rows="2"
        class="w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
        @keydown.meta.enter="handleParseAndAdd"
        @keydown.ctrl.enter="handleParseAndAdd"
      />
      <button
        @click="handleParseAndAdd"
        :disabled="!nlText.trim() || isParsing"
        class="mt-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
      >
        <svg v-if="isParsing" class="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>{{ isParsing ? 'Parsing...' : 'Parse & Add' }}</span>
      </button>
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
      class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
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
  import { useAccounts } from '@/composables/useAccounts'
  import { useCommodities } from '@/composables/useCommodities'
  import { useToast } from '@/composables/useNotifications'
  import { useConfig } from '@/composables/useConfig'

  const { error: showErrorToast } = useToast()
  const { config } = useConfig()
  const { accountNames } = useAccounts()
  const { commodityCodes } = useCommodities()

  const emit = defineEmits<{
    addTransaction: [payload: { account: string; currency: string; parsed?: ParsedTransaction; scrollToResult: boolean }]
  }>()

  const account = ref('')
  const currency = ref('')
  const nlText = ref('')
  const isParsing = ref(false)

  const handleParseAndAdd = async () => {
    if (!nlText.value.trim() || isParsing.value) return

    isParsing.value = true
    try {
      const llm = config.value?.ai?.llm
      const parsed = await parseNaturalLanguageTransaction(
        nlText.value.trim(),
        {
          accountNames: accountNames.value,
          currencies: commodityCodes.value,
          defaultAccount: account.value || undefined,
          defaultCurrency: currency.value || undefined,
        },
        llm?.api_url ? { apiUrl: llm.api_url, apiKey: llm.api_key || undefined, model: llm.model || undefined, temperature: llm.temperature, maxTokens: llm.max_tokens } : undefined,
      )
      emit('addTransaction', {
        account: account.value,
        currency: currency.value,
        parsed,
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
