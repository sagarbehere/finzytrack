<template>
  <div class="flex flex-col items-center justify-center gap-0.5 min-w-[24px] py-1">
    <div
      v-for="(icon, index) in statusIcons"
      :key="index"
      :title="icon.tooltip"
      class="text-sm leading-none"
      :class="icon.class"
    >
      {{ icon.symbol }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { TransactionViewModel } from '@/types/transactions'

interface Props {
  transaction: TransactionViewModel
}

const props = defineProps<Props>()

interface StatusIcon {
  symbol: string
  tooltip: string
  class: string
  priority: number
}

const statusIcons = computed(() => {
  const icons: StatusIcon[] = []

  // Check if transaction is unbalanced (highest priority)
  const isUnbalanced = computed(() => {
    const totalInCents = props.transaction.postings.reduce((sum, posting) => {
      const amount = posting.amount || 0
      return sum + Math.round(amount * 100)
    }, 0)
    return Math.abs(totalInCents) >= 1
  })

  if (isUnbalanced.value) {
    icons.push({
      symbol: '⚠️',
      tooltip: 'Transaction is unbalanced',
      class: 'text-red-600 dark:text-red-400',
      priority: 1
    })
  }

  // Check for potential duplicate (second priority)
  if (props.transaction.import_details?.is_duplicate) {
    icons.push({
      symbol: '👥',
      tooltip: 'Potential duplicate transaction',
      class: 'text-gray-600 dark:text-gray-400',
      priority: 2
    })
  }

  // Check confidence level (lowest priority)
  const confidence = props.transaction.import_details?.confidence
  if (confidence !== undefined) {
    if (confidence >= 0.8) {
      icons.push({
        symbol: '✅',
        tooltip: `High confidence auto-categorization (${Math.round(confidence * 100)}%)`,
        class: 'text-green-600 dark:text-green-400',
        priority: 3
      })
    } else if (confidence < 0.5) {
      icons.push({
        symbol: '❓',
        tooltip: `Low confidence auto-categorization (${Math.round(confidence * 100)}%)`,
        class: 'text-yellow-600 dark:text-yellow-400',
        priority: 3
      })
    }
  }

  // Sort by priority and return
  return icons.sort((a, b) => a.priority - b.priority)
})
</script>