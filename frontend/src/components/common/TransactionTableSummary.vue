<template>
  <div v-if="transactions.length > 0" class="mt-4">
    <div class="card">
      <div class="px-4 py-3">
        <div class="flex items-center justify-around gap-6 text-sm">
          <div v-if="importContext" class="flex items-center gap-2">
            <span class="text-gray-600 dark:text-gray-400">Net Flow:</span>
            <span class="font-semibold text-gray-900 dark:text-white">
              <template v-if="Object.keys(netFlowByCurrency).length > 0">
                <span v-for="(amount, currency, index) in netFlowByCurrency" :key="currency">
                  {{ amount }} {{ currency }}<span v-if="index < Object.keys(netFlowByCurrency).length - 1">, </span>
                </span>
              </template>
              <template v-else>—</template>
            </span>
          </div>
          <div v-if="importContext" class="flex items-center gap-2">
            <span class="text-gray-600 dark:text-gray-400">Potential duplicates:</span>
            <button
              @click="handleDuplicateSummaryClick"
              :class="[
                'font-semibold',
                duplicateCount > 0
                  ? 'text-orange-600 dark:text-orange-400 hover:underline cursor-pointer'
                  : 'text-gray-900 dark:text-white cursor-default'
              ]"
              :disabled="duplicateCount === 0"
            >
              {{ duplicateCount }}
            </button>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-gray-600 dark:text-gray-400">Edited:</span>
            <span class="font-semibold text-gray-900 dark:text-white">{{ editedCount }}</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-gray-600 dark:text-gray-400">Unbalanced:</span>
            <span class="font-semibold" :class="unbalancedCount > 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-white'">{{ unbalancedCount }}</span>
          </div>
        </div>

        <!-- Account sums by currency -->
        <div v-if="Object.keys(accountSumsByCurrency).length > 0" class="mt-3 pt-3 border-t border-gray-200 dark:border-white/10">
          <div class="flex flex-wrap justify-center items-center gap-x-6 gap-y-2 text-sm">
            <span class="text-gray-500 dark:text-gray-400 font-medium">Account Totals:</span>
            <div v-for="(currencies, account) in accountSumsByCurrency" :key="account" class="flex items-center gap-2">
              <span class="text-gray-600 dark:text-gray-400">{{ account }}:</span>
              <span class="font-semibold text-gray-900 dark:text-white">
                <span v-for="(amount, currency, index) in currencies" :key="currency">
                  {{ amount.toFixed(2) }} {{ currency }}<span v-if="index < Object.keys(currencies).length - 1">, </span>
                </span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { TransactionViewModel, ImportContext } from '@/types/transactions'
import { isTransactionBalanced } from '@/utils/transactions'

interface Props {
  transactions: TransactionViewModel[]
  importContext?: Map<string, ImportContext>
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'duplicateClick', transactionId: string): void
}>()

const getImportContext = (transactionId: string): ImportContext | undefined => {
  return props.importContext?.get(transactionId)
}

const netFlowByCurrency = computed(() => {
  const flows: Record<string, number> = {}

  props.transactions.forEach(transaction => {
    const sourceAccount = transaction.meta['source_account']

    if (sourceAccount) {
      const sourcePosting = transaction.postings.find(p => p.account === sourceAccount)
      if (sourcePosting && sourcePosting.amount !== null) {
        const currency = sourcePosting.currency
        if (!flows[currency]) {
          flows[currency] = 0
        }
        flows[currency] += sourcePosting.amount
      }
    }
  })

  const formatted: Record<string, string> = {}
  Object.keys(flows).forEach(currency => {
    formatted[currency] = flows[currency].toFixed(2)
  })
  return formatted
})

const editedCount = computed(() => {
  return props.transactions.filter(t => t.internal.isModified).length
})

const unbalancedCount = computed(() => {
  return props.transactions.filter(t => !isTransactionBalanced(t)).length
})

const duplicateCount = computed(() => {
  return props.transactions.filter(t => {
    const context = getImportContext(t.id)
    return context?.is_duplicate === true
  }).length
})

const accountSumsByCurrency = computed(() => {
  const sums: Record<string, Record<string, number>> = {}

  props.transactions.forEach(transaction => {
    transaction.postings.forEach(posting => {
      if (posting.amount === null) return

      const topLevelAccount = posting.account.split(':')[0]
      const currency = posting.currency

      if (!sums[topLevelAccount]) {
        sums[topLevelAccount] = {}
      }
      if (!sums[topLevelAccount][currency]) {
        sums[topLevelAccount][currency] = 0
      }

      sums[topLevelAccount][currency] += posting.amount
    })
  })

  return sums
})

const handleDuplicateSummaryClick = () => {
  if (duplicateCount.value === 0) return

  const firstDuplicateTransaction = props.transactions.find(t => {
    const context = getImportContext(t.id)
    return context?.is_duplicate === true
  })

  if (firstDuplicateTransaction) {
    emit('duplicateClick', firstDuplicateTransaction.id)
  }
}
</script>
