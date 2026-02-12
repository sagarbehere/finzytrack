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
            <DialogPanel class="w-full max-w-6xl transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 p-6 text-left shadow-xl transition-all">
              <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-1">
                Account Statement
              </DialogTitle>
              <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
                {{ account?.fullPath }}
              </p>

              <!-- Date filter -->
              <div class="mb-4 relative z-20">
                <DatePresetSelector
                  :start-date="dateStartDate"
                  :end-date="dateEndDate"
                  :active-preset="dateActivePreset"
                  @update:active-preset="dateActivePreset = $event"
                  @change="handleDateChange"
                />
              </div>

              <!-- Search + Currency chips -->
              <div class="mb-3 flex flex-wrap items-center gap-3">
                <!-- Search input -->
                <div class="relative flex-1 min-w-[200px] max-w-sm">
                  <MagnifyingGlassIcon class="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                  <input
                    v-model="searchText"
                    type="text"
                    placeholder="Search payee or narration..."
                    class="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-300 rounded-md bg-white dark:bg-gray-700 dark:border-gray-600 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <!-- Currency toggle chips -->
                <div v-if="allCurrencies.length > 1" class="flex items-center gap-1.5">
                  <span class="text-xs text-gray-500 dark:text-gray-400">Currencies:</span>
                  <button
                    v-for="ccy in allCurrencies"
                    :key="ccy"
                    @click="toggleCurrency(ccy)"
                    :class="[
                      'px-2 py-0.5 text-xs font-medium rounded-full border transition-colors',
                      visibleCurrencies.has(ccy)
                        ? 'bg-teal-100 text-teal-800 border-teal-300 dark:bg-teal-900/40 dark:text-teal-300 dark:border-teal-700'
                        : 'bg-gray-100 text-gray-500 border-gray-300 dark:bg-gray-700 dark:text-gray-400 dark:border-gray-600'
                    ]"
                  >
                    {{ ccy }}
                  </button>
                </div>
              </div>

              <!-- Transaction count -->
              <p class="mb-2 text-xs text-gray-500 dark:text-gray-400">
                Showing {{ filteredTransactions.length }} of {{ enrichedTransactions.length }} transactions
              </p>

              <!-- Loading state -->
              <div v-if="isLoading" class="flex justify-center py-12">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
              </div>

              <!-- Empty state -->
              <div v-else-if="enrichedTransactions.length === 0" class="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
                No transactions found for this account
              </div>

              <!-- Statement table -->
              <div v-else class="overflow-x-auto max-h-[60vh] overflow-y-auto border border-gray-200 dark:border-gray-700 rounded-md">
                <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                  <thead class="bg-gray-50 dark:bg-gray-700 sticky top-0 z-10">
                    <tr>
                      <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[50px]">#</th>
                      <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[100px]">Date</th>
                      <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase dark:text-gray-400">Description</th>
                      <template v-for="ccy in visibleCurrenciesSorted" :key="ccy">
                        <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[120px]">
                          Amount ({{ ccy }})
                        </th>
                        <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[120px]">
                          Balance ({{ ccy }})
                        </th>
                      </template>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-800">
                    <tr
                      v-for="(tx, idx) in filteredTransactions"
                      :key="tx.id"
                      class="hover:bg-gray-50 dark:hover:bg-gray-700/50"
                    >
                      <td class="px-3 py-2 text-xs text-gray-400 dark:text-gray-500 tabular-nums">{{ idx + 1 }}</td>
                      <td class="px-3 py-2 text-sm text-gray-900 dark:text-white whitespace-nowrap tabular-nums">{{ tx.date }}</td>
                      <td class="px-3 py-2 text-sm text-gray-900 dark:text-white truncate max-w-[300px]" :title="txDescription(tx)">
                        {{ txDescription(tx) }}
                      </td>
                      <template v-for="ccy in visibleCurrenciesSorted" :key="ccy">
                        <td class="px-3 py-2 text-sm text-right tabular-nums whitespace-nowrap"
                          :class="amountColor(txAmount(tx, ccy))"
                        >
                          {{ formatAmount(txAmount(tx, ccy)) }}
                        </td>
                        <td class="px-3 py-2 text-sm text-right tabular-nums whitespace-nowrap text-gray-900 dark:text-white">
                          {{ formatBalance(tx.runningBalances[ccy]) }}
                        </td>
                      </template>
                    </tr>
                    <!-- No results after filter -->
                    <tr v-if="filteredTransactions.length === 0">
                      <td :colspan="3 + visibleCurrenciesSorted.length * 2" class="px-3 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                        No transactions match the current filters
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
import { MagnifyingGlassIcon } from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'
import type { TransactionViewModel } from '@/types/transactions'
import { useTransactionQuery } from '@/composables/useTransactionQuery'
import DatePresetSelector from '@/components/common/DatePresetSelector.vue'

interface AccountPosting {
  amount: number
  currency: string
}

interface EnrichedTransaction {
  id: string
  date: string
  payee: string
  narration: string
  accountPostings: AccountPosting[]
  runningBalances: Record<string, number>
}

interface Props {
  open: boolean
  account: AccountTreeNode | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const { queryTransactions, isLoading } = useTransactionQuery()

// State
const allTransactions = ref<TransactionViewModel[]>([])
const enrichedTransactions = ref<EnrichedTransaction[]>([])
const allCurrencies = ref<string[]>([])
const visibleCurrencies = ref<Set<string>>(new Set())
const searchText = ref('')
const dateStartDate = ref<string | null>(null)
const dateEndDate = ref<string | null>(null)
const dateActivePreset = ref<string | null>('All Time')

// Computed: visible currencies sorted
const visibleCurrenciesSorted = computed(() =>
  allCurrencies.value.filter(c => visibleCurrencies.value.has(c))
)

// Computed: filtered transactions (date + search, balances stay cumulative)
const filteredTransactions = computed(() => {
  let result = enrichedTransactions.value

  // Date filter
  if (dateStartDate.value) {
    result = result.filter(tx => tx.date >= dateStartDate.value!)
  }
  if (dateEndDate.value) {
    result = result.filter(tx => tx.date <= dateEndDate.value!)
  }

  // Search filter
  if (searchText.value.trim()) {
    const term = searchText.value.trim().toLowerCase()
    result = result.filter(tx =>
      tx.payee.toLowerCase().includes(term) ||
      tx.narration.toLowerCase().includes(term)
    )
  }

  return result
})

// Load data when modal opens
watch(() => props.open, async (isOpen) => {
  if (isOpen && props.account) {
    searchText.value = ''
    dateStartDate.value = null
    dateEndDate.value = null
    dateActivePreset.value = 'All Time'
    await loadTransactions()
  }
})

async function loadTransactions() {
  if (!props.account) return

  try {
    const { transactions } = await queryTransactions(
      { accountContains: props.account.fullPath },
      'sqlite',
      50000
    )
    allTransactions.value = transactions
    computeEnrichedTransactions()
    // Populate date pickers with actual range for "All Time"
    if (enrichedTransactions.value.length > 0) {
      dateStartDate.value = enrichedTransactions.value[0].date
      dateEndDate.value = enrichedTransactions.value[enrichedTransactions.value.length - 1].date
    }
  } catch {
    allTransactions.value = []
    enrichedTransactions.value = []
    allCurrencies.value = []
    visibleCurrencies.value = new Set()
  }
}

function computeEnrichedTransactions() {
  if (!props.account) return

  const accountPath = props.account.fullPath

  // Sort by date ASC, then id ASC for stable order
  const sorted = [...allTransactions.value].sort((a, b) => {
    const dateComp = a.date.localeCompare(b.date)
    if (dateComp !== 0) return dateComp
    return a.id.localeCompare(b.id)
  })

  // Compute running balances
  const runningBalances: Record<string, number> = {}
  const currencySet = new Set<string>()
  const enriched: EnrichedTransaction[] = []

  for (const tx of sorted) {
    // Find postings for this exact account
    const accountPostings: AccountPosting[] = []
    for (const posting of tx.postings) {
      if (posting.account === accountPath && posting.amount !== null) {
        accountPostings.push({
          amount: posting.amount,
          currency: posting.currency,
        })
        currencySet.add(posting.currency)

        // Accumulate running balance
        if (!(posting.currency in runningBalances)) {
          runningBalances[posting.currency] = 0
        }
        runningBalances[posting.currency] += posting.amount
      }
    }

    // Only include transactions that have postings for this account
    if (accountPostings.length > 0) {
      enriched.push({
        id: tx.id,
        date: tx.date,
        payee: tx.payee || '',
        narration: tx.narration || '',
        accountPostings,
        runningBalances: { ...runningBalances },
      })
    }
  }

  const currencies = Array.from(currencySet).sort()
  allCurrencies.value = currencies
  visibleCurrencies.value = new Set(currencies)
  enrichedTransactions.value = enriched
}

function toggleCurrency(ccy: string) {
  const next = new Set(visibleCurrencies.value)
  if (next.has(ccy)) {
    // Don't allow deselecting the last one
    if (next.size > 1) {
      next.delete(ccy)
    }
  } else {
    next.add(ccy)
  }
  visibleCurrencies.value = next
}

function handleDateChange(range: { startDate: string | null; endDate: string | null }) {
  dateStartDate.value = range.startDate
  dateEndDate.value = range.endDate
}

function txDescription(tx: EnrichedTransaction): string {
  if (tx.payee && tx.narration) return `${tx.payee} | ${tx.narration}`
  return tx.payee || tx.narration || '—'
}

function txAmount(tx: EnrichedTransaction, currency: string): number | null {
  let total = 0
  let found = false
  for (const p of tx.accountPostings) {
    if (p.currency === currency) {
      total += p.amount
      found = true
    }
  }
  return found ? total : null
}

function formatAmount(value: number | null): string {
  if (value === null) return '—'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function formatBalance(value: number | undefined): string {
  if (value === undefined) return '—'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function amountColor(value: number | null): string {
  if (value === null) return 'text-gray-400 dark:text-gray-500'
  if (value > 0) return 'text-green-600 dark:text-green-400'
  if (value < 0) return 'text-red-600 dark:text-red-400'
  return 'text-gray-900 dark:text-white'
}

function handleClose() {
  emit('update:open', false)
}
</script>
