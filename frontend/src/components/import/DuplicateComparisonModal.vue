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
            <DialogPanel class="w-full max-w-4xl transform overflow-hidden rounded-2xl bg-white dark:bg-gray-800 p-6 text-left align-middle shadow-xl transition-all relative">
              <!-- Close X button -->
              <button
                @click="emit('close')"
                class="absolute top-4 right-4 text-gray-400 hover:text-gray-500 dark:hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:focus-visible:outline-indigo-500"
              >
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>

              <DialogTitle
                as="h3"
                class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-4 pr-8"
              >
                Potential Duplicate Found
                <span v-if="totalDuplicates > 1" class="text-sm font-normal text-gray-600 dark:text-gray-400">
                  ({{ currentIndex + 1 }} of {{ totalDuplicates }})
                </span>
              </DialogTitle>

              <div class="grid grid-cols-2 gap-6">
                <!-- New Transaction -->
                <div class="border border-indigo-300 dark:border-indigo-600 rounded-lg p-4 bg-indigo-50 dark:bg-indigo-900/20">
                  <h4 class="font-semibold text-indigo-900 dark:text-indigo-100 mb-3">New Transaction</h4>
                  <div class="space-y-2 text-sm">
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Date:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ currentTransaction.date }}</span>
                    </div>
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Payee:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ currentTransaction.payee }}</span>
                    </div>
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Amount:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">
                        {{ formatAmount(currentTransaction.postings[0]?.amount) }} {{ currentTransaction.postings[0]?.currency }}
                      </span>
                    </div>
                    <div>
                      <span class="text-gray-600 dark:text-gray-400">Account:</span>
                      <span class="ml-2 font-medium text-gray-900 dark:text-white">{{ currentTransaction.postings[0]?.account }}</span>
                    </div>
                  </div>
                </div>

                <!-- Existing Transaction(s) -->
                <div class="border border-orange-300 dark:border-orange-600 rounded-lg p-4 bg-orange-50 dark:bg-orange-900/20">
                  <h4 class="font-semibold text-orange-900 dark:text-orange-100 mb-3">
                    Existing Transaction{{ currentDuplicateMatches.length > 1 ? 's' : '' }}
                    <span v-if="currentDuplicateMatches.length > 1" class="text-xs font-normal">
                      ({{ currentDuplicateMatches.length }} matches)
                    </span>
                  </h4>
                  <div class="space-y-4 max-h-64 overflow-y-auto">
                    <div
                      v-for="(match, index) in currentDuplicateMatches"
                      :key="index"
                      :class="['space-y-2 text-sm', index > 0 ? 'pt-4 border-t border-orange-200 dark:border-orange-700' : '']"
                    >
                      <div v-if="currentDuplicateMatches.length > 1" class="text-xs text-orange-700 dark:text-orange-300 font-semibold mb-2">
                        Match {{ index + 1 }} of {{ currentDuplicateMatches.length }}
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
                      <div class="text-xs text-gray-500 dark:text-gray-400 space-y-1">
                        <div>ID: {{ match.id }}</div>
                        <div v-if="match.match_type" class="text-xs">
                          Match: {{ match.match_type }}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Navigation and Action Buttons -->
              <div class="mt-6 flex items-center justify-between">
                <!-- Left Navigation Arrow -->
                <button
                  v-if="totalDuplicates > 1"
                  type="button"
                  :disabled="currentIndex === 0"
                  @click="previousDuplicate"
                  :class="[
                    'inline-flex items-center justify-center rounded-md p-2 text-gray-700 dark:text-gray-200',
                    currentIndex === 0
                      ? 'opacity-50 cursor-not-allowed'
                      : 'hover:bg-gray-100 dark:hover:bg-white/5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:focus-visible:outline-indigo-500'
                  ]"
                >
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
                  </svg>
                </button>
                <div v-else class="w-10"></div>

                <!-- Action Buttons -->
                <div class="flex space-x-3">
                  <button
                    type="button"
                    class="inline-flex justify-center rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                    @click="handleKeep"
                  >
                    Keep This Transaction
                  </button>
                  <button
                    type="button"
                    class="inline-flex justify-center rounded-md bg-orange-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-orange-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-600 dark:bg-orange-500 dark:shadow-none dark:hover:bg-orange-400 dark:focus-visible:outline-orange-500"
                    @click="handleRemove"
                  >
                    Remove as Duplicate
                  </button>
                </div>

                <!-- Right Navigation Arrow -->
                <button
                  v-if="totalDuplicates > 1"
                  type="button"
                  :disabled="currentIndex === totalDuplicates - 1"
                  @click="nextDuplicate"
                  :class="[
                    'inline-flex items-center justify-center rounded-md p-2 text-gray-700 dark:text-gray-200',
                    currentIndex === totalDuplicates - 1
                      ? 'opacity-50 cursor-not-allowed'
                      : 'hover:bg-gray-100 dark:hover:bg-white/5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:focus-visible:outline-indigo-500'
                  ]"
                >
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                  </svg>
                </button>
                <div v-else class="w-10"></div>
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
import type { TransactionViewModel } from '@/types/transactions'
import type { DuplicateInfo } from '@/services/generated-api'

interface DuplicateTransaction {
  transaction: TransactionViewModel
  duplicateMatches: DuplicateInfo[]
}

interface Props {
  duplicateTransactions: DuplicateTransaction[]
  isOpen: boolean
  initialIndex?: number
}

interface Emits {
  (e: 'close'): void
  (e: 'keepTransaction', transactionId: string): void
  (e: 'removeDuplicate', transactionId: string): void
}

const props = withDefaults(defineProps<Props>(), {
  initialIndex: 0
})
const emit = defineEmits<Emits>()

// Current index in the duplicate transactions list
const currentIndex = ref(props.initialIndex)

// Watch for changes to initialIndex when modal reopens
watch(() => props.initialIndex, (newIndex) => {
  currentIndex.value = newIndex
})

// Computed properties
const totalDuplicates = computed(() => props.duplicateTransactions.length)

const currentTransaction = computed(() => {
  return props.duplicateTransactions[currentIndex.value]?.transaction
})

const currentDuplicateMatches = computed(() => {
  return props.duplicateTransactions[currentIndex.value]?.duplicateMatches || []
})

// Navigation functions
function previousDuplicate() {
  if (currentIndex.value > 0) {
    currentIndex.value--
  }
}

function nextDuplicate() {
  if (currentIndex.value < totalDuplicates.value - 1) {
    currentIndex.value++
  }
}

// Action handlers
function handleKeep() {
  if (currentTransaction.value) {
    emit('keepTransaction', currentTransaction.value.id)
    // Auto-advance to next duplicate or close if this was the last one
    if (currentIndex.value < totalDuplicates.value - 1) {
      currentIndex.value++
    } else {
      emit('close')
    }
  }
}

function handleRemove() {
  if (currentTransaction.value) {
    emit('removeDuplicate', currentTransaction.value.id)
    // Modal will automatically update when the transaction is removed from the list
    // If this was the last duplicate, the modal will close
  }
}

function formatAmount(amount: number | string | null | undefined): string {
  if (amount === undefined || amount === null) return '0.00'
  const num = typeof amount === 'string' ? parseFloat(amount) : amount
  return num.toFixed(2)
}
</script>
