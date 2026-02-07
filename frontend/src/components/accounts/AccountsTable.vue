<template>
  <div class="overflow-x-auto">
    <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
      <thead class="bg-gray-50 dark:bg-gray-800">
        <tr>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Account Name
          </th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Type
          </th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Status
          </th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Opened
          </th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Closed
          </th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Currencies
          </th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            <div class="flex items-center gap-1">
              Balance
              <div class="relative group">
                <InformationCircleIcon class="h-4 w-4 cursor-help text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" />
                <div class="absolute left-0 top-6 z-50 hidden group-hover:block w-64 p-2 text-xs font-normal normal-case tracking-normal bg-gray-900 text-white rounded shadow-lg dark:bg-gray-700">
                  <p class="mb-1"><strong>Balance sheet</strong> (Assets, Liabilities, Equity): cumulative balance up to end date.</p>
                  <p><strong>Income statement</strong> (Income, Expenses): balance within the date range.</p>
                </div>
              </div>
            </div>
          </th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Notes
          </th>
          <th scope="col" class="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400">
            Actions
          </th>
        </tr>
      </thead>
      <tbody class="bg-white divide-y divide-gray-200 dark:bg-gray-900 dark:divide-gray-700">
        <tr
          v-for="node in displayNodes"
          :key="node.id"
          class="hover:bg-gray-50 dark:hover:bg-gray-800/50"
        >
          <!-- Account Name with tree indent -->
          <td class="px-4 py-3 whitespace-nowrap">
            <div
              class="flex items-center"
              :style="{ paddingLeft: `${node.depth * 24}px` }"
            >
              <!-- Expand/Collapse button -->
              <button
                v-if="node.children.length > 0"
                @click="emit('toggle', node.id)"
                class="mr-2 p-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 focus:outline-none"
              >
                <ChevronRightIcon
                  class="h-4 w-4 text-gray-500 dark:text-gray-400 transition-transform"
                  :class="{ 'rotate-90': expandedIds.has(node.id) }"
                />
              </button>
              <span v-else class="w-5 mr-2"></span>

              <!-- Account name -->
              <span
                class="text-sm font-medium"
                :class="[
                  node.isVirtual
                    ? 'text-gray-400 italic dark:text-gray-500'
                    : 'text-gray-900 dark:text-white'
                ]"
              >
                {{ node.name }}
              </span>
            </div>
          </td>

          <!-- Type badge -->
          <td class="px-4 py-3 whitespace-nowrap">
            <span
              class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
              :class="typeColors[node.type]"
            >
              {{ node.type }}
            </span>
          </td>

          <!-- Status badge -->
          <td class="px-4 py-3 whitespace-nowrap">
            <span
              class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
              :class="statusColors[node.status]"
            >
              {{ node.status === 'open' ? 'Open' : 'Closed' }}
            </span>
          </td>

          <!-- Opened date -->
          <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
            {{ node.openDate || '—' }}
          </td>

          <!-- Closed date -->
          <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
            {{ node.closeDate || '—' }}
          </td>

          <!-- Currencies -->
          <td class="px-4 py-3 whitespace-nowrap">
            <div class="flex flex-wrap gap-1">
              <span
                v-for="currency in node.currencyBadges.slice(0, 3)"
                :key="currency"
                class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300"
              >
                {{ currency }}
              </span>
              <span
                v-if="node.currencyBadges.length > 3"
                class="text-xs text-gray-500 dark:text-gray-400"
              >
                +{{ node.currencyBadges.length - 3 }}
              </span>
            </div>
          </td>

          <!-- Balance -->
          <td class="px-4 py-3 whitespace-nowrap">
            <div class="text-sm">
              <button
                v-if="formatBalanceDisplay(node).display"
                @click="emit('view-transactions', node)"
                class="text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
                title="View transactions for this account"
              >
                {{ formatBalanceDisplay(node).display }}
              </button>
              <span v-else class="text-gray-900 dark:text-white">—</span>
              <button
                v-if="formatBalanceDisplay(node).overflow > 0"
                @click="emit('show-balances', node)"
                class="ml-1 text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 text-xs"
              >
                +{{ formatBalanceDisplay(node).overflow }} more
              </button>
            </div>
          </td>

          <!-- Notes -->
          <td class="px-4 py-3 text-sm text-gray-500 dark:text-gray-400 max-w-[200px] truncate">
            {{ node.notes || '—' }}
          </td>

          <!-- Actions -->
          <td class="px-4 py-3 whitespace-nowrap text-right text-sm">
            <div v-if="!node.isVirtual" class="flex items-center justify-end gap-2">
              <!-- Edit -->
              <button
                @click="emit('edit', node)"
                class="text-gray-500 hover:text-blue-600 dark:text-gray-400 dark:hover:text-blue-400"
                title="Edit account"
              >
                <PencilIcon class="h-4 w-4" />
              </button>

              <!-- Close/Reopen -->
              <button
                v-if="node.status === 'open'"
                @click="emit('close', node)"
                class="text-gray-500 hover:text-amber-600 dark:text-gray-400 dark:hover:text-amber-400"
                title="Close account"
              >
                <XCircleIcon class="h-4 w-4" />
              </button>
              <button
                v-else
                @click="emit('reopen', node)"
                class="text-gray-500 hover:text-green-600 dark:text-gray-400 dark:hover:text-green-400"
                title="Reopen account"
              >
                <ArrowPathIcon class="h-4 w-4" />
              </button>

              <!-- Delete -->
              <button
                @click="emit('delete', node)"
                class="text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
                title="Delete account"
              >
                <TrashIcon class="h-4 w-4" />
              </button>
            </div>
            <span v-else class="text-gray-300 dark:text-gray-600">—</span>
          </td>
        </tr>

        <!-- Empty state -->
        <tr v-if="displayNodes.length === 0">
          <td colspan="9" class="px-4 py-12 text-center text-gray-500 dark:text-gray-400">
            No accounts found
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import {
  ChevronRightIcon,
  PencilIcon,
  TrashIcon,
  XCircleIcon,
  ArrowPathIcon,
  InformationCircleIcon
} from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'
import { typeColors, statusColors } from '@/types/accounts'
import { formatBalances } from '@/composables/useAccountsTree'

interface Props {
  displayNodes: AccountTreeNode[]
  expandedIds: Set<string>
}

interface Emits {
  (e: 'toggle', id: string): void
  (e: 'edit', node: AccountTreeNode): void
  (e: 'close', node: AccountTreeNode): void
  (e: 'reopen', node: AccountTreeNode): void
  (e: 'delete', node: AccountTreeNode): void
  (e: 'show-balances', node: AccountTreeNode): void
  (e: 'view-transactions', node: AccountTreeNode): void
}

defineProps<Props>()
const emit = defineEmits<Emits>()

function formatBalanceDisplay(node: AccountTreeNode) {
  return formatBalances(node.aggregatedBalances, 2)
}
</script>
