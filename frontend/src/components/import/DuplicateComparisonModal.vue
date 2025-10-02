<template>
  <TransitionRoot appear :show="isOpen" as="template">
    <Dialog as="div" @close="emit('close')" class="relative z-50">
      <TransitionChild
        as="template"
        enter="duration-300 ease-out"
        enter-from="opacity-0"
        enter-to="opacity-100"
        leave="duration-200 ease-in"
        leave-from="opacity-100"
        leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black bg-opacity-25" />
      </TransitionChild>

      <div class="fixed inset-0 overflow-y-auto">
        <div class="flex min-h-full items-center justify-center p-4 text-center">
          <TransitionChild
            as="template"
            enter="duration-300 ease-out"
            enter-from="opacity-0 scale-95"
            enter-to="opacity-100 scale-100"
            leave="duration-200 ease-in"
            leave-from="opacity-100 scale-100"
            leave-to="opacity-0 scale-95"
          >
            <DialogPanel class="w-full max-w-4xl transform overflow-hidden rounded-2xl bg-white dark:bg-gray-800 p-6 text-left align-middle shadow-xl transition-all">
              <DialogTitle
                as="h3"
                class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-4"
              >
                Potential Duplicate Found
              </DialogTitle>

              <div class="grid grid-cols-2 gap-6">
                <!-- New Transaction -->
                <div class="border border-blue-300 dark:border-blue-600 rounded-lg p-4 bg-blue-50 dark:bg-blue-900/20">
                  <h4 class="font-semibold text-blue-900 dark:text-blue-100 mb-3">New Transaction</h4>
                  <div class="space-y-2 text-sm">
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Date:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ newTransaction.date }}</span>
                    </div>
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Payee:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ newTransaction.payee }}</span>
                    </div>
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Amount:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">
                        {{ formatAmount(newTransaction.postings[0]?.amount) }} {{ newTransaction.postings[0]?.currency }}
                      </span>
                    </div>
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Account:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ newTransaction.postings[0]?.account }}</span>
                    </div>
                  </div>
                </div>

                <!-- Existing Transaction(s) -->
                <div class="border border-orange-300 dark:border-orange-600 rounded-lg p-4 bg-orange-50 dark:bg-orange-900/20">
                  <h4 class="font-semibold text-orange-900 dark:text-orange-100 mb-3">
                    Existing Transaction{{ duplicateMatches.length > 1 ? 's' : '' }}
                    <span v-if="duplicateMatches.length > 1" class="text-xs font-normal">
                      ({{ duplicateMatches.length }} matches)
                    </span>
                  </h4>
                  <div class="space-y-4 max-h-64 overflow-y-auto">
                    <div
                      v-for="(match, index) in duplicateMatches"
                      :key="index"
                      :class="['space-y-2 text-sm', index > 0 ? 'pt-4 border-t border-orange-200 dark:border-orange-700' : '']"
                    >
                      <div v-if="duplicateMatches.length > 1" class="text-xs text-orange-700 dark:text-orange-300 font-semibold mb-2">
                        Match {{ index + 1 }} of {{ duplicateMatches.length }}
                      </div>
                      <div>
                        <span class="text-gray-600 dark:text-gray-400">Date:</span>
                        <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ match.date }}</span>
                      </div>
                      <div>
                        <span class="text-gray-600 dark:text-gray-400">Payee:</span>
                        <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ match.payee }}</span>
                      </div>
                      <div v-if="match.narration">
                        <span class="text-gray-600 dark:text-gray-400">Narration:</span>
                        <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ match.narration }}</span>
                      </div>
                      <div>
                        <span class="text-gray-600 dark:text-gray-400">Amount:</span>
                        <span class="ml-2 font-medium text-gray-900 dark:text-white">
                          {{ formatAmount(match.amount) }}
                        </span>
                      </div>
                      <div>
                        <span class="text-gray-600 dark:text-gray-400">Account:</span>
                        <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ match.account }}</span>
                      </div>
                      <div v-if="match.transaction_id" class="text-xs text-gray-500 dark:text-gray-400">
                        ID: {{ match.transaction_id }}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="mt-6 flex justify-end space-x-3">
                <button
                  type="button"
                  class="inline-flex justify-center rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                  @click="emit('keepTransaction')"
                >
                  Keep This Transaction
                </button>
                <button
                  type="button"
                  class="inline-flex justify-center rounded-md border border-transparent bg-orange-600 px-4 py-2 text-sm font-medium text-white hover:bg-orange-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 focus-visible:ring-offset-2"
                  @click="emit('removeDuplicate')"
                >
                  Remove as Duplicate
                </button>
                <button
                  type="button"
                  class="inline-flex justify-center rounded-md border border-transparent bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2"
                  @click="emit('removeAllDuplicates')"
                >
                  Remove All Duplicates
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
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import type { TransactionViewModel } from '@/types/transactions'
import type { DuplicateInfo } from '@/services/generated-api'

interface Props {
  newTransaction: TransactionViewModel
  duplicateMatches: DuplicateInfo[]
  isOpen: boolean
}

interface Emits {
  (e: 'close'): void
  (e: 'keepTransaction'): void
  (e: 'removeDuplicate'): void
  (e: 'removeAllDuplicates'): void
}

defineProps<Props>()
const emit = defineEmits<Emits>()

function formatAmount(amount: number | string | null | undefined): string {
  if (amount === undefined || amount === null) return '0.00'
  const num = typeof amount === 'string' ? parseFloat(amount) : amount
  return num.toFixed(2)
}
</script>
