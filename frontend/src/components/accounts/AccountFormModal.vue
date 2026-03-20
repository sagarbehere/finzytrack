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
              <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-4">
                {{ mode === 'create' ? 'Create Account' : 'Edit Account' }}
              </DialogTitle>

              <form @submit.prevent="handleSubmit" class="space-y-4">
                <!-- Account Name -->
                <div>
                  <label for="name" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Account Name
                  </label>
                  <input
                    id="name"
                    v-model="formData.name"
                    type="text"
                    :disabled="mode === 'edit'"
                    placeholder="e.g., Assets:Bank:Checking"
                    class="block w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white disabled:bg-gray-100 disabled:dark:bg-gray-800 disabled:cursor-not-allowed"
                    :class="{ 'border-red-500': errors.name }"
                  />
                  <p v-if="errors.name" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ errors.name }}
                  </p>
                  <p v-else class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Format: Type:Category:SubCategory (e.g., Assets:Bank:Checking)
                  </p>
                </div>

                <!-- Open Date -->
                <div>
                  <label for="openDate" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Open Date
                  </label>
                  <input
                    id="openDate"
                    v-model="formData.openDate"
                    type="date"
                    class="block w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                    :class="{ 'border-red-500': errors.openDate }"
                  />
                  <p v-if="errors.openDate" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ errors.openDate }}
                  </p>
                </div>

                <!-- Currencies -->
                <div>
                  <label for="currencies" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Currencies
                  </label>
                  <div class="flex flex-wrap gap-2 mb-2">
                    <span
                      v-for="currency in formData.currencies"
                      :key="currency"
                      class="inline-flex items-center gap-1 px-2 py-1 rounded bg-blue-100 text-blue-800 text-sm dark:bg-blue-900/30 dark:text-blue-400"
                    >
                      {{ currency }}
                      <button
                        type="button"
                        @click="removeCurrency(currency)"
                        class="hover:text-blue-600 dark:hover:text-blue-300"
                      >
                        <XMarkIcon class="h-4 w-4" />
                      </button>
                    </span>
                  </div>
                  <div class="flex gap-2">
                    <div class="flex-1">
                      <CommodityDropdown
                        v-model="currencyInput"
                        placeholder="Add currency (e.g., USD)"
                        :allow-custom="true"
                      />
                    </div>
                    <button
                      type="button"
                      @click="addCurrency"
                      class="px-3 py-2 bg-gray-100 text-gray-700 rounded-md text-sm hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600"
                    >
                      Add
                    </button>
                  </div>
                  <p v-if="errors.currencies" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ errors.currencies }}
                  </p>
                </div>

                <!-- Description -->
                <div>
                  <label for="description" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Description (optional)
                  </label>
                  <textarea
                    id="description"
                    v-model="formData.description"
                    rows="2"
                    placeholder="Account description or notes"
                    class="block w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                  ></textarea>
                </div>

                <!-- Banking Details -->
                <div class="border-t border-gray-200 dark:border-gray-700 pt-4">
                  <h4 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Banking Details (optional)</h4>
                  <div class="space-y-3">
                    <div>
                      <label for="accountNumber" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Account Number</label>
                      <input
                        id="accountNumber"
                        v-model="formData.accountNumber"
                        type="text"
                        placeholder="e.g., 1234567890"
                        class="block w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                      />
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                      <div>
                        <label for="ifscCode" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">IFSC Code</label>
                        <input
                          id="ifscCode"
                          v-model="formData.ifscCode"
                          type="text"
                          placeholder="e.g., HDFC0001234"
                          class="block w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                        />
                      </div>
                      <div>
                        <label for="swiftBic" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">SWIFT / BIC</label>
                        <input
                          id="swiftBic"
                          v-model="formData.swiftBic"
                          type="text"
                          placeholder="e.g., HDFCINBB"
                          class="block w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                        />
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Custom Fields -->
                <div class="border-t border-gray-200 dark:border-gray-700 pt-4">
                  <div class="flex items-center justify-between mb-3">
                    <h4 class="text-sm font-medium text-gray-700 dark:text-gray-300">Custom Fields (optional)</h4>
                    <button
                      type="button"
                      @click="addCustomField"
                      class="text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 flex items-center gap-1"
                    >
                      <PlusIcon class="h-3.5 w-3.5" />
                      Add field
                    </button>
                  </div>
                  <div v-if="formData.customFields.length > 0" class="space-y-2">
                    <div
                      v-for="(field, index) in formData.customFields"
                      :key="index"
                      class="flex gap-2 items-start"
                    >
                      <input
                        v-model="field.key"
                        type="text"
                        placeholder="key"
                        class="w-2/5 px-2.5 py-1.5 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white font-mono"
                        :class="{ 'border-red-400': field.key && !isValidKey(field.key) }"
                      />
                      <input
                        v-model="field.value"
                        type="text"
                        placeholder="value"
                        class="flex-1 px-2.5 py-1.5 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                      />
                      <button
                        type="button"
                        @click="removeCustomField(index)"
                        class="flex-shrink-0 mt-1.5 text-gray-400 hover:text-red-500 dark:hover:text-red-400"
                      >
                        <XMarkIcon class="h-4 w-4" />
                      </button>
                    </div>
                    <p class="text-xs text-gray-400 dark:text-gray-500">Keys must be lowercase letters, numbers, and underscores.</p>
                  </div>
                  <p v-else class="text-xs text-gray-400 dark:text-gray-500">
                    Store arbitrary metadata like branch name, opening date, registered address, etc.
                  </p>
                </div>

                <!-- Buttons -->
                <div class="flex justify-end gap-3 pt-4 border-t border-gray-200 dark:border-gray-700">
                  <button
                    type="button"
                    @click="handleClose"
                    class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600 dark:hover:bg-gray-600"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    :disabled="isSubmitting"
                    class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {{ isSubmitting ? 'Saving...' : (mode === 'create' ? 'Create' : 'Save') }}
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
import { ref, reactive, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon, PlusIcon } from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'

const BANKING_KEYS = new Set(['account_number', 'ifsc_code', 'swift_bic'])
const RESERVED_KEYS = new Set(['description', ...BANKING_KEYS])

interface Props {
  open: boolean
  mode: 'create' | 'edit'
  account?: AccountTreeNode | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
  (e: 'submit', data: FormSubmitData): void
}

export interface FormSubmitData {
  name: string
  openDate: string
  currencies: string[]
  description: string
  metadata: Record<string, string>
}

interface InternalFormData {
  name: string
  openDate: string
  currencies: string[]
  description: string
  accountNumber: string
  ifscCode: string
  swiftBic: string
  customFields: { key: string; value: string }[]
}

const props = withDefaults(defineProps<Props>(), {
  account: null
})
const emit = defineEmits<Emits>()

const isSubmitting = ref(false)
const currencyInput = ref('')

const formData = reactive<InternalFormData>({
  name: '',
  openDate: new Date().toISOString().split('T')[0],
  currencies: [],
  description: '',
  accountNumber: '',
  ifscCode: '',
  swiftBic: '',
  customFields: [],
})

const errors = reactive({
  name: '',
  openDate: '',
  currencies: '',
})

// Watch for account changes to populate edit form
watch(() => props.account, (account) => {
  if (account && props.mode === 'edit') {
    formData.name = account.fullPath
    formData.openDate = account.openDate || new Date().toISOString().split('T')[0]
    formData.currencies = [...account.currencyBadges]
    formData.description = account.notes || ''
    formData.accountNumber = account.metadata['account_number'] || ''
    formData.ifscCode = account.metadata['ifsc_code'] || ''
    formData.swiftBic = account.metadata['swift_bic'] || ''
    formData.customFields = Object.entries(account.metadata)
      .filter(([k]) => !RESERVED_KEYS.has(k))
      .map(([key, value]) => ({ key, value }))
  }
}, { immediate: true })

// Reset form when modal opens in create mode
watch(() => props.open, (isOpen) => {
  if (isOpen && props.mode === 'create') {
    formData.name = ''
    formData.openDate = new Date().toISOString().split('T')[0]
    formData.currencies = []
    formData.description = ''
    formData.accountNumber = ''
    formData.ifscCode = ''
    formData.swiftBic = ''
    formData.customFields = []
    currencyInput.value = ''
    clearErrors()
  }
})

function clearErrors() {
  errors.name = ''
  errors.openDate = ''
  errors.currencies = ''
}

function addCurrency() {
  const currency = currencyInput.value.trim().toUpperCase()
  if (currency && !formData.currencies.includes(currency)) {
    formData.currencies.push(currency)
  }
  currencyInput.value = ''
}

function removeCurrency(currency: string) {
  formData.currencies = formData.currencies.filter(c => c !== currency)
}

function addCustomField() {
  formData.customFields.push({ key: '', value: '' })
}

function removeCustomField(index: number) {
  formData.customFields.splice(index, 1)
}

function isValidKey(key: string): boolean {
  return /^[a-z][a-z0-9_]*$/.test(key)
}

function validate(): boolean {
  clearErrors()
  let valid = true

  if (!formData.name.trim()) {
    errors.name = 'Account name is required'
    valid = false
  } else if (!/^[A-Z][A-Za-z0-9\-_]*(?::[A-Z][A-Za-z0-9\-_]*)+$/.test(formData.name)) {
    errors.name = 'Invalid account format. Must start with a type (Assets, Liabilities, etc.)'
    valid = false
  }

  if (!formData.openDate) {
    errors.openDate = 'Open date is required'
    valid = false
  }

  if (formData.currencies.length === 0) {
    errors.currencies = 'At least one currency is required'
    valid = false
  }

  return valid
}

function buildMetadata(): Record<string, string> {
  const meta: Record<string, string> = {}
  if (formData.accountNumber.trim()) meta['account_number'] = formData.accountNumber.trim()
  if (formData.ifscCode.trim()) meta['ifsc_code'] = formData.ifscCode.trim()
  if (formData.swiftBic.trim()) meta['swift_bic'] = formData.swiftBic.trim()
  for (const field of formData.customFields) {
    if (field.key.trim() && field.value.trim() && isValidKey(field.key.trim())) {
      meta[field.key.trim()] = field.value.trim()
    }
  }
  return meta
}

async function handleSubmit() {
  if (!validate()) return

  isSubmitting.value = true
  try {
    emit('submit', {
      name: formData.name,
      openDate: formData.openDate,
      currencies: formData.currencies,
      description: formData.description,
      metadata: buildMetadata(),
    })
  } finally {
    isSubmitting.value = false
  }
}

function handleClose() {
  emit('update:open', false)
}
</script>
