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
            <DialogPanel class="w-full max-w-3xl transform overflow-hidden rounded-lg bg-white dark:bg-gray-800 p-4 sm:p-6 text-left shadow-xl transition-all">
              <DialogTitle as="h3" class="text-lg font-medium leading-6 text-gray-900 dark:text-white mb-1">
                Balance Assertions
              </DialogTitle>
              <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
                {{ account?.fullPath }}
              </p>

              <!-- Add button -->
              <div class="mb-4 flex justify-end">
                <button
                  v-if="!showAddForm"
                  type="button"
                  @click="openAddForm"
                  class="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-500 dark:bg-indigo-500 dark:hover:bg-indigo-600"
                >
                  <PlusIcon class="h-4 w-4" />
                  Add Balance Assertion
                </button>
              </div>

              <!-- Add form -->
              <div v-if="showAddForm" class="mb-4 p-4 border border-indigo-200 dark:border-indigo-800 rounded-lg bg-indigo-50 dark:bg-indigo-900/20">
                <h4 class="text-sm font-medium text-gray-900 dark:text-white mb-3">New Balance Assertion</h4>
                <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Date</label>
                    <input
                      v-model="addForm.date"
                      type="date"
                      class="block w-full rounded-md bg-white px-2 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    />
                  </div>
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Amount</label>
                    <input
                      v-model.number="addForm.amount"
                      type="number"
                      step="0.01"
                      placeholder="0.00"
                      class="block w-full rounded-md bg-white px-2 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    />
                  </div>
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Currency</label>
                    <CommodityDropdown
                      v-model="addForm.currency"
                      placeholder="e.g. USD"
                      :allow-custom="!hasDeclaredCurrencies"
                      :include-pattern="declaredCurrencyPattern"
                    />
                  </div>
                </div>
                <p
                  v-if="addCurrencyWarning"
                  class="mt-2 text-xs text-amber-700 dark:text-amber-400"
                >
                  <ExclamationTriangleIcon class="inline h-3.5 w-3.5 mr-1 -mt-0.5" />
                  {{ addCurrencyWarning }}
                </p>
                <div class="mt-3">
                  <label class="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <input v-model="addForm.includePad" type="checkbox" class="rounded border-gray-300 text-indigo-600 focus:outline-indigo-600 dark:border-white/10 dark:bg-white/5" />
                    Include pad directive
                  </label>
                </div>
                <div v-if="addForm.includePad" class="mt-2">
                  <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Pad Source Account</label>
                  <AccountDropdown
                    v-model="addForm.padSourceAccount"
                    placeholder="Select pad source account..."
                  />
                </div>
                <div class="mt-3 flex justify-end gap-2">
                  <button
                    type="button"
                    @click="cancelAdd"
                    class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    @click="submitAdd"
                    :disabled="!isAddFormValid || isSaving"
                    class="px-3 py-1.5 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:hover:bg-indigo-600"
                  >
                    {{ isSaving ? 'Adding...' : 'Add' }}
                  </button>
                </div>
              </div>

              <!-- Directives table -->
              <div v-if="isLoadingDirectives" class="flex justify-center py-8">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
              </div>

              <div v-else-if="directives.length === 0 && !showAddForm" class="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                No balance assertions for this account
              </div>

              <div v-else-if="directives.length > 0" class="overflow-x-auto max-h-96 overflow-y-auto">
                <table class="min-w-full table-fixed divide-y divide-gray-200 dark:divide-gray-700">
                  <thead class="bg-gray-50 dark:bg-gray-800/75 sticky top-0">
                    <tr>
                      <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[100px]">Date</th>
                      <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[52px]">Ccy</th>
                      <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[110px]">Balance</th>
                      <th class="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[44px]">Pad</th>
                      <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase dark:text-gray-400">Pad Source</th>
                      <th class="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[68px]">Status</th>
                      <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase dark:text-gray-400 w-[68px]">Actions</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                    <template
                      v-for="(directive, index) in directives"
                      :key="`${directive.date}-${directive.currency}-${index}`"
                    >
                    <tr
                      class="hover:bg-gray-50 dark:hover:bg-gray-700/50"
                      :class="{ 'bg-yellow-50 dark:bg-yellow-900/10': editingIndex === index }"
                    >
                      <td class="px-3 py-2 text-sm text-gray-900 dark:text-white whitespace-nowrap">{{ directive.date }}</td>
                      <td class="px-3 py-2 text-sm text-gray-900 dark:text-white">{{ directive.currency }}</td>
                      <td class="px-3 py-2 text-sm text-right text-gray-900 dark:text-white">{{ formatBalance(directive.expected_balance, directive.currency) }}</td>
                      <td class="px-3 py-2 text-center">
                        <span
                          v-if="directive.has_pad"
                          class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400"
                        >
                          Pad
                        </span>
                        <span v-else class="text-gray-300 dark:text-gray-600">—</span>
                      </td>
                      <td class="px-3 py-2 text-sm text-gray-500 dark:text-gray-400 truncate" :title="directive.pad_source_account ?? ''">
                        {{ directive.pad_source_account || '—' }}
                      </td>
                      <td class="px-3 py-2 text-center">
                        <button
                          v-if="directive.has_error"
                          type="button"
                          @click="expandedErrorIndex = expandedErrorIndex === index ? null : index"
                          class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50"
                        >
                          <ExclamationTriangleIcon class="h-3 w-3" />
                          Fail
                        </button>
                        <span
                          v-else
                          class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                        >
                          <CheckCircleIcon class="h-3 w-3" />
                          Pass
                        </span>
                      </td>
                      <td class="px-3 py-2 text-right whitespace-nowrap">
                        <div class="flex items-center justify-end gap-2">
                          <button
                            @click="startEdit(index)"
                            class="text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400"
                            title="Edit"
                          >
                            <PencilIcon class="h-4 w-4" />
                          </button>
                          <button
                            @click="confirmDelete(index)"
                            class="text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
                            title="Delete"
                          >
                            <TrashIcon class="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                    <!-- Error detail row -->
                    <tr v-if="directive.has_error && expandedErrorIndex === index">
                      <td colspan="7" class="px-3 py-2 bg-red-50 dark:bg-red-900/10">
                        <p class="text-xs text-red-700 dark:text-red-400 break-words">
                          {{ directive.error_message }}
                        </p>
                      </td>
                    </tr>
                    </template>
                  </tbody>
                </table>
              </div>

              <!-- Edit form (below table, full width) -->
              <div v-if="editingIndex !== null" class="mt-4 p-4 border border-yellow-200 dark:border-yellow-800 rounded-lg bg-yellow-50 dark:bg-yellow-900/20">
                <h4 class="text-sm font-medium text-gray-900 dark:text-white mb-3">
                  Edit Balance Assertion ({{ directives[editingIndex].date }})
                </h4>
                <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Date</label>
                    <input
                      v-model="editForm.newDate"
                      type="date"
                      class="block w-full rounded-md bg-white px-2 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    />
                  </div>
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Amount</label>
                    <input
                      v-model.number="editForm.newAmount"
                      type="number"
                      step="0.01"
                      class="block w-full rounded-md bg-white px-2 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                    />
                  </div>
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Currency</label>
                    <CommodityDropdown
                      v-model="editForm.newCurrency"
                      :allow-custom="!hasDeclaredCurrencies"
                      :include-pattern="declaredCurrencyPattern"
                    />
                  </div>
                </div>
                <p
                  v-if="editCurrencyWarning"
                  class="mt-2 text-xs text-amber-700 dark:text-amber-400"
                >
                  <ExclamationTriangleIcon class="inline h-3.5 w-3.5 mr-1 -mt-0.5" />
                  {{ editCurrencyWarning }}
                </p>
                <div class="mt-3">
                  <label class="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <input v-model="editForm.includePad" type="checkbox" class="rounded border-gray-300 text-indigo-600 focus:outline-indigo-600 dark:border-white/10 dark:bg-white/5" />
                    Include pad directive
                  </label>
                </div>
                <div v-if="editForm.includePad" class="mt-2">
                  <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Pad Source Account</label>
                  <AccountDropdown
                    v-model="editForm.padSourceAccount"
                    placeholder="Select pad source account..."
                  />
                </div>
                <div class="mt-3 flex justify-end gap-2">
                  <button
                    type="button"
                    @click="cancelEdit"
                    class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    @click="submitEdit"
                    :disabled="!isEditFormValid || isSaving"
                    class="px-3 py-1.5 text-sm font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:hover:bg-indigo-600"
                  >
                    {{ isSaving ? 'Saving...' : 'Save' }}
                  </button>
                </div>
              </div>

              <!-- Delete confirmation -->
              <div v-if="deleteConfirmIndex !== null" class="mt-4 p-3 border border-red-200 dark:border-red-800 rounded-lg bg-red-50 dark:bg-red-900/20">
                <p class="text-sm text-red-800 dark:text-red-300">
                  Delete balance assertion for <strong>{{ directives[deleteConfirmIndex].date }}</strong>
                  ({{ directives[deleteConfirmIndex].expected_balance }} {{ directives[deleteConfirmIndex].currency }})?
                </p>
                <div v-if="directives[deleteConfirmIndex].has_pad" class="mt-2">
                  <label class="inline-flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
                    <input v-model="deleteAlsoPad" type="checkbox" class="rounded border-red-300 text-red-600 focus:ring-red-500 dark:border-red-700 dark:bg-white/5" />
                    Also delete associated pad directive
                  </label>
                </div>
                <div class="mt-2 flex justify-end gap-2">
                  <button
                    type="button"
                    @click="deleteConfirmIndex = null"
                    class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    @click="performDelete"
                    :disabled="isSaving"
                    class="px-3 py-1.5 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 disabled:opacity-50 dark:bg-red-500 dark:hover:bg-red-600"
                  >
                    {{ isSaving ? 'Deleting...' : 'Delete' }}
                  </button>
                </div>
              </div>

              <!-- Close button (hidden when a form is active) -->
              <div v-if="editingIndex === null && deleteConfirmIndex === null && !showAddForm" class="mt-6 flex justify-end">
                <button
                  type="button"
                  @click="handleClose"
                  class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
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
import {
  PlusIcon,
  PencilIcon,
  TrashIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
} from '@heroicons/vue/24/outline'
import type { AccountTreeNode } from '@/types/accounts'
import type { BalanceDirectiveData } from '@/services/generated-api'
import { useAccounts } from '@/composables/useAccounts'
import { useToast } from '@/composables/useNotifications'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import { getLocale } from '@/utils/currencyFormat'
import { toMoney, toNumber, type Money } from '@/utils/money'

interface Props {
  open: boolean
  account: AccountTreeNode | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const { fetchBalanceDirectives, addBalanceDirective, updateBalanceDirective, deleteBalanceDirective } = useAccounts()
const toast = useToast()

// State
const directives = ref<BalanceDirectiveData[]>([])
const isLoadingDirectives = ref(false)
const isSaving = ref(false)

// Add form
const showAddForm = ref(false)
const addForm = ref({
  date: '',
  amount: 0,
  currency: '',
  includePad: false,
  padSourceAccount: '',
})

// Edit form
const editingIndex = ref<number | null>(null)
const editForm = ref({
  newDate: '',
  newCurrency: '',
  newAmount: 0,
  includePad: false,
  padSourceAccount: '',
})

// Error detail expansion
const expandedErrorIndex = ref<number | null>(null)

// Delete confirm
const deleteConfirmIndex = ref<number | null>(null)
const deleteAlsoPad = ref(true)

// Currency hints derived from the account
const declaredCurrencies = computed<string[]>(() => props.account?.declaredCurrencies ?? [])
const postingCurrencies = computed<string[]>(() => props.account?.currencyBadges ?? [])
const hasDeclaredCurrencies = computed(() => declaredCurrencies.value.length > 0)

// Restrict the dropdown when the open directive constrains the account.
// `includePattern` accepts a regex; we anchor it to whole-token matches.
const declaredCurrencyPattern = computed<RegExp | undefined>(() => {
  if (!hasDeclaredCurrencies.value) return undefined
  const escaped = declaredCurrencies.value.map(c => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  return new RegExp(`^(${escaped.join('|')})$`)
})

const defaultCurrency = computed<string>(() => {
  return declaredCurrencies.value[0] ?? postingCurrencies.value[0] ?? ''
})

function currencyWarning(picked: string): string {
  if (!picked) return ''
  // Constrained accounts use dropdown restriction, so no need to warn here.
  if (hasDeclaredCurrencies.value) return ''
  if (postingCurrencies.value.length === 0) return ''
  if (postingCurrencies.value.includes(picked)) return ''
  return `Account has no postings in ${picked}. Existing currencies: ${postingCurrencies.value.join(', ')}. The balance assertion will likely fail.`
}

const addCurrencyWarning = computed(() => currencyWarning(addForm.value.currency))
const editCurrencyWarning = computed(() =>
  editingIndex.value === null ? '' : currencyWarning(editForm.value.newCurrency)
)

// Validations
const isAddFormValid = computed(() => {
  if (!addForm.value.date || !addForm.value.currency || addForm.value.amount === null) return false
  if (addForm.value.includePad && !addForm.value.padSourceAccount) return false
  return true
})

const isEditFormValid = computed(() => {
  if (!editForm.value.newDate || !editForm.value.newCurrency || editForm.value.newAmount === null) return false
  if (editForm.value.includePad && !editForm.value.padSourceAccount) return false
  return true
})

// Load directives when modal opens
watch(() => props.open, async (isOpen) => {
  if (isOpen && props.account) {
    await loadDirectives()
    // Reset form state
    showAddForm.value = false
    editingIndex.value = null
    deleteConfirmIndex.value = null
    expandedErrorIndex.value = null
    resetAddForm()
  }
})

async function loadDirectives() {
  if (!props.account) return
  isLoadingDirectives.value = true
  try {
    directives.value = await fetchBalanceDirectives(props.account.fullPath)
  } catch {
    directives.value = []
  } finally {
    isLoadingDirectives.value = false
  }
}

function resetAddForm() {
  addForm.value = {
    date: '',
    amount: 0,
    currency: defaultCurrency.value,
    includePad: false,
    padSourceAccount: '',
  }
}

// Re-seed the currency once the account (and therefore its declared currencies)
// is available, in case resetAddForm ran before defaults were known.
watch(defaultCurrency, (next) => {
  if (showAddForm.value && !addForm.value.currency) addForm.value.currency = next
})

function openAddForm() {
  resetAddForm()
  showAddForm.value = true
}

function cancelAdd() {
  showAddForm.value = false
  resetAddForm()
}

async function submitAdd() {
  if (!props.account || !isAddFormValid.value) return
  isSaving.value = true
  try {
    await addBalanceDirective(props.account.fullPath, {
      date: addForm.value.date,
      currency: addForm.value.currency,
      amount: addForm.value.amount,
      include_pad: addForm.value.includePad,
      pad_source_account: addForm.value.includePad ? addForm.value.padSourceAccount : undefined,
    })
    toast.success('Balance Assertion Added', `Added balance assertion for ${addForm.value.date}`)
    cancelAdd()
    await loadDirectives()
  } catch {
    // Error already displayed by composable
  } finally {
    isSaving.value = false
  }
}

function startEdit(index: number) {
  const d = directives.value[index]
  editingIndex.value = index
  deleteConfirmIndex.value = null
  editForm.value = {
    newDate: d.date,
    newCurrency: d.currency,
    newAmount: toNumber(toMoney(d.expected_balance)),
    includePad: d.has_pad,
    padSourceAccount: d.pad_source_account ?? '',
  }
}

function cancelEdit() {
  editingIndex.value = null
}

async function submitEdit() {
  if (!props.account || editingIndex.value === null || !isEditFormValid.value) return
  const original = directives.value[editingIndex.value]
  isSaving.value = true
  try {
    await updateBalanceDirective(props.account.fullPath, {
      original_date: original.date,
      original_currency: original.currency,
      original_amount: original.expected_balance,
      new_date: editForm.value.newDate,
      new_currency: editForm.value.newCurrency,
      new_amount: editForm.value.newAmount,
      include_pad: editForm.value.includePad,
      pad_source_account: editForm.value.includePad ? editForm.value.padSourceAccount : undefined,
    })
    toast.success('Balance Assertion Updated', `Updated balance assertion for ${editForm.value.newDate}`)
    editingIndex.value = null
    await loadDirectives()
  } catch {
    // Error already displayed by composable
  } finally {
    isSaving.value = false
  }
}

function confirmDelete(index: number) {
  deleteConfirmIndex.value = index
  editingIndex.value = null
  deleteAlsoPad.value = directives.value[index].has_pad
}

async function performDelete() {
  if (!props.account || deleteConfirmIndex.value === null) return
  const d = directives.value[deleteConfirmIndex.value]
  isSaving.value = true
  try {
    await deleteBalanceDirective(
      props.account.fullPath,
      d.date,
      d.currency,
      toMoney(d.expected_balance),
      deleteAlsoPad.value,
    )
    toast.success('Balance Assertion Deleted', `Deleted balance assertion for ${d.date}`)
    deleteConfirmIndex.value = null
    await loadDirectives()
  } catch {
    // Error already displayed by composable
  } finally {
    isSaving.value = false
  }
}

function formatBalance(value: Money | string, currency: string): string {
  return toNumber(toMoney(value)).toLocaleString(getLocale(currency), {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function handleClose() {
  emit('update:open', false)
}
</script>
