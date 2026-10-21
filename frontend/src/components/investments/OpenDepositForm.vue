<template>
  <div>
    <!-- Account + type -->
    <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <label for="open-deposit-account" class="block text-sm font-medium text-gray-900 dark:text-white">New account</label>
        <input
          id="open-deposit-account"
          v-model="form.account"
          type="text"
          :class="inputClass"
          placeholder="Full name of the new account"
        />
      </div>
      <div>
        <span class="block text-sm font-medium text-gray-900 dark:text-white">Account type</span>
        <div class="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label="Account type">
          <button
            v-for="t in types"
            :key="t.id"
            type="button"
            role="radio"
            :aria-checked="form.type === t.id"
            :class="[
              form.type === t.id
                ? 'bg-indigo-600 text-white dark:bg-indigo-500'
                : 'bg-white text-gray-700 inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-gray-200 dark:inset-ring-white/10 dark:hover:bg-white/20',
              'rounded-full px-3 py-1.5 text-sm font-medium',
            ]"
            @click="form.type = t.id"
          >
            {{ t.label }}
          </button>
        </div>
      </div>
    </div>

    <!-- Rate + maturity -->
    <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <label for="open-deposit-rate" class="block text-sm font-medium text-gray-900 dark:text-white">
          Interest rate <span class="text-gray-400">(optional)</span>
        </label>
        <input id="open-deposit-rate" v-model="form.interestRate" type="text" :class="inputClass" placeholder="e.g. 4.50%" />
      </div>
      <div v-if="form.type !== 'savings'">
        <label for="open-deposit-maturity" class="block text-sm font-medium text-gray-900 dark:text-white">
          Maturity date <span class="text-gray-400">(optional)</span>
        </label>
        <input id="open-deposit-maturity" v-model="form.maturityDate" type="date" :class="inputClass" />
      </div>
    </div>

    <!-- Interest income account -->
    <div class="mt-6">
      <label class="flex items-center gap-3">
        <input
          v-model="form.createIncome"
          type="checkbox"
          class="size-4 rounded-sm border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/10 dark:bg-white/5"
        />
        <span class="text-sm font-medium text-gray-900 dark:text-white">Create an interest income account for this deposit</span>
      </label>
      <p class="mt-1 ml-7 text-sm text-gray-500 dark:text-gray-400">
        Interest earned by this account is then matched to it, even when it is paid into another account.
      </p>
      <div class="mt-3 ml-7">
        <template v-if="form.createIncome">
          <label for="open-deposit-income" class="block text-sm font-medium text-gray-900 dark:text-white">Income account</label>
          <input
            id="open-deposit-income"
            v-model="form.newIncomeAccount"
            type="text"
            :class="inputClass"
            placeholder="Full name of the new income account"
          />
        </template>
        <template v-else>
          <AccountDropdown
            v-model="form.existingIncomeAccount"
            label="Existing interest income account (optional)"
            :account-types="['Income']"
            placeholder="Leave empty if not needed"
          />
          <button
            v-if="form.existingIncomeAccount"
            type="button"
            class="mt-2 text-sm font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
            @click="form.existingIncomeAccount = ''"
          >
            Don't use one
          </button>
        </template>
      </div>
    </div>

    <!-- First deposit -->
    <div class="mt-6">
      <label class="flex items-center gap-3">
        <input
          v-model="form.recordDeposit"
          type="checkbox"
          class="size-4 rounded-sm border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/10 dark:bg-white/5"
        />
        <span class="text-sm font-medium text-gray-900 dark:text-white">Record the first deposit</span>
      </label>
      <div v-if="form.recordDeposit" class="mt-3 ml-7 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <AccountDropdown
            v-model="form.fromAccount"
            label="From account"
            :account-types="['Assets', 'Liabilities']"
          />
        </div>
        <div>
          <label for="open-deposit-amount" class="block text-sm font-medium text-gray-900 dark:text-white">Amount</label>
          <input id="open-deposit-amount" v-model="form.amount" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 10000.00" />
        </div>
        <div>
          <label for="open-deposit-date" class="block text-sm font-medium text-gray-900 dark:text-white">Date</label>
          <input id="open-deposit-date" v-model="form.depositDate" type="date" :class="inputClass" />
        </div>
      </div>
    </div>

    <!-- Preview -->
    <div class="mt-6">
      <h3 class="text-sm font-medium text-gray-900 dark:text-white">Will be written to your ledger</h3>
      <p v-if="stale && preview && !previewError" role="alert" class="mt-2 text-sm text-yellow-700 dark:text-yellow-400">
        Your accounts changed since the preview. Check the new preview below, then create again.
      </p>
      <p v-if="missing.length > 0" class="mt-2 text-sm text-gray-500 dark:text-gray-400">
        Enter {{ missing.join(', ') }} to see what will be written.
      </p>
      <p v-else-if="previewError" role="alert" class="mt-2 text-sm text-red-600 dark:text-red-400">{{ previewError }}</p>
      <pre
        v-else-if="preview"
        class="mt-2 overflow-x-auto rounded-md bg-gray-50 p-4 font-mono text-xs/5 text-gray-800 outline-1 -outline-offset-1 outline-gray-200 dark:bg-white/5 dark:text-gray-200 dark:outline-white/10"
      >{{ preview }}</pre>
      <ul v-if="warnings.length > 0 && !previewError && missing.length === 0" class="mt-2 list-disc space-y-1 pl-5 text-sm text-yellow-700 dark:text-yellow-400">
        <li v-for="w in warnings" :key="w">{{ w }}</li>
      </ul>
    </div>

    <div class="mt-6 flex justify-end">
      <button
        type="button"
        :disabled="!canCreate"
        class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
        @click="create"
      >
        {{ creating ? 'Creating…' : 'Create' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Open Deposit: a savings, CD or bond account, optionally with its own interest
 * income account and its first deposit. Unlike the other Quick Actions it writes
 * directly (accounts can't be staged in the review table), after showing a live
 * preview of exactly what will be written. See dev-docs/investment-view.md §10.4.
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import { ApiError, InvestmentsService } from '@/services/generated-api'
import { useAccounts } from '@/composables/useAccounts'
import { useToast } from '@/composables/useNotifications'
import { errorHandler } from '@/utils/ErrorHandler'
import {
  buildOpenDepositRequest,
  missingFields,
  type DepositType,
  type OpenDepositForm,
} from '@/utils/openDeposit'

const props = defineProps<{ date: string; currency: string }>()
const emit = defineEmits<{ created: [accounts: string[]] }>()

const { fetchAccounts } = useAccounts()
const { success: showSuccessToast } = useToast()

const inputClass =
  'mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500'

const types: { id: DepositType; label: string }[] = [
  { id: 'savings', label: 'Savings' },
  { id: 'cd', label: 'CD' },
  { id: 'bond', label: 'Bond' },
]

function emptyForm(): OpenDepositForm {
  return {
    account: '', type: 'cd', currency: props.currency, openDate: props.date,
    interestRate: '', maturityDate: '',
    createIncome: false, newIncomeAccount: '', existingIncomeAccount: '',
    recordDeposit: false, fromAccount: '', amount: '', depositDate: props.date,
  }
}

const form = reactive<OpenDepositForm>(emptyForm())

// The shared Date and Currency fields above the form are the open date and currency.
watch(() => props.date, (d, old) => {
  if (form.depositDate === old || !form.depositDate) form.depositDate = d
  form.openDate = d
})
watch(() => props.currency, c => { form.currency = c })

// ── Live preview (dry run), debounced ──
const missing = computed(() => missingFields(form))
const preview = ref('')
const previewError = ref('')
const warnings = ref<string[]>([])
const previewFor = ref('')   // the request the current preview belongs to
const previewHash = ref('')  // its fingerprint, sent with Create
let timer: ReturnType<typeof setTimeout> | undefined
let seq = 0

const requestKey = computed(() => JSON.stringify(buildOpenDepositRequest(form, true)))

function clearPreview() {
  preview.value = ''
  previewError.value = ''
  warnings.value = []
  previewFor.value = ''
  previewHash.value = ''
}

watch(requestKey, key => {
  clearTimeout(timer)
  seq++   // a reply for the previous values is now stale
  clearPreview()
  if (missing.value.length > 0) return
  timer = setTimeout(() => runPreview(key), 400)
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))

async function runPreview(key: string) {
  const id = ++seq
  try {
    const resp = await InvestmentsService.openDeposit(JSON.parse(key))
    if (id !== seq) return
    // The ledger printer separates entries by two blank lines; one reads better here.
    preview.value = (resp.data?.preview ?? '').replace(/\n{3,}/g, '\n\n').trim()
    warnings.value = resp.data?.warnings ?? []
    previewError.value = ''
    previewHash.value = resp.data?.preview_hash ?? ''
    previewFor.value = key
  } catch (err) {
    if (id !== seq) return
    // Expected while the user is still typing (e.g. a name that already exists):
    // shown inline, not as a notification.
    previewError.value = errorHandler.messageOf(err)
  }
}

// ── Create ──
const creating = ref(false)
// Only what was previewed can be created.
const canCreate = computed(() =>
  !creating.value && !previewError.value && previewHash.value !== ''
  && previewFor.value !== '' && previewFor.value === requestKey.value,
)

function isStale(err: unknown): boolean {
  return err instanceof ApiError && err.body?.error?.code === 'PREVIEW_STALE'
}

async function create() {
  if (!canCreate.value) return
  creating.value = true
  try {
    const resp = await InvestmentsService.openDeposit({
      ...buildOpenDepositRequest(form, false),
      expected_preview_hash: previewHash.value,
    })
    const created = resp.data?.accounts_created ?? []
    const note = (resp.data?.warnings ?? []).join(' ')
    showSuccessToast('Created', `Opened ${created.join(' and ')}.${note ? ` ${note}` : ''}`)
    await fetchAccounts(true)   // the new accounts appear in the other forms at once
    emit('created', created)
    Object.assign(form, emptyForm())
  } catch (err) {
    if (isStale(err)) {
      // Something changed since the preview: show the new one before anything is written.
      const key = requestKey.value
      seq++
      clearPreview()
      await runPreview(key)
      stale.value = !previewError.value
    } else {
      errorHandler.display(err)
    }
  } finally {
    creating.value = false
  }
}

// Shown above a refreshed preview after a stale Create; cleared by any edit.
const stale = ref(false)
watch(requestKey, () => { stale.value = false })
</script>
