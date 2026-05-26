<template>
  <TransitionRoot appear :show="open" as="template">
    <Dialog as="div" @close="emit('update:open', false)" class="relative z-40">
      <!-- Backdrop -->
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

      <!-- Drawer panel -->
      <div class="fixed inset-0 overflow-hidden">
        <div class="absolute inset-0 overflow-hidden">
          <div class="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
            <TransitionChild
              as="template"
              enter="transform transition ease-in-out duration-300"
              enter-from="translate-x-full"
              enter-to="translate-x-0"
              leave="transform transition ease-in-out duration-200"
              leave-from="translate-x-0"
              leave-to="translate-x-full"
            >
              <DialogPanel class="pointer-events-auto w-screen max-w-md">
                <div class="flex h-full flex-col bg-white dark:bg-gray-800 shadow-xl">

                  <!-- Header -->
                  <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10 flex-shrink-0">
                    <div class="flex items-start justify-between gap-4">
                      <div class="flex-1 min-w-0">
                        <DialogTitle class="text-base font-semibold text-gray-900 dark:text-white break-all leading-tight">
                          {{ account?.fullPath }}
                        </DialogTitle>
                        <div class="flex items-center gap-2 mt-1.5">
                          <span
                            class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                            :class="account ? typeColors[account.type] : ''"
                          >
                            {{ account?.type }}
                          </span>
                          <span
                            class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                            :class="account ? statusColors[account.status] : ''"
                          >
                            {{ account?.status === 'open' ? 'Open' : 'Closed' }}
                          </span>
                        </div>
                      </div>
                      <button
                        @click="emit('update:open', false)"
                        class="flex-shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 mt-0.5"
                      >
                        <XMarkIcon class="h-5 w-5" />
                      </button>
                    </div>
                  </div>

                  <!-- Scrollable content -->
                  <div v-if="account" class="flex-1 overflow-y-auto px-6 py-5 space-y-6">

                    <!-- Core Info -->
                    <section>
                      <dl class="grid grid-cols-2 gap-x-6 gap-y-4">
                        <div>
                          <dt class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Opened</dt>
                          <dd class="mt-1 text-sm text-gray-900 dark:text-white">{{ account.openDate || '—' }}</dd>
                        </div>
                        <div>
                          <dt class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Closed</dt>
                          <dd class="mt-1 text-sm text-gray-900 dark:text-white">{{ account.closeDate || '—' }}</dd>
                        </div>
                        <div class="col-span-2">
                          <dt class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-1">Currencies</dt>
                          <dd class="flex flex-wrap gap-1">
                            <span
                              v-for="c in account.declaredCurrencies"
                              :key="c"
                              class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
                            >{{ c }}</span>
                            <span v-if="account.declaredCurrencies.length === 0" class="text-sm text-gray-400 dark:text-gray-500">Any</span>
                          </dd>
                        </div>
                      </dl>
                    </section>

                    <!-- Balance -->
                    <section v-if="account.aggregatedBalances.length > 0">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Balance</h3>
                      <div class="space-y-2">
                        <div
                          v-for="bal in account.aggregatedBalances"
                          :key="bal.currency"
                          class="flex justify-between items-center text-sm"
                        >
                          <span class="text-gray-500 dark:text-gray-400">{{ bal.currency }}</span>
                          <span
                            class="font-medium tabular-nums"
                            :class="sign(bal.balance) < 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-white'"
                          >
                            {{ toNumber(bal.balance).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
                          </span>
                        </div>
                      </div>
                    </section>

                    <!-- Banking Details -->
                    <section v-if="hasBankingDetails">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Banking Details</h3>
                      <dl class="space-y-2">
                        <div v-if="account.metadata['account_number']" class="flex justify-between items-center gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">Account Number</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white font-mono text-right">{{ account.metadata['account_number'] }}</dd>
                        </div>
                        <div v-if="account.metadata['ifsc_code']" class="flex justify-between items-center gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">IFSC Code</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white font-mono text-right">{{ account.metadata['ifsc_code'] }}</dd>
                        </div>
                        <div v-if="account.metadata['swift_bic']" class="flex justify-between items-center gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">SWIFT / BIC</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white font-mono text-right">{{ account.metadata['swift_bic'] }}</dd>
                        </div>
                      </dl>
                    </section>

                    <!-- Custom Fields -->
                    <section v-if="customFields.length > 0">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Custom Fields</h3>
                      <dl class="space-y-2">
                        <div v-for="field in customFields" :key="field.key" class="flex justify-between items-start gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">{{ formatFieldKey(field.key) }}</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white text-right">{{ field.value }}</dd>
                        </div>
                      </dl>
                    </section>

                    <!-- Notes -->
                    <section v-if="account.notes">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Notes</h3>
                      <p class="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{{ account.notes }}</p>
                    </section>

                    <!-- Empty metadata state -->
                    <p
                      v-if="!hasBankingDetails && !customFields.length && !account.notes"
                      class="text-sm text-gray-400 dark:text-gray-500 italic"
                    >
                      No additional details. Click Edit to add metadata.
                    </p>

                  </div>

                  <!-- Footer -->
                  <div class="px-6 py-4 border-t border-gray-200 dark:border-white/10 flex-shrink-0">
                    <button
                      @click="account && emit('edit', account)"
                      class="w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
                    >
                      Edit Account
                    </button>
                  </div>

                </div>
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'
import { typeColors, statusColors } from '@/types/accounts'
import { sign, toNumber } from '@/utils/money'

const BANKING_KEYS = ['account_number', 'ifsc_code', 'swift_bic']
const RESERVED_KEYS = new Set(['description', ...BANKING_KEYS])

interface Props {
  open: boolean
  account: AccountTreeNode | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
  (e: 'edit', account: AccountTreeNode): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const hasBankingDetails = computed(() =>
  BANKING_KEYS.some(k => props.account?.metadata[k])
)

const customFields = computed(() => {
  if (!props.account) return []
  return Object.entries(props.account.metadata)
    .filter(([k]) => !RESERVED_KEYS.has(k))
    .map(([key, value]) => ({ key, value }))
})

function formatFieldKey(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}
</script>
