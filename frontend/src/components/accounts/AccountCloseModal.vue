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
            <DialogPanel class="w-full max-w-md transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 p-4 sm:p-6 text-left shadow-xl transition-all">
              <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-4">
                Close Account
              </DialogTitle>

              <div class="mb-4">
                <p class="text-sm text-gray-600 dark:text-gray-400">
                  You are about to close the account:
                </p>
                <p class="mt-1 font-medium text-gray-900 dark:text-white break-all">
                  {{ account?.fullPath }}
                </p>
              </div>

              <form @submit.prevent="handleSubmit" class="space-y-4">
                <!-- Close Date -->
                <div>
                  <label for="closeDate" class="block text-sm/6 font-medium text-gray-900 dark:text-white">
                    Close Date
                  </label>
                  <input
                    id="closeDate"
                    v-model="closeDate"
                    type="date"
                    :min="account?.openDate || undefined"
                    class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    :class="{ 'border-red-500': error }"
                  />
                  <p v-if="error" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ error }}
                  </p>
                  <p v-if="account?.openDate" class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Account opened on {{ account.openDate }}
                  </p>
                </div>

                <!-- Reason (optional) -->
                <div>
                  <label for="reason" class="block text-sm/6 font-medium text-gray-900 dark:text-white">
                    Reason (optional)
                  </label>
                  <textarea
                    id="reason"
                    v-model="reason"
                    rows="2"
                    placeholder="Why is this account being closed?"
                    class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                  ></textarea>
                </div>

                <!-- Buttons -->
                <div class="flex justify-end gap-3 pt-4">
                  <button
                    type="button"
                    @click="handleClose"
                    class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    :disabled="isSubmitting"
                    class="px-4 py-2 text-sm font-medium text-white bg-amber-600 rounded-md hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {{ isSubmitting ? 'Closing...' : 'Close Account' }}
                  </button>
                </div>
              </form>
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
import type { AccountTreeNode } from '@/types/accounts'

interface Props {
  open: boolean
  account: AccountTreeNode | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
  (e: 'submit', data: { closeDate: string; reason?: string }): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const closeDate = ref(new Date().toISOString().split('T')[0])
const reason = ref('')
const error = ref('')
const isSubmitting = ref(false)

// Reset form when modal opens
watch(() => props.open, (isOpen) => {
  if (isOpen) {
    closeDate.value = new Date().toISOString().split('T')[0]
    reason.value = ''
    error.value = ''
  }
})

function validate(): boolean {
  error.value = ''

  if (!closeDate.value) {
    error.value = 'Close date is required'
    return false
  }

  if (props.account?.openDate && closeDate.value < props.account.openDate) {
    error.value = 'Close date must be after open date'
    return false
  }

  return true
}

async function handleSubmit() {
  if (!validate()) return

  isSubmitting.value = true
  try {
    emit('submit', {
      closeDate: closeDate.value,
      reason: reason.value || undefined
    })
  } finally {
    isSubmitting.value = false
  }
}

function handleClose() {
  emit('update:open', false)
}
</script>
