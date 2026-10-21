<template>
  <div>
    <!-- Action selector (pill tabs) -->
    <div class="mb-6 flex flex-wrap gap-2">
      <button
        v-for="a in actions"
        :key="a.id"
        type="button"
        @click="selectAction(a.id)"
        :class="[
          action === a.id
            ? 'bg-indigo-600 text-white dark:bg-indigo-500'
            : 'bg-white text-gray-700 inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-gray-200 dark:inset-ring-white/10 dark:hover:bg-white/20',
          'rounded-full px-3 py-1.5 text-sm font-medium',
        ]"
      >
        {{ a.label }}
      </button>
    </div>

    <p class="mb-5 text-sm text-gray-500 dark:text-gray-400">{{ currentAction.help }}</p>

    <!-- Shared: date + currency -->
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Date</label>
        <input
          v-model="form.date"
          type="date"
          class="mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />
      </div>
      <div>
        <CommodityDropdown
          v-model="form.currency"
          label="Currency"
          :currencies-only="true"
          :allow-custom="true"
          placeholder="e.g. USD"
        />
      </div>
    </div>

    <!-- Sale form (own lot picker + live gain preview) -->
    <SaleForm
      v-if="action === 'sell'"
      ref="saleFormRef"
      :date="form.date"
      :currency="form.currency"
      :is-loading="isLoading"
      @submit="ev => assembleAndStage(ev, () => saleFormRef?.reset())"
    />

    <!-- Open Deposit writes directly, after a preview (accounts can't be staged) -->
    <OpenDepositForm
      v-if="action === 'open_deposit'"
      :date="form.date"
      :currency="form.currency"
    />

    <template v-if="action !== 'sell' && action !== 'open_deposit'">
    <!-- Holding account (buy / drip / add-holding) -->
    <div v-if="needs('holding_account')" class="mt-4">
      <AccountDropdown
        v-model="form.holding_account"
        label="Holding account"
        :account-types="['Assets']"
        :allow-custom="true"
      />
    </div>

    <!-- Cash account (buy / dividend) -->
    <div v-if="needs('cash_account')" class="mt-4">
      <AccountDropdown
        v-model="form.cash_account"
        :label="action === 'buy' ? 'Cash account (funds the buy)' : 'Cash account (receives the dividend)'"
        :account-types="['Assets', 'Liabilities']"
        :allow-custom="true"
      />
    </div>

    <!-- Security -->
    <div v-if="needs('security')" class="mt-4">
      <CommodityDropdown
        v-model="form.security"
        label="Security"
        :holdings-only="true"
        :allow-custom="true"
        placeholder="e.g. VOO"
      />
    </div>

    <!-- CD / term-deposit account (cd-maturity) -->
    <div v-if="needs('cd_account')" class="mt-4">
      <AccountDropdown
        v-model="form.cd_account"
        label="CD / term-deposit account"
        :account-types="['Assets']"
        :allow-custom="true"
      />
    </div>

    <!-- Deposit account (interest lands here) -->
    <div v-if="needs('deposit_account')" class="mt-4">
      <AccountDropdown
        v-model="form.deposit_account"
        label="Account receiving the interest"
        :account-types="['Assets']"
        :allow-custom="true"
        placeholder="the instrument (compounding) or a cash account (paid out)"
      />
      <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
        Pick the instrument itself to compound, or a separate cash account if it’s paid out.
      </p>
    </div>

    <!-- Destination account (cd-maturity) -->
    <div v-if="needs('destination_account')" class="mt-4">
      <AccountDropdown
        v-model="form.destination_account"
        label="Destination account (receives principal + interest)"
        :account-types="['Assets']"
        :allow-custom="true"
      />
    </div>

    <!-- Amount fields -->
    <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div v-if="needs('units')">
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Units</label>
        <input v-model="form.units" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 10" />
      </div>
      <div v-if="needs('unit_price')">
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Price per unit</label>
        <input v-model="form.unit_price" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 417.45" />
      </div>
      <div v-if="needs('avg_cost')">
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Average cost per unit</label>
        <input v-model="form.avg_cost" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 400.00" />
      </div>
      <div v-if="needs('amount')">
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Amount</label>
        <input v-model="form.amount" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 379.09" />
      </div>
      <div v-if="needs('principal')">
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Principal</label>
        <input v-model="form.principal" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 10000.00" />
      </div>
      <div v-if="needs('interest')">
        <label class="block text-sm font-medium text-gray-900 dark:text-white">
          Interest <span v-if="action === 'cd_maturity'" class="text-gray-400">(optional)</span>
        </label>
        <input v-model="form.interest" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 250.00" />
      </div>
    </div>

    <!-- Income account (drip / dividend / interest / cd-maturity) -->
    <div v-if="needs('income_account')" class="mt-4">
      <AccountDropdown
        v-model="form.income_account"
        :label="isInterestKind ? (action === 'cd_maturity' ? 'Interest income account (if interest was paid)' : 'Interest income account') : 'Income account'"
        :account-types="['Income']"
        :allow-custom="true"
      />
      <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
        Tag this account with <code>income-type: "{{ isInterestKind ? 'interest' : 'dividend' }}"</code>
        so it feeds the {{ isInterestKind ? 'interest' : 'dividend' }} split.
      </p>
    </div>

    <!-- Opening account (add-holding) -->
    <div v-if="needs('opening_account')" class="mt-4">
      <AccountDropdown
        v-model="form.opening_account"
        label="Opening-balances account"
        :account-types="['Equity']"
        :allow-custom="true"
      />
    </div>

    <!-- Fee (buy only, optional) -->
    <div v-if="action === 'buy'" class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Fee <span class="text-gray-400">(optional)</span></label>
        <input v-model="form.fee" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 4.95" />
      </div>
      <div v-if="hasFee">
        <AccountDropdown
          v-model="form.fee_account"
          label="Fee account"
          :account-types="['Expenses']"
          :allow-custom="true"
        />
      </div>
    </div>

    <!-- Narration (optional) -->
    <div class="mt-4">
      <label class="block text-sm font-medium text-gray-900 dark:text-white">Narration <span class="text-gray-400">(optional)</span></label>
      <input v-model="form.narration" type="text" :class="inputClass" :placeholder="defaultNarration" />
    </div>
    </template>

    <!-- Warnings / errors from the assembler -->
    <div v-if="issues.length" class="mt-4 rounded-lg bg-yellow-50 p-4 ring-1 ring-yellow-200 dark:bg-yellow-900/20 dark:ring-yellow-500/30">
      <p class="text-sm font-medium text-yellow-800 dark:text-yellow-300">Please review</p>
      <ul class="mt-1 list-inside list-disc text-sm text-yellow-700 dark:text-yellow-400">
        <li v-for="(iss, i) in issues" :key="i">{{ iss.message }}</li>
      </ul>
    </div>

    <div v-if="action !== 'sell' && action !== 'open_deposit'" class="mt-6 flex justify-end">
      <button
        type="button"
        @click="submit"
        :disabled="!canSubmit || isLoading"
        class="flex items-center gap-2 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400"
      >
        <svg v-if="isLoading" class="h-4 w-4 animate-spin text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>Add to review</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { ref, reactive, computed, watch, onMounted } from 'vue'
  import AccountDropdown from '@/components/common/AccountDropdown.vue'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import SaleForm from '@/components/investments/SaleForm.vue'
  import OpenDepositForm from '@/components/investments/OpenDepositForm.vue'
  import { CommoditiesService } from '@/services/generated-api'
  import type { AssembleRequest, AssembleIssue } from '@/services/generated-api'
  import type { TransactionViewModel } from '@/types/transactions'
  import { useInvestmentActions } from '@/composables/useInvestmentActions'
  import { useAccounts } from '@/composables/useAccounts'
  import { useToast } from '@/composables/useNotifications'

  type ActionId = 'buy' | 'drip' | 'cash_dividend' | 'sell' | 'cd_maturity' | 'interest' | 'add_holding' | 'open_deposit'
  type InvestmentEvent = AssembleRequest['events'][number]

  const emit = defineEmits<{ staged: [vms: TransactionViewModel[]] }>()

  const { assembleAndConvert, isLoading } = useInvestmentActions()
  // The Sale form owns its inputs; reset them after a successful stage, as the
  // inline forms reset their amounts.
  const saleFormRef = ref<InstanceType<typeof SaleForm> | null>(null)
  const { accountDetails, fetchAccounts } = useAccounts()
  const { success: showSuccessToast } = useToast()

  const inputClass =
    'mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500'

  const actions: { id: ActionId; label: string; help: string; fields: string[] }[] = [
    { id: 'buy', label: 'Buy', help: 'Acquire a holding at cost — units bought against a cash account.', fields: ['holding_account', 'security', 'units', 'unit_price', 'cash_account'] },
    { id: 'drip', label: 'Reinvest', help: 'A reinvested dividend/income buys more units — no cash changes hands.', fields: ['holding_account', 'security', 'units', 'unit_price', 'income_account'] },
    { id: 'cash_dividend', label: 'Dividend', help: 'A dividend paid to cash (not reinvested).', fields: ['cash_account', 'security', 'amount', 'income_account'] },
    { id: 'sell', label: 'Sell', help: 'Sell units of a holding — pick the lots (or a strategy) and see the realized gain before staging.', fields: [] },
    { id: 'cd_maturity', label: 'CD Matures', help: 'A CD / term deposit matures — principal (and any final interest) is paid to another account.', fields: ['cd_account', 'principal', 'interest', 'destination_account', 'income_account'] },
    { id: 'interest', label: 'Interest', help: 'Record periodic interest — credited to the instrument (compounding) or paid to another account.', fields: ['deposit_account', 'interest', 'income_account'] },
    { id: 'add_holding', label: 'Add Holding', help: 'Record a position you already hold at its average cost — seeds current value without lot-by-lot history.', fields: ['holding_account', 'security', 'units', 'avg_cost', 'opening_account'] },
    { id: 'open_deposit', label: 'Open Deposit', help: 'Open a savings, CD or bond account, and optionally its interest income account and first deposit. The Date and Currency above are its open date and currency. This is written to your ledger when you click Create; it does not go to the review table.', fields: [] },
  ]

  const action = ref<ActionId>('buy')
  const currentAction = computed(() => actions.find(a => a.id === action.value)!)

  const today = new Date().toISOString().split('T')[0]
  const form = reactive({
    date: today,
    currency: '',
    holding_account: '',
    security: '',
    units: '',
    unit_price: '',
    cash_account: '',
    fee: '',
    fee_account: '',
    income_account: '',
    amount: '',
    avg_cost: '',
    opening_account: '',
    cd_account: '',
    destination_account: '',
    deposit_account: '',
    principal: '',
    interest: '',
    narration: '',
  })

  const isInterestKind = computed(() => action.value === 'interest' || action.value === 'cd_maturity')

  const issues = ref<AssembleIssue[]>([])

  function needs(field: string): boolean {
    return currentAction.value.fields.includes(field)
  }

  const hasFee = computed(() => isPositiveNumber(form.fee))

  const defaultNarration = computed(() => {
    const s = form.security || 'SECURITY'
    switch (action.value) {
      case 'buy': return `Buy ${s}`
      case 'drip': return `${s} dividend reinvested`
      case 'cash_dividend': return `${s} dividend`
      case 'cd_maturity': return 'CD matured'
      case 'interest': return 'Interest'
      case 'add_holding': return `Opening holding ${s}`
    }
    return ''
  })

  function isPositiveNumber(v: string): boolean {
    if (v === '' || v == null) return false
    const n = Number(v)
    return Number.isFinite(n) && n > 0
  }

  function isNonNegativeNumber(v: string): boolean {
    if (v === '' || v == null) return false
    const n = Number(v)
    return Number.isFinite(n) && n >= 0
  }

  const NUMERIC_REQUIRED = new Set(['units', 'unit_price', 'amount', 'avg_cost', 'principal'])

  const canSubmit = computed(() => {
    if (!form.date || !form.currency) return false
    for (const f of currentAction.value.fields) {
      if (f === 'interest') {
        if (action.value === 'interest') {
          if (!isPositiveNumber(form.interest)) return false
        } else if (form.interest !== '' && !isNonNegativeNumber(form.interest)) {
          return false // cd_maturity: interest optional, but a bad value blocks
        }
      } else if (f === 'income_account') {
        // cd_maturity's income account is required only when interest was entered.
        if (action.value === 'cd_maturity') {
          if (isPositiveNumber(form.interest) && !form.income_account) return false
        } else if (!form.income_account) {
          return false
        }
      } else if (NUMERIC_REQUIRED.has(f)) {
        if (!isPositiveNumber((form as Record<string, string>)[f])) return false
      } else if (!(form as Record<string, string>)[f]) {
        return false
      }
    }
    // A fee amount requires a fee account.
    if (action.value === 'buy' && hasFee.value && !form.fee_account) return false
    return true
  })

  function selectAction(id: ActionId) {
    action.value = id
    issues.value = []
  }

  function buildEvent(): InvestmentEvent {
    const base = {
      date: form.date,
      currency: form.currency,
      narration: form.narration || null,
    }
    switch (action.value) {
      case 'buy':
        return {
          ...base, type: 'buy',
          holding_account: form.holding_account, security: form.security,
          units: form.units, unit_price: form.unit_price, cash_account: form.cash_account,
          ...(hasFee.value ? { fee: form.fee, fee_account: form.fee_account } : {}),
        } as InvestmentEvent
      case 'drip':
        return {
          ...base, type: 'drip',
          holding_account: form.holding_account, security: form.security,
          units: form.units, unit_price: form.unit_price, income_account: form.income_account,
        } as InvestmentEvent
      case 'cash_dividend':
        return {
          ...base, type: 'cash_dividend',
          cash_account: form.cash_account, security: form.security,
          amount: form.amount, income_account: form.income_account,
        } as InvestmentEvent
      case 'cd_maturity': {
        const hasInterest = isPositiveNumber(form.interest)
        return {
          ...base, type: 'cd_maturity',
          cd_account: form.cd_account, principal: form.principal,
          interest: hasInterest ? form.interest : '0',
          destination_account: form.destination_account,
          ...(hasInterest ? { interest_income_account: form.income_account } : {}),
        } as InvestmentEvent
      }
      case 'interest':
        return {
          ...base, type: 'interest',
          deposit_account: form.deposit_account, interest: form.interest,
          income_account: form.income_account,
        } as InvestmentEvent
      case 'add_holding':
        return {
          ...base, type: 'add_holding',
          holding_account: form.holding_account, security: form.security,
          units: form.units, avg_cost: form.avg_cost, opening_account: form.opening_account,
        } as InvestmentEvent
    }
    throw new Error('unreachable')
  }

  async function submit() {
    if (!canSubmit.value) return
    await assembleAndStage(buildEvent(), resetAmounts)
  }

  // Shared assemble → stage path used by the inline forms and the Sale form.
  async function assembleAndStage(event: InvestmentEvent, onSuccess?: () => void) {
    issues.value = []
    try {
      const { bundle, warnings, errors } = await assembleAndConvert([event])
      issues.value = [...errors, ...warnings]
      if (bundle.transactions.length > 0) {
        emit('staged', bundle.transactions)
        if (errors.length === 0) {
          showSuccessToast('Added to review', 'The draft was staged below. Review and Register when ready.')
          onSuccess?.()
        }
      }
    } catch (_error) {
      // Network/API error already surfaced by the composable
    }
  }

  // Keep accounts/currency between entries (common case: several actions in a row);
  // clear only the per-entry amounts.
  function resetAmounts() {
    form.units = ''
    form.unit_price = ''
    form.amount = ''
    form.avg_cost = ''
    form.principal = ''
    form.interest = ''
    form.fee = ''
    form.narration = ''
  }

  // Metadata-driven income-account suggestion (never keyed off account names). The user
  // can always override. For dividend/DRIP prefer an account whose dividend_of matches the
  // security, else the sole income-type: dividend account; for interest/CD maturity the sole
  // income-type: interest account.
  function incomeAccountsOfType(kind: string) {
    return accountDetails.value.filter(
      a => String(a.metadata?.['income-type'] ?? '').toLowerCase() === kind
    )
  }
  watch(() => [action.value, form.security, accountDetails.value] as const, ([act, security]) => {
    if (form.income_account) return
    if (act === 'drip' || act === 'cash_dividend') {
      if (!security) return
      const dividends = incomeAccountsOfType('dividend')
      const exact = dividends.filter(a => a.metadata?.['dividend_of'] === security)
      const pick = exact.length === 1 ? exact[0] : (dividends.length === 1 ? dividends[0] : null)
      if (pick) form.income_account = pick.name
    } else if (act === 'interest' || act === 'cd_maturity') {
      const interest = incomeAccountsOfType('interest')
      if (interest.length === 1) form.income_account = interest[0].name
    }
  })

  onMounted(async () => {
    fetchAccounts()
    // Prefill the currency with the ledger's first operating currency (no hardcoding).
    try {
      const res = await CommoditiesService.getOperatingCurrencies()
      const first = res.data?.currencies?.[0]
      if (first && !form.currency) form.currency = first
    } catch {
      // Non-fatal — the user can pick the currency.
    }
  })
</script>
