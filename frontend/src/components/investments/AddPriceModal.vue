<template>
  <TransitionRoot :show="open" as="template">
    <Dialog class="relative z-40" @close="emit('update:open', false)">
      <TransitionChild
        as="template"
        enter="ease-out duration-300" enter-from="opacity-0" enter-to="opacity-100"
        leave="ease-in duration-200" leave-from="opacity-100" leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-gray-500/75 transition-opacity dark:bg-gray-900/50" />
      </TransitionChild>

      <div class="fixed inset-0 z-10 w-screen overflow-y-auto">
        <div class="flex min-h-full items-end justify-center p-4 text-center sm:items-center sm:p-0">
          <TransitionChild
            as="template"
            enter="ease-out duration-300" enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95" enter-to="opacity-100 translate-y-0 sm:scale-100"
            leave="ease-in duration-200" leave-from="opacity-100 translate-y-0 sm:scale-100" leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
          >
            <DialogPanel class="relative transform rounded-lg bg-white px-4 pt-5 pb-4 text-left shadow-xl transition-all sm:my-8 sm:w-full sm:max-w-lg sm:p-6 dark:bg-gray-800 dark:outline dark:-outline-offset-1 dark:outline-white/10">
              <DialogTitle class="text-base font-semibold text-gray-900 dark:text-white">Add price</DialogTitle>
              <p class="mt-2 text-sm text-gray-500 dark:text-gray-400">
                The price of one unit on a date.
              </p>
              <div class="mt-5">
                <PriceForm
                  :default-quote="defaultQuote"
                  :saving="saving"
                  :cancelable="true"
                  @submit="submit"
                  @cancel="emit('update:open', false)"
                />
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>

      <ConfirmDialog
        :is-open="confirm.isOpen.value"
        :title="confirm.dialogOptions.value.title"
        :message="confirm.dialogOptions.value.message"
        :confirm-text="confirm.dialogOptions.value.confirmText"
        :cancel-text="confirm.dialogOptions.value.cancelText"
        :variant="confirm.dialogOptions.value.variant"
        @confirm="confirm.handleConfirm"
        @cancel="confirm.handleCancel"
        @close="confirm.handleClose"
      />
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import type { PriceEditRequest } from '@/services/generated-api'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import PriceForm from '@/components/investments/PriceForm.vue'
import { useSaveAskingToReplace, type SaveOutcome } from '@/composables/usePrices'
import { addRequest, type PriceInput } from '@/utils/prices'

const props = defineProps<{
  open: boolean
  defaultQuote: string
  saving: boolean
  today: string
  save: (request: PriceEditRequest) => Promise<SaveOutcome>
}>()
const emit = defineEmits<{
  'update:open': [value: boolean]
  saved: [input: PriceInput, replaced: boolean]
}>()

const { confirm, saveAsking } = useSaveAskingToReplace(props.save, () => props.today)

async function submit(input: PriceInput) {
  const result = await saveAsking(addRequest(input))
  if (result) emit('saved', input, result.replaced)
}
</script>
