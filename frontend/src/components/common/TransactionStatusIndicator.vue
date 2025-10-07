<template>
  <div class="flex flex-col items-center justify-start gap-0.5 min-w-[24px] py-1.5">
    <div
      v-for="icon in statusIcons"
      :key="icon.key"
      :title="icon.tooltip"
      :class="[
        icon.clickable ? 'cursor-pointer hover:scale-110 transition-transform' : '',
        icon.heroIcon ? 'relative w-5 h-5' : 'text-sm leading-none'
      ]"
      @click="icon.clickable ? handleIconClick(icon.key) : null"
    >
      <!-- Heroicon with circular background -->
      <component
        v-if="icon.heroIcon"
        :is="icon.heroIcon"
        :class="['w-5 h-5', icon.class]"
      />
      <!-- Regular emoji/symbol -->
      <span v-else :class="icon.class" class="inline-block py-1.5">
        {{ icon.symbol }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  CheckCircleIcon,
  MinusCircleIcon,
  ExclamationCircleIcon
} from '@heroicons/vue/24/solid'
import type { TransactionViewModel, ImportContext, LedgerContext } from '@/types/transactions'

interface Props {
  transaction: TransactionViewModel
  importContext?: ImportContext
  ledgerContext?: LedgerContext
}

const props = defineProps<Props>()

interface Emits {
  (e: 'duplicateClick', transactionId: string): void
}

const emit = defineEmits<Emits>()

interface StatusIcon {
  key: string
  symbol?: string
  heroIcon?: any
  tooltip: string
  class: string
  priority: number
  clickable?: boolean
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
      tooltip: 'Potential duplicate transaction (click to review)',
      class: 'text-yellow-600 dark:text-yellow-400',
      priority: 2,
      clickable: true
    })
  }

  // Priority 3: Import context - confidence level
  const confidence = props.importContext?.confidence
  if (confidence !== undefined) {
    if (confidence >= 0.95) {
      icons.push({
        key: 'high-confidence',
        heroIcon: CheckCircleIcon,
        tooltip: `High confidence auto-categorization (${Math.round(confidence * 100)}%)`,
        class: 'text-green-600 dark:text-green-400',
        priority: 3
      })
    } else if (confidence <= 0.5) {
      icons.push({
        key: 'low-confidence',
        heroIcon: ExclamationCircleIcon,
        tooltip: `Low confidence auto-categorization (${Math.round(confidence * 100)}%)`,
        class: 'text-red-600 dark:text-red-400',
        priority: 3
      })
    } else {
      // Medium confidence (between 50% and 95%)
      icons.push({
        key: 'medium-confidence',
        heroIcon: MinusCircleIcon,
        tooltip: `Medium confidence auto-categorization (${Math.round(confidence * 100)}%)`,
        class: 'text-blue-600 dark:text-blue-400',
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
  if (props.transaction.internal.isModified) {
    icons.push({
      key: 'modified',
      symbol: '✏️',
      tooltip: 'Transaction has been modified',
      class: 'text-blue-600 dark:text-blue-400',
      priority: 6
    })
  }

  // Priority 7: Fallback indicator (no specific context)
  if (icons.length === 0 && props.transaction.internal.isNew) {
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

function handleIconClick(iconKey: string) {
  if (iconKey === 'duplicate') {
    emit('duplicateClick', props.transaction.id)
  }
}
</script>