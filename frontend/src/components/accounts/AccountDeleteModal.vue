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
            <DialogPanel class="w-full max-w-md transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 p-6 text-left shadow-xl transition-all">
              <div class="flex items-start gap-4">
                <div class="flex-shrink-0 flex items-center justify-center h-10 w-10 rounded-full bg-red-100 dark:bg-red-900/30">
                  <ExclamationTriangleIcon class="h-6 w-6 text-red-600 dark:text-red-400" />
                </div>
                <div class="flex-1">
                  <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-white">
                    Delete Account
                  </DialogTitle>
                  <div class="mt-2">
                    <p class="text-sm text-gray-600 dark:text-gray-400">
                      Are you sure you want to delete this account?
                    </p>
                    <p class="mt-2 font-medium text-gray-900 dark:text-white">
                      {{ account?.fullPath }}
                    </p>
                  </div>
                </div>
              </div>

              <!-- Transaction count warning -->
              <div v-if="transactionCount > 0" class="mt-4 p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-md">
                <p class="text-sm text-amber-800 dark:text-amber-300">
                  This account has <strong>{{ transactionCount }}</strong> associated transaction{{ transactionCount > 1 ? 's' : '' }}.
                </p>
              </div>

              <!-- Delete transactions checkbox -->
              <div v-if="transactionCount > 0" class="mt-4">
                <label class="flex items-start gap-3 cursor-pointer">
                  <input
                    v-model="deleteTransactions"
                    type="checkbox"
                    class="mt-1 h-4 w-4 rounded border-gray-300 text-red-600 focus:outline-red-600 dark:border-white/10 dark:bg-white/5"
                  />
                  <span class="text-sm text-gray-700 dark:text-gray-300">
                    Also delete {{ transactionCount }} associated transaction{{ transactionCount > 1 ? 's' : '' }}
                  </span>
                </label>
              </div>

              <!-- Warning text -->
              <div class="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md">
                <p class="text-sm text-red-800 dark:text-red-300">
                  <strong>Warning:</strong> This action cannot be undone.
                  <span v-if="deleteTransactions && transactionCount > 0">
                    All {{ transactionCount }} transaction{{ transactionCount > 1 ? 's' : '' }} will be permanently deleted.
                  </span>
                </p>
              </div>

              <!-- Buttons -->
              <div class="mt-6 flex justify-end gap-3">
                <button
                  type="button"
                  @click="handleClose"
                  class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  @click="handleDelete"
                  :disabled="isDeleting"
                  class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {{ isDeleting ? 'Deleting...' : 'Delete' }}
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
import { ref, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { ExclamationTriangleIcon } from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'

interface Props {
  open: boolean
  account: AccountTreeNode | null
  transactionCount: number
}

interface Emits {
  (e: 'update:open', value: boolean): void
  (e: 'submit', data: { deleteTransactions: boolean }): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const deleteTransactions = ref(true)
const isDeleting = ref(false)

// Reset checkbox when modal opens
watch(() => props.open, (isOpen) => {
  if (isOpen) {
    deleteTransactions.value = true
  }
})

function handleDelete() {
  isDeleting.value = true
  emit('submit', { deleteTransactions: deleteTransactions.value })
  // Note: isDeleting will be reset when modal closes
}

function handleClose() {
  isDeleting.value = false
  emit('update:open', false)
}
</script>
