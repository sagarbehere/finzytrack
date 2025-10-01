<template>
  <div class="flex flex-col items-center justify-center gap-0.5 min-w-[24px] py-1">
    <div
      v-for="icon in statusIcons"
      :key="icon.key"
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
import type { TransactionViewModel, ImportContext, LedgerContext } from '@/types/transactions'

interface Props {
  transaction: TransactionViewModel
  importContext?: ImportContext
  ledgerContext?: LedgerContext
}

const props = defineProps<Props>()

interface StatusIcon {
  key: string
  symbol: string
  tooltip: string
  class: string
  priority: number
}

const statusIcons = computed(() => {
  const icons: StatusIcon[] = []

  // Priority 1: Unbalanced (always check, highest priority)
  const isUnbalanced = computed(() => {
    const totalInCents = props.transaction.postings.reduce((sum, posting) => {
      const amount = posting.amount || 0
      return sum + Math.round(amount * 100)
    }, 0)
    return Math.abs(totalInCents) >= 1
  })

  if (isUnbalanced.value) {
    icons.push({
      key: 'unbalanced',
      symbol: '⚠️',
      tooltip: 'Transaction is unbalanced',
      class: 'text-red-600 dark:text-red-400',
      priority: 1
    })
  }

  // Priority 2: Import context - duplicate detection
  if (props.importContext?.is_duplicate) {
    icons.push({
      key: 'duplicate',
      symbol: '👥',
      tooltip: 'Potential duplicate transaction',
      class: 'text-yellow-600 dark:text-yellow-400',
      priority: 2
    })
  }

  // Priority 3: Import context - confidence level
  const confidence = props.importContext?.confidence
  if (confidence !== undefined) {
    if (confidence >= 0.8) {
      icons.push({
        key: 'high-confidence',
        symbol: '✅',
        tooltip: `High confidence auto-categorization (${Math.round(confidence * 100)}%)`,
        class: 'text-green-600 dark:text-green-400',
        priority: 3
      })
    } else if (confidence < 0.5) {
      icons.push({
        key: 'low-confidence',
        symbol: '❓',
        tooltip: `Low confidence auto-categorization (${Math.round(confidence * 100)}%)`,
        class: 'text-yellow-600 dark:text-yellow-400',
        priority: 3
      })
    }
  }

  // Priority 4: Ledger context - reconciliation status
  if (props.ledgerContext?.reconciled === true) {
    icons.push({
      key: 'reconciled',
      symbol: '✓',
      tooltip: 'Reconciled with bank statement',
      class: 'text-green-600 dark:text-green-400',
      priority: 4
    })
  }

  // Priority 5: Ledger context - pending edits
  if (props.ledgerContext?.pending_edits === true) {
    icons.push({
      key: 'pending-edits',
      symbol: '⏳',
      tooltip: 'Has pending edits',
      class: 'text-blue-600 dark:text-blue-400',
      priority: 5
    })
  }

  // Priority 6: Modified transaction indicator
  if (props.transaction.meta.isModified) {
    icons.push({
      key: 'modified',
      symbol: '✏️',
      tooltip: 'Transaction has been modified',
      class: 'text-blue-600 dark:text-blue-400',
      priority: 6
    })
  }

  // Priority 7: Fallback indicator (no specific context)
  if (icons.length === 0 && props.transaction.meta.isNew) {
    icons.push({
      key: 'new',
      symbol: '✨',
      tooltip: 'New transaction',
      class: 'text-gray-600 dark:text-gray-400',
      priority: 7
    })
  }

  // Sort by priority and return
  return icons.sort((a, b) => a.priority - b.priority)
})
</script>