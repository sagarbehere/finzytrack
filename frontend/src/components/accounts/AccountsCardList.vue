<template>
  <div class="space-y-1.5">
    <div
      v-for="node in displayNodes"
      :key="node.id"
      class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10"
    >
      <!-- Main row: chevron + name + type badge -->
      <div class="flex items-center gap-2 px-3 py-2.5">
        <!-- Expand/Collapse -->
        <button
          v-if="node.children.length > 0"
          @click="emit('toggle', node.id)"
          class="shrink-0 p-1 rounded hover:bg-gray-200 dark:hover:bg-white/5"
        >
          <ChevronRightIcon
            class="h-4 w-4 text-gray-500 dark:text-gray-400 transition-transform"
            :class="{ 'rotate-90': expandedIds.has(node.id) }"
          />
        </button>
        <span v-else class="w-6 shrink-0"></span>

        <!-- Name + parent path -->
        <div class="flex-1 min-w-0">
          <!-- Parent breadcrumb -->
          <span v-if="node.depth > 0" class="block text-xs text-gray-400 dark:text-gray-500 truncate">
            {{ parentPath(node) }}
          </span>
          <!-- Account name -->
          <button
            v-if="!node.isVirtual"
            @click="emit('show-detail', node)"
            class="text-left text-sm font-medium text-gray-900 dark:text-white hover:text-indigo-600 dark:hover:text-indigo-400 truncate block w-full"
          >
            {{ node.name }}
          </button>
          <span
            v-else
            class="text-sm font-medium text-gray-400 italic dark:text-gray-500 truncate block"
          >
            {{ node.name }}
          </span>
        </div>

        <!-- Type badge -->
        <span
          class="shrink-0 inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
          :class="typeColors[node.type]"
        >
          {{ node.type }}
        </span>
      </div>

      <!-- Balance + Status + Currencies (shown for all nodes including virtual) -->
      <div v-if="node.aggregatedBalances.length > 0 || !node.isVirtual" class="border-t border-gray-100 dark:border-white/5 px-3 py-2 flex items-center justify-between gap-2">
        <div class="min-w-0 flex-1">
          <button
            v-if="!node.isVirtual && formatBalanceDisplay(node).display && formatBalanceDisplay(node).display !== '—'"
            @click="emit('view-transactions', node)"
            class="text-sm text-indigo-600 hover:text-indigo-800 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300 truncate block"
            title="View transactions"
          >
            {{ formatBalanceDisplay(node).display }}
            <span
              v-if="formatBalanceDisplay(node).overflow > 0"
              class="text-xs"
            >+{{ formatBalanceDisplay(node).overflow }} more</span>
          </button>
          <span v-else-if="node.isVirtual && formatBalanceDisplay(node).display && formatBalanceDisplay(node).display !== '—'" class="text-sm text-gray-600 dark:text-gray-300 truncate block">
            {{ formatBalanceDisplay(node).display }}
            <span
              v-if="formatBalanceDisplay(node).overflow > 0"
              class="text-xs text-gray-400"
            >+{{ formatBalanceDisplay(node).overflow }} more</span>
          </span>
          <span v-else class="text-sm text-gray-400 dark:text-gray-500">—</span>
        </div>

        <div class="flex items-center gap-1.5 shrink-0">
          <span
            v-if="!node.isVirtual"
            class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium"
            :class="statusColors[node.status]"
          >
            {{ node.status === 'open' ? 'Open' : 'Closed' }}
          </span>
          <span
            v-for="currency in node.currencyBadges.slice(0, 2)"
            :key="currency"
            class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
          >{{ currency }}</span>
          <span
            v-if="node.currencyBadges.length > 2"
            class="text-xs text-gray-500 dark:text-gray-400"
          >+{{ node.currencyBadges.length - 2 }}</span>
        </div>
      </div>

      <!-- Actions row (real accounts only — virtual nodes have no directives to act on) -->
      <template v-if="!node.isVirtual">
        <div class="border-t border-gray-100 dark:border-white/5 px-3 py-1.5 flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
          <button
            @click="emit('edit', node)"
            class="flex items-center gap-1 text-xs text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 py-1"
          >
            <PencilIcon class="h-4 w-4" />
            <span>Edit</span>
          </button>
          <button
            @click="emit('show-statement', node)"
            class="flex items-center gap-1 text-xs text-gray-500 hover:text-teal-600 dark:text-gray-400 dark:hover:text-teal-400 py-1"
          >
            <DocumentTextIcon class="h-4 w-4" />
            <span>Statement</span>
          </button>
          <button
            @click="emit('show-balance-directives', node)"
            class="flex items-center gap-1 text-xs text-gray-500 hover:text-purple-600 dark:text-gray-400 dark:hover:text-purple-400 py-1"
          >
            <ScaleIcon class="h-4 w-4" />
            <span>Balance</span>
          </button>
          <button
            v-if="node.status === 'open'"
            @click="emit('close', node)"
            class="flex items-center gap-1 text-xs text-gray-500 hover:text-amber-600 dark:text-gray-400 dark:hover:text-amber-400 py-1"
          >
            <XCircleIcon class="h-4 w-4" />
            <span>Close</span>
          </button>
          <button
            v-else
            @click="emit('reopen', node)"
            class="flex items-center gap-1 text-xs text-gray-500 hover:text-green-600 dark:text-gray-400 dark:hover:text-green-400 py-1"
          >
            <ArrowPathIcon class="h-4 w-4" />
            <span>Reopen</span>
          </button>
          <button
            @click="emit('delete', node)"
            class="flex items-center gap-1 text-xs text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400 py-1"
          >
            <TrashIcon class="h-4 w-4" />
          </button>
        </div>
      </template>
    </div>

    <!-- Empty state -->
    <div v-if="displayNodes.length === 0" class="px-4 py-12 text-center text-gray-500 dark:text-gray-400">
      No accounts found
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  ChevronRightIcon,
  PencilIcon,
  TrashIcon,
  XCircleIcon,
  ArrowPathIcon,
  ScaleIcon,
  DocumentTextIcon,
} from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'
import { typeColors, statusColors } from '@/types/accounts'
import { formatBalances } from '@/composables/accountTreeUtils'

interface Props {
  displayNodes: AccountTreeNode[]
  expandedIds: Set<string>
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'toggle', id: string): void
  (e: 'edit', node: AccountTreeNode): void
  (e: 'close', node: AccountTreeNode): void
  (e: 'reopen', node: AccountTreeNode): void
  (e: 'delete', node: AccountTreeNode): void
  (e: 'show-balances', node: AccountTreeNode): void
  (e: 'show-balance-directives', node: AccountTreeNode): void
  (e: 'show-statement', node: AccountTreeNode): void
  (e: 'view-transactions', node: AccountTreeNode): void
  (e: 'show-detail', node: AccountTreeNode): void
}>()

function formatBalanceDisplay(node: AccountTreeNode) {
  return formatBalances(node.aggregatedBalances, 2)
}

/** Extract parent path from fullPath, e.g. "Assets:Bank:Checking" → "Assets : Bank" */
function parentPath(node: AccountTreeNode): string {
  const parts = node.fullPath.split(':')
  if (parts.length <= 1) return ''
  return parts.slice(0, -1).join(' : ')
}

</script>
