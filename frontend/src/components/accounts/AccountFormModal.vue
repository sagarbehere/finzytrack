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
            <DialogPanel class="w-full max-w-lg transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 p-4 sm:p-6 text-left shadow-xl transition-all">
              <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-4">
                {{ mode === 'create' ? 'Create Account' : 'Edit Account' }}
              </DialogTitle>

              <form @submit.prevent="handleSubmit" class="space-y-4">
                <!-- Account Name -->
                <div>
                  <label for="name" class="block text-sm/6 font-medium text-gray-900 dark:text-white">
                    Account Name
                  </label>
                  <input
                    id="name"
                    v-model="formData.name"
                    type="text"
                    placeholder="e.g., Assets:Bank:Checking"
                    class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    :class="{ 'border-red-500': errors.name }"
                  />
                  <p v-if="errors.name" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ errors.name }}
                  </p>
                  <p v-else-if="mode === 'edit' && formData.name !== originalName" class="mt-1 text-xs text-amber-600 dark:text-amber-400">
                    Renaming will update all transactions referencing this account.
                  </p>
                  <p v-else class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Format: Type:Category:SubCategory (e.g., Assets:Bank:Checking)
                  </p>
                </div>

                <!-- Open Date -->
                <div>
                  <label for="openDate" class="block text-sm/6 font-medium text-gray-900 dark:text-white">
                    Open Date
                  </label>
                  <input
                    id="openDate"
                    v-model="formData.openDate"
                    type="date"
                    class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    :class="{ 'border-red-500': errors.openDate }"
                  />
                  <p v-if="errors.openDate" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ errors.openDate }}
                  </p>
                </div>

                <!-- Close Date (edit mode, closed accounts only) -->
                <div v-if="mode === 'edit' && account?.closeDate">
                  <label for="closeDate" class="block text-sm/6 font-medium text-gray-900 dark:text-white">
                    Close Date
                  </label>
                  <input
                    id="closeDate"
                    v-model="formData.closeDate"
                    type="date"
                    class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    :class="{ 'border-red-500': errors.closeDate }"
                  />
                  <p v-if="errors.closeDate" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ errors.closeDate }}
                  </p>
                </div>

                <!-- Currencies -->
                <div>
                  <label for="currencies" class="block text-sm/6 font-medium text-gray-900 dark:text-white">
                    Currencies
                  </label>
                  <div class="flex flex-wrap gap-2 mb-2">
                    <span
                      v-for="currency in formData.currencies"
                      :key="currency"
                      class="inline-flex items-center gap-1 px-2 py-1 rounded bg-indigo-100 text-indigo-800 text-sm dark:bg-indigo-900/30 dark:text-indigo-400"
                    >
                      {{ currency }}
                      <button
                        type="button"
                        @click="removeCurrency(currency)"
                        class="hover:text-indigo-600 dark:hover:text-indigo-300"
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
                      class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                    >
                      Add
                    </button>
                  </div>
                  <p v-if="!errors.currencies" class="mt-1 text-sm text-gray-500 dark:text-gray-400">
                    Leave empty to allow any currency or security, for example in a brokerage account.
                  </p>
                  <p v-if="errors.currencies" class="mt-1 text-sm text-red-600 dark:text-red-400">
                    {{ errors.currencies }}
                  </p>
                </div>

                <!-- Description -->
                <div>
                  <label for="description" class="block text-sm/6 font-medium text-gray-900 dark:text-white">
                    Description (optional)
                  </label>
                  <textarea
                    id="description"
                    v-model="formData.description"
                    rows="2"
                    placeholder="Account description or notes"
                    class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                  ></textarea>
                </div>

                <!-- Banking Details -->
                <div class="border-t border-gray-200 dark:border-white/10 pt-4">
                  <h4 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Banking Details (optional)</h4>
                  <div class="space-y-3">
                    <div>
                      <label for="accountNumber" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Account Number</label>
                      <input
                        id="accountNumber"
                        v-model="formData.accountNumber"
                        type="text"
                        placeholder="e.g., 1234567890"
                        class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                      />
                    </div>
                    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div>
                        <label for="ifscCode" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">IFSC Code</label>
                        <input
                          id="ifscCode"
                          v-model="formData.ifscCode"
                          type="text"
                          placeholder="e.g., HDFC0001234"
                          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                        />
                      </div>
                      <div>
                        <label for="swiftBic" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">SWIFT / BIC</label>
                        <input
                          id="swiftBic"
                          v-model="formData.swiftBic"
                          type="text"
                          placeholder="e.g., HDFCINBB"
                          class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                        />
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Investment details: the recognized keys for this account, typed -->
                <div v-if="typedFields.length > 0" class="border-t border-gray-200 dark:border-white/10 pt-4">
                  <h4 class="text-sm font-medium text-gray-700 dark:text-gray-300">Investment details (optional)</h4>
                  <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">Each one adds detail to the investment dashboards.</p>
                  <div class="mt-3 space-y-3">
                    <div v-for="f in shownTypedFields" :key="f.key">
                      <label
                        :for="fieldApplies(f, formData.investment) && ['date', 'text', 'percent', 'integer'].includes(f.kind) ? `inv-${f.key}` : undefined"
                        class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
                      >{{ f.label }}</label>
                      <!-- Set, but not used for this type: shown read-only so it can be cleared -->
                      <div v-if="!fieldApplies(f, formData.investment)" class="flex items-center gap-2">
                        <span class="flex-1 text-sm text-gray-500 dark:text-gray-400">{{ formData.investment[f.key] }}</span>
                        <button
                          type="button"
                          class="text-xs font-medium text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300"
                          @click="formData.investment[f.key] = ''"
                        >
                          Clear
                        </button>
                      </div>
                      <p v-if="!fieldApplies(f, formData.investment)" class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        Not used when {{ f.requires_key === 'income-type' ? 'income type' : 'account type' }} is {{ formData.investment[f.requires_key ?? ''] || 'not set' }}.
                      </p>
                      <template v-else>
                      <ValueCombobox
                        v-if="f.kind === 'enum'"
                        :model-value="formData.investment[f.key] ?? ''"
                        :options="f.values ?? []"
                        placeholder="Not set"
                        :aria-label="f.label"
                        @update:model-value="v => (formData.investment[f.key] = canonicalValue(f, v))"
                      />
                      <input
                        v-else-if="f.kind === 'date'"
                        :id="`inv-${f.key}`"
                        v-model="formData.investment[f.key]"
                        type="date"
                        class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                      />
                      <div v-else-if="f.kind === 'account'" class="flex items-center gap-2">
                        <div class="flex-1">
                          <AccountDropdown
                            v-model="formData.investment[f.key]"
                            placeholder="Not set"
                            :account-types="['Income']"
                            :open-only="false"
                            :allow-custom="true"
                          />
                        </div>
                        <button
                          v-if="formData.investment[f.key]"
                          type="button"
                          :aria-label="`Clear ${f.label}`"
                          class="text-gray-400 hover:text-red-500 dark:hover:text-red-400"
                          @click="formData.investment[f.key] = ''"
                        >
                          <XMarkIcon class="h-4 w-4" />
                        </button>
                      </div>
                      <CommodityDropdown
                        v-else-if="f.kind === 'commodity'"
                        v-model="formData.investment[f.key]"
                        placeholder="Not set"
                        :allow-custom="true"
                        :clearable="true"
                      />
                      <input
                        v-else
                        :id="`inv-${f.key}`"
                        v-model="formData.investment[f.key]"
                        type="text"
                        placeholder="Not set"
                        class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
                      />
                      <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ f.adds }}</p>
                      </template>
                    </div>
                  </div>
                </div>

                <!-- Custom Fields -->
                <div class="border-t border-gray-200 dark:border-white/10 pt-4">
                  <div class="flex items-center justify-between mb-3">
                    <h4 class="text-sm font-medium text-gray-700 dark:text-gray-300">Custom Fields (optional)</h4>
                    <button
                      type="button"
                      @click="addCustomField"
                      class="text-xs text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300 flex items-center gap-1"
                    >
                      <PlusIcon class="h-3.5 w-3.5" />
                      Add field
                    </button>
                  </div>
                  <div v-if="formData.customFields.length > 0" class="space-y-2">
                    <div
                      v-for="(field, index) in formData.customFields"
                      :key="index"
                      class="flex flex-wrap gap-2 items-start sm:flex-nowrap"
                    >
                      <input
                        v-model="field.key"
                        type="text"
                        placeholder="key"
                        class="w-full sm:w-2/5 rounded-md bg-white px-2.5 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 font-mono placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                        :class="{ 'border-red-400': field.key && !isValidKey(field.key) }"
                      />
                      <input
                        v-model="field.value"
                        type="text"
                        placeholder="value"
                        class="flex-1 rounded-md bg-white px-2.5 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                      />
                      <button
                        type="button"
                        @click="removeCustomField(index)"
                        class="flex-shrink-0 mt-1.5 text-gray-400 hover:text-red-500 dark:hover:text-red-400"
                      >
                        <XMarkIcon class="h-4 w-4" />
                      </button>
                    </div>
                    <p v-if="errors.customFields" class="text-xs text-red-600 dark:text-red-400">{{ errors.customFields }}</p>
                    <p v-else class="text-xs text-gray-400 dark:text-gray-500">Keys start with a lowercase letter and use letters, numbers, '-' and '_'.</p>
                  </div>
                  <p v-else class="text-xs text-gray-400 dark:text-gray-500">
                    Store arbitrary metadata like branch name, opening date, registered address, etc.
                  </p>
                </div>

                <!-- Buttons -->
                <div class="flex justify-end gap-3 pt-4 border-t border-gray-200 dark:border-white/10">
                  <button
                    type="button"
                    @click="handleClose"
                    class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    :disabled="isSubmitting || (mode === 'edit' && isLoadingAccount)"
                    class="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
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
import { computed, ref, reactive, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon, PlusIcon } from '@heroicons/vue/24/outline'
import type { AccountFormSubmitData, AccountTreeNode } from '@/types/accounts'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import ValueCombobox from '@/components/common/ValueCombobox.vue'
import { useCommodities } from '@/composables/useCommodities'
import { useMetadataVocabulary } from '@/composables/useMetadataVocabulary'
import { MetadataService } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { canonicalValue, fieldApplies, fieldsFor, type MetadataScope } from '@/utils/metadataVocabulary'

const BANKING_KEYS = new Set(['account_number', 'ifsc_code', 'swift_bic'])
const RESERVED_KEYS = new Set(['description', ...BANKING_KEYS])

interface Props {
  open: boolean
  mode: 'create' | 'edit'
  account?: AccountTreeNode | null
  // The account's recognized-metadata scope when the caller already knows it
  // (the Setup & Metadata review). Undefined → looked up from the review.
  investmentScope?: MetadataScope | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
  (e: 'submit', data: FormSubmitData): void
}

export type FormSubmitData = AccountFormSubmitData

interface InternalFormData {
  name: string
  openDate: string
  closeDate: string
  currencies: string[]
  description: string
  accountNumber: string
  ifscCode: string
  swiftBic: string
  customFields: { key: string; value: string }[]
  // Recognized investment keys (dev-docs/investment-view.md §10.3), edited as typed fields.
  investment: Record<string, string>
}

const props = withDefaults(defineProps<Props>(), {
  account: null,
  investmentScope: undefined,
})
const emit = defineEmits<Emits>()

const isSubmitting = ref(false)
const isLoadingAccount = ref(false)
const currencyInput = ref('')
const originalName = ref('')

const formData = reactive<InternalFormData>({
  name: '',
  openDate: new Date().toISOString().split('T')[0],
  closeDate: '',
  currencies: [],
  description: '',
  accountNumber: '',
  ifscCode: '',
  swiftBic: '',
  customFields: [],
  investment: {},
})

const errors = reactive({
  name: '',
  openDate: '',
  closeDate: '',
  currencies: '',
  customFields: '',
})

const { fields: vocabularyFields, fetchVocabulary } = useMetadataVocabulary()
const { commodityDetails, fetchCommodities } = useCommodities()

/**
 * Which recognized-metadata scope a NEW account belongs to: Income accounts, and
 * Assets accounts that hold only currencies (account-type describes
 * a currency balance; a brokerage account's holdings carry their own). An
 * existing account's scope comes from the backend review instead, so it follows
 * the same rule as the Setup & Metadata tab (including the held-at-cost check).
 */
function scopeFor(name: string, currencies: string[]): MetadataScope | null {
  const root = name.split(':')[0]
  if (root === 'Income') return 'income-account'
  if (root !== 'Assets') return null
  const isCurrency = new Map(commodityDetails.value.map(c => [c.code, c.is_currency]))
  return currencies.every(c => isCurrency.get(c) ?? true) ? 'account' : null
}

const editScope = ref<MetadataScope | null>(null)

async function lookUpScope(name: string): Promise<MetadataScope | null> {
  if (props.investmentScope !== undefined) return props.investmentScope
  try {
    const review = await MetadataService.getMetadataReview()
    const item = review.data?.items.find(i => i.item === name && i.scope !== 'commodity')
    return (item?.scope as MetadataScope | undefined) ?? null
  } catch (err) {
    errorHandler.display(err)
    return null
  }
}

const typedFields = computed(() => {
  const scope = props.mode === 'edit' ? editScope.value : scopeFor(formData.name, formData.currencies)
  return scope ? fieldsFor(vocabularyFields.value, scope) : []
})

// Fields that apply now, plus any that hold a value but no longer apply (e.g.
// maturity_date after the type changed from CD) — kept visible so they can be cleared.
const shownTypedFields = computed(() =>
  typedFields.value.filter(f => fieldApplies(f, formData.investment) || !!formData.investment[f.key]),
)

// Watch for account changes to populate edit form
watch(() => props.account, async (account) => {
  if (account && props.mode === 'edit') {
    // Save stays disabled until this account's form is filled; a result for an
    // account that is no longer selected is dropped.
    isLoadingAccount.value = true
    const [, , scope] = await Promise.all([fetchVocabulary(), fetchCommodities(), lookUpScope(account.fullPath)])
    if (props.account !== account) return
    isLoadingAccount.value = false
    editScope.value = scope
    const typedKeys = new Set(scope ? fieldsFor(vocabularyFields.value, scope).map(f => f.key) : [])
    originalName.value = account.fullPath
    formData.name = account.fullPath
    formData.openDate = account.openDate || new Date().toISOString().split('T')[0]
    formData.closeDate = account.closeDate || ''
    // Edit the DECLARED currency constraint (the `open` directive), which is
    // period-independent — NOT the currencies that happen to have postings in the
    // current date filter (currencyBadges), which is both misleading and would
    // silently rewrite the open's constraint on save. Fall back to observed
    // currencies only for an unconstrained open (no declared currencies).
    formData.currencies = account.declaredCurrencies.length > 0
      ? [...account.declaredCurrencies]
      : [...account.currencyBadges]
    formData.description = account.notes || ''
    formData.accountNumber = account.metadata['account_number'] || ''
    formData.ifscCode = account.metadata['ifsc_code'] || ''
    formData.swiftBic = account.metadata['swift_bic'] || ''
    formData.customFields = Object.entries(account.metadata)
      .filter(([k]) => !RESERVED_KEYS.has(k) && !typedKeys.has(k))
      .map(([key, value]) => ({ key, value }))
    formData.investment = Object.fromEntries(
      Object.entries(account.metadata).filter(([k]) => typedKeys.has(k)),
    )
  }
}, { immediate: true })

// Reset form when modal opens in create mode
watch(() => props.open, (isOpen) => {
  if (isOpen && props.mode === 'create') {
    formData.name = ''
    formData.openDate = new Date().toISOString().split('T')[0]
    formData.closeDate = ''
    formData.currencies = []
    formData.description = ''
    formData.accountNumber = ''
    formData.ifscCode = ''
    formData.swiftBic = ''
    formData.customFields = []
    formData.investment = {}
    currencyInput.value = ''
    fetchVocabulary()
    fetchCommodities()
    clearErrors()
  }
})

function clearErrors() {
  errors.name = ''
  errors.openDate = ''
  errors.closeDate = ''
  errors.currencies = ''
  errors.customFields = ''
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

// Beancount's metadata-key grammar.
function isValidKey(key: string): boolean {
  return /^[a-z][a-zA-Z0-9_-]*$/.test(key)
}

function validate(): boolean {
  clearErrors()
  let valid = true

  if (!formData.name.trim()) {
    errors.name = 'Account name is required'
    valid = false
  } else if (/\s/.test(formData.name)) {
    errors.name = 'Account name cannot contain spaces. Use colons to separate segments (e.g., Assets:BankOfAmerica)'
    valid = false
  } else if (!/^[A-Z][A-Za-z0-9\-_]*(?::[A-Z][A-Za-z0-9\-_]*)+$/.test(formData.name)) {
    errors.name = 'Invalid format. Use Type:Category:Name with each segment starting uppercase (e.g., Assets:Bank:Checking)'
    valid = false
  }

  if (!formData.openDate) {
    errors.openDate = 'Open date is required'
    valid = false
  }

  if (formData.closeDate && formData.openDate && formData.closeDate < formData.openDate) {
    errors.closeDate = 'Close date must be on or after the open date'
    valid = false
  }

  // No currencies = an unconstrained `open`: the account may hold any currency
  // or security (what a brokerage account usually wants).

  if (formData.customFields.some(f => f.key.trim() && !isValidKey(f.key.trim()))) {
    errors.customFields = 'Fix the highlighted keys. A key starts with a lowercase letter and uses letters, numbers, \'-\' and \'_\'.'
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
  // Kept even when a field no longer applies (e.g. type changed from CD) — the
  // form never drops a value the user didn't clear.
  for (const [key, value] of Object.entries(formData.investment)) {
    if (value && value.trim()) meta[key] = value.trim()
  }
  return meta
}

async function handleSubmit() {
  if (!validate()) return

  isSubmitting.value = true
  try {
    const submitData: FormSubmitData = {
      name: formData.name,
      openDate: formData.openDate,
      currencies: formData.currencies,
      description: formData.description,
      metadata: buildMetadata(),
    }
    // Include closeDate only in edit mode for closed accounts
    if (props.mode === 'edit' && props.account?.closeDate) {
      submitData.closeDate = formData.closeDate || null
    }
    emit('submit', submitData)
  } finally {
    isSubmitting.value = false
  }
}

function handleClose() {
  emit('update:open', false)
}
</script>
