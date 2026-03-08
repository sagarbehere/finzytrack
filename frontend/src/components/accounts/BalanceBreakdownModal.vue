<template>
  <TransitionRoot appear :show="open" as="template">
    <Dialog as="div" @close="handleClose" class="relative z-50">
      <TransitionChild
        as="template"
        enter="duration-300 ease-out"
        enter-from="opacity-0"
        enter-to="opacity-100"
        leave="duration-200 ease-in"
        leave-from="opacity-100"
        leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/25 dark:bg-black/50" />
      </TransitionChild>

      <div class="fixed inset-0 overflow-y-auto">
        <div class="flex min-h-full items-center justify-center p-4">
          <TransitionChild
            as="template"
            enter="duration-300 ease-out"
            enter-from="opacity-0 scale-95"
            enter-to="opacity-100 scale-100"
            leave="duration-200 ease-in"
            leave-from="opacity-100 scale-100"
            leave-to="opacity-0 scale-95"
          >
            <DialogPanel class="w-full max-w-lg transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 p-6 text-left shadow-xl transition-all">
              <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-2">
                Balance Breakdown
              </DialogTitle>
              <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
                {{ account?.fullPath }}
              </p>

              <!-- Search -->
              <div class="mb-4">
                <div class="relative">
                  <MagnifyingGlassIcon class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                  <input
                    v-model="searchQuery"
                    type="text"
                    placeholder="Search currencies..."
                    class="block w-full pl-9 pr-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                  />
                </div>
              </div>

              <!-- Balances table -->
              <div class="max-h-80 overflow-y-auto">
                <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                  <thead class="bg-gray-50 dark:bg-gray-700 sticky top-0">
                    <tr>
                      <th
                        scope="col"
                        class="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400 cursor-pointer hover:text-gray-700 dark:hover:text-gray-200"
                        @click="toggleSort('currency')"
                      >
                        <div class="flex items-center gap-1">
                          Currency
                          <ChevronUpIcon
                            v-if="sortBy === 'currency'"
                            class="h-3 w-3"
                            :class="{ 'rotate-180': sortDir === 'desc' }"
                          />
                        </div>
                      </th>
                      <th
                        scope="col"
                        class="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400 cursor-pointer hover:text-gray-700 dark:hover:text-gray-200"
                        @click="toggleSort('balance')"
                      >
                        <div class="flex items-center justify-end gap-1">
                          Balance
                          <ChevronUpIcon
                            v-if="sortBy === 'balance'"
                            class="h-3 w-3"
                            :class="{ 'rotate-180': sortDir === 'desc' }"
                          />
                        </div>
                      </th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                    <tr
                      v-for="balance in filteredBalances"
                      :key="balance.currency"
                      class="hover:bg-gray-50 dark:hover:bg-gray-700/50"
                    >
                      <td class="px-4 py-2 text-sm font-medium text-gray-900 dark:text-white">
                        {{ balance.currency }}
                      </td>
                      <td
                        class="px-4 py-2 text-sm text-right"
                        :class="[
                          balance.balance >= 0
                            ? 'text-green-600 dark:text-green-400'
                            : 'text-red-600 dark:text-red-400'
                        ]"
                      >
                        {{ formatBalance(balance.balance, balance.currency) }}
                      </td>
                    </tr>
                    <tr v-if="filteredBalances.length === 0">
                      <td colspan="2" class="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                        No currencies found
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <!-- Close button -->
              <div class="mt-6 flex justify-end">
                <button
                  type="button"
                  @click="handleClose"
                  class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600 dark:hover:bg-gray-600"
                >
                  Close
                </button>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { MagnifyingGlassIcon, ChevronUpIcon } from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'
import { getLocale } from '@/utils/currencyFormat'

interface Props {
  open: boolean
  account: AccountTreeNode | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const searchQuery = ref('')
const sortBy = ref<'currency' | 'balance'>('balance')
const sortDir = ref<'asc' | 'desc'>('desc')

// Reset state when modal opens
watch(() => props.open, (isOpen) => {
  if (isOpen) {
    searchQuery.value = ''
    sortBy.value = 'balance'
    sortDir.value = 'desc'
  }
})

const filteredBalances = computed(() => {
  if (!props.account) return []

  let balances = [...props.account.aggregatedBalances]

  // Filter by search
  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    balances = balances.filter(b => b.currency.toLowerCase().includes(query))
  }

  // Sort
  balances.sort((a, b) => {
    let comparison = 0
    if (sortBy.value === 'currency') {
      comparison = a.currency.localeCompare(b.currency)
    } else {
      comparison = Math.abs(b.balance) - Math.abs(a.balance)
    }
    return sortDir.value === 'asc' ? comparison : -comparison
  })

  return balances
})

function toggleSort(field: 'currency' | 'balance') {
  if (sortBy.value === field) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = field
    sortDir.value = field === 'balance' ? 'desc' : 'asc'
  }
}

function formatBalance(value: number, currency: string): string {
  return value.toLocaleString(getLocale(currency), {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })
}

function handleClose() {
  emit('update:open', false)
}
</script>
