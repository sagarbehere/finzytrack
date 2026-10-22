<template>
  <div class="space-y-8">
    <!-- ── 1. Choose the statement ─────────────────────────────────────────── -->
    <section v-if="!parsed && !result">
      <h3 class="text-base font-semibold text-gray-900 dark:text-white">Indian mutual funds (CAS)</h3>
      <p class="mt-1 max-w-3xl text-sm text-gray-500 dark:text-gray-400">
        Import every mutual fund you hold with CAMS and KFintech, with its full history, from one
        Consolidated Account Statement (CAS). The PDF is read on this computer; it is not sent anywhere.
        Use the <strong>detailed</strong> statement, for a period that starts before your first investment.
        <a :href="GUIDE_URL" target="_blank" rel="noopener noreferrer" :class="link">How to get it</a>
      </p>

      <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label for="stmt-file" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Statement PDF</label>
          <input
            id="stmt-file"
            ref="fileInput"
            type="file"
            accept=".pdf,application/pdf"
            class="mt-2 block w-full text-sm text-gray-900 file:mr-3 file:rounded-md file:border-0 file:bg-indigo-50 file:px-2.5 file:py-1.5 file:text-sm file:font-semibold file:text-indigo-600 hover:file:bg-indigo-100 dark:text-white dark:file:bg-indigo-500/20 dark:file:text-indigo-400"
            @change="onFile"
          />
        </div>
        <div>
          <label for="stmt-password" class="block text-sm/6 font-medium text-gray-900 dark:text-white">PDF password</label>
          <input
            id="stmt-password"
            v-model="password"
            type="password"
            autocomplete="off"
            :class="inputClass"
            @keydown.enter="readStatement"
          />
          <p class="mt-2 text-sm text-gray-500 dark:text-gray-400">The one you chose when you asked for the statement. It is not saved.</p>
        </div>
      </div>
      <FormFeedback v-if="readError" :message="readError" severity="error" class="mt-4" />
      <div class="mt-4 flex justify-end">
        <button type="button" :class="primaryLg" :disabled="!file || reading" @click="readStatement">
          {{ reading ? 'Reading…' : 'Read statement' }}
        </button>
      </div>
    </section>

    <!-- ── Result of Register ──────────────────────────────────────────────── -->
    <section v-if="result" class="rounded-md bg-green-50 p-4 dark:bg-green-500/10 dark:outline dark:outline-green-500/15">
      <div class="flex gap-3">
        <CheckCircleIcon class="size-5 shrink-0 text-green-400 dark:text-green-300" aria-hidden="true" />
        <div class="flex-1">
          <h3 class="text-sm font-medium text-green-800 dark:text-green-100">
            Imported {{ result.transactions }} transactions and {{ result.balances }} balance checks
          </h3>
          <div class="mt-2 text-sm text-green-700 dark:text-green-100/80">
            <p v-if="resultSentence(result, resultFacts)">{{ resultSentence(result, resultFacts) }}</p>
            <p v-if="(result.failed_balance_checks ?? []).length" class="mt-2">
              These balance checks don't hold — the ledger and the statement differ:
            </p>
            <ul v-if="(result.failed_balance_checks ?? []).length" class="mt-1 list-inside list-disc">
              <li v-for="(m, i) in result.failed_balance_checks ?? []" :key="i">{{ m }}</li>
            </ul>
            <p class="mt-2">
              To see values over time, fetch each fund's full price history (NAVs back to its first purchase).
            </p>
            <p v-if="priceResult" class="mt-2">
              Prices: {{ priceResult.added }} added<span v-if="priceResult.updated">, {{ priceResult.updated }} updated</span>
              in {{ priceSeconds }} s<span v-if="priceResult.as_of">, latest {{ priceResult.as_of }}</span>.
              <span v-if="priceResult.failed?.length"> Not found: {{ priceResult.failed.join('; ') }}.</span>
            </p>
          </div>
          <div class="mt-4 flex flex-wrap gap-2">
            <button type="button" :class="primary" :disabled="fetchingPrices" @click="updatePrices">
              {{ fetchingPrices ? 'Fetching prices…' : 'Update prices' }}
            </button>
            <router-link to="/dashboard?tab=investment-holdings" :class="secondary">Open Holdings &amp; Performance</router-link>
            <button type="button" :class="secondary" @click="startOver">Import another statement</button>
          </div>
        </div>
      </div>
    </section>

    <!-- ── 2. Mapping + preview ────────────────────────────────────────────── -->
    <template v-if="parsed">
      <div class="flex flex-wrap items-start justify-between gap-4 border-b border-gray-200 pb-5 dark:border-white/10">
        <div>
          <h3 class="text-base font-semibold text-gray-900 dark:text-white">
            {{ plan ? spanSentence(plan.summary) : `Statement ${parsed.period_start} to ${parsed.period_end}` }}
          </h3>
          <p v-if="parsed.investors.length" class="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Investor{{ parsed.investors.length > 1 ? 's' : '' }}: {{ parsed.investors.join(', ') }}
          </p>
        </div>
        <button type="button" :class="secondary" @click="startOver">Choose another file</button>
      </div>

      <!-- Accounts -->
      <section class="space-y-6">
        <div>
          <h4 class="text-sm font-semibold text-gray-900 dark:text-white">Where the funds are held</h4>
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
            One account per distributor in the statement. You can pick the same account for all.
          </p>
          <div class="mt-3 space-y-4">
            <div v-for="d in parsed.distributors" :key="d.key" class="grid grid-cols-1 gap-2 sm:grid-cols-2 sm:items-end">
              <div>
                <p class="text-sm font-medium text-gray-900 dark:text-white">{{ d.label }}</p>
                <p class="text-xs text-gray-500 dark:text-gray-400" :title="d.funds.join(', ')">
                  {{ d.funds.length }} fund{{ d.funds.length === 1 ? '' : 's' }}: {{ d.funds.slice(0, 3).join(', ') }}<span v-if="d.funds.length > 3">, …</span>
                </p>
              </div>
              <AccountDropdown v-model="mapping.holding_accounts[d.key]" :account-types="['Assets']" />
            </div>
          </div>
        </div>

        <div v-if="parsed.needs_cash_account">
          <h4 class="text-sm font-semibold text-gray-900 dark:text-white">Money paid in and out</h4>
          <p class="mt-1 max-w-3xl text-sm text-gray-500 dark:text-gray-400">
            The statement shows the fund side only, not which bank paid or received the money. The simplest choice is
            one Equity account for money moving between the outside world and your investments: your history and
            returns are exact and your bank accounts are not changed. Later, categorise bank transfers to your funds to
            the same account.
          </p>
          <div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <AccountDropdown
                v-model="mapping.cash_fallback_account"
                label="Account for purchases, redemptions and dividends"
                :account-types="['Equity', 'Assets', 'Liabilities']"
              />
            </div>
            <div>
              <AccountDropdown
                v-model="mapping.cash_matching_account"
                label="Match with bank transactions in (optional)"
                :account-types="['Assets', 'Liabilities']"
              />
              <p class="mt-2 text-sm text-gray-500 dark:text-gray-400">
                A transfer account your bank imports use for payments to and from these funds. A statement amount
                (with its stamp duty) that matches one of its transactions within
                <input v-model.number="mapping.match_window_days" type="number" min="0" max="31" aria-label="Days" class="w-14 rounded-md bg-white px-2 py-0.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10" />
                days is booked against it; the rest goes to the account on the left.
              </p>
            </div>
            <div>
              <label for="stmt-cutoff" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Different account before (optional)</label>
              <input id="stmt-cutoff" v-model="cutoff" type="date" :class="inputClass" />
            </div>
            <div v-if="mapping.cutoff_date">
              <AccountDropdown
                v-model="mapping.cash_before_cutoff_account"
                :label="`Account before ${mapping.cutoff_date}`"
                :account-types="['Equity', 'Assets', 'Liabilities']"
              />
            </div>
            <div class="sm:col-span-2">
              <label for="stmt-history" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Import history from (optional)</label>
              <input id="stmt-history" v-model="historyStart" type="date" :class="[inputClass, 'sm:max-w-xs']" />
              <p class="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Earlier transactions are not imported. What you held on that date is entered once, with each purchase's
                real date and cost. Returns from that date then count those holdings as money put in at cost on that
                date. Leave empty to import the full history.
              </p>
            </div>
          </div>
        </div>

        <div v-if="parsed.needs_dividend_account || parsed.needs_taxes_account || parsed.needs_gains_account">
          <h4 class="text-sm font-semibold text-gray-900 dark:text-white">Income, taxes and gains</h4>
          <div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div v-if="parsed.needs_dividend_account">
              <AccountDropdown v-model="mapping.dividend_account" label="Dividend income" :account-types="['Income']" />
              <div v-if="dividendUntagged" class="mt-2 flex items-start gap-2">
                <input id="stmt-tag" v-model="mapping.tag_dividend_account" type="checkbox" class="mt-0.5 size-4 rounded-sm border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/10 dark:bg-white/5" />
                <label for="stmt-tag" class="text-sm text-gray-500 dark:text-gray-400">Mark this account as dividend income</label>
              </div>
            </div>
            <div v-if="parsed.needs_taxes_account">
              <AccountDropdown v-model="mapping.taxes_account" label="Stamp duty, STT and TDS" :account-types="['Expenses', 'Equity']" />
            </div>
            <div v-if="parsed.needs_gains_account">
              <AccountDropdown v-model="mapping.gains_account" label="Capital gains" :account-types="['Income']" />
            </div>
          </div>
        </div>

        <div v-if="parsed.ledger_files.length > 1" class="sm:max-w-xl">
          <h4 class="text-sm font-semibold text-gray-900 dark:text-white">Write to file</h4>
          <div class="mt-2">
            <SelectListbox v-model="targetFile" :options="parsed.ledger_files" :label-of="fileLabel" />
          </div>
        </div>

        <p class="text-sm text-gray-500 dark:text-gray-400">
          Need an account that doesn't exist yet? Create it in
          <router-link to="/accounts" :class="link">Accounts</router-link>, then pick it here.
        </p>
      </section>

      <!-- Preview -->
      <section class="space-y-4">
        <p v-if="previewing" class="text-sm text-gray-500 dark:text-gray-400" role="status">Updating preview…</p>
        <FormFeedback v-if="previewError" :message="previewError" severity="error" />

        <div v-if="plan && plan.blocking.length" class="rounded-md bg-red-50 p-4 dark:bg-red-500/15 dark:outline dark:outline-red-500/25" role="alert">
          <h4 class="text-sm font-medium text-red-800 dark:text-red-200">Before you can register</h4>
          <ul class="mt-2 list-inside list-disc text-sm text-red-700 dark:text-red-200/80">
            <li v-for="(b, i) in plan.blocking" :key="i">{{ b.message }}</li>
          </ul>
        </div>

        <div v-if="plan && plan.warnings.length" class="rounded-md bg-yellow-50 p-4 dark:bg-yellow-500/10 dark:outline dark:outline-yellow-500/15">
          <h4 class="text-sm font-medium text-yellow-800 dark:text-yellow-100">Check these ({{ plan.warnings.length }})</h4>
          <ul class="mt-2 list-inside list-disc text-sm text-yellow-700 dark:text-yellow-100/80">
            <li v-for="(w, i) in shownWarnings" :key="i">
              {{ w.message }}
              <button
                v-if="w.row_id && fundOfRow(w.row_id)"
                type="button"
                class="ml-1 font-medium underline hover:text-yellow-600 dark:hover:text-yellow-200"
                @click="goToRow(w.row_id)"
              >
                Show
              </button>
            </li>
          </ul>
          <button
            v-if="plan.warnings.length > WARNINGS_SHOWN"
            type="button"
            class="mt-2 text-sm font-medium text-yellow-800 underline dark:text-yellow-100"
            @click="allWarnings = !allWarnings"
          >
            {{ allWarnings ? 'Show fewer' : `Show all ${plan.warnings.length}` }}
          </button>
        </div>

        <dl v-if="plan" class="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div v-for="s in stats" :key="s.label" class="overflow-hidden rounded-lg bg-white px-4 py-5 shadow-sm sm:p-6 dark:bg-gray-800/75 dark:inset-ring dark:inset-ring-white/10">
            <dt class="truncate text-sm font-medium text-gray-500 dark:text-gray-400">{{ s.label }}</dt>
            <dd class="mt-1 text-2xl font-semibold tracking-tight text-gray-900 dark:text-white">{{ s.value }}</dd>
          </div>
        </dl>

        <div v-if="plan && plan.unclaimed_bank_transactions?.length" class="text-sm text-gray-500 dark:text-gray-400">
          <p>
            {{ plan.unclaimed_bank_transactions?.length }} bank transaction(s) in {{ mapping.cash_matching_account }}
            during the statement period matched nothing (different amount or date):
          </p>
          <ul class="mt-1 list-inside list-disc">
            <li v-for="(u, i) in (plan.unclaimed_bank_transactions ?? []).slice(0, 10)" :key="i">{{ u.date }} · {{ u.amount }} · {{ u.narration }}</li>
          </ul>
        </div>

        <!-- Funds -->
        <template v-if="plan">
          <ul class="divide-y divide-gray-200 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:divide-white/10 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
            <StatementFundRow
              v-for="f in groups.held"
              :key="fundKey(f)"
              :fund="f"
              :open="openFund === fundKey(f)"
              @toggle="toggleFund(f)"
              @code="(code: string) => setCode(f, code)"
              @hand-entries="(choice: 'replace' | 'after_latest') => (mapping.hand_entries[f.security_key] = choice)"
            >
              <StatementFundDetail
                :fund="f"
                :all-funds="plan.funds"
                :transactions="plan.transactions"
                :focus-row="focusRow"
                @correct="correct"
                @undo="undoCorrection"
              />
            </StatementFundRow>
          </ul>

          <div v-if="groups.redeemed.length">
            <button type="button" class="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white" @click="showRedeemed = !showRedeemed">
              <ChevronRightIcon :class="['size-4 text-gray-400 transition-transform', showRedeemed ? 'rotate-90' : '']" aria-hidden="true" />
              {{ groups.redeemed.length }} fund{{ groups.redeemed.length === 1 ? '' : 's' }} fully redeemed
              <span class="font-normal text-gray-500 dark:text-gray-400">
                ({{ redeemedReconciled }} of {{ groups.redeemed.length }} match the statement)
              </span>
            </button>
            <ul v-if="showRedeemed" class="mt-2 divide-y divide-gray-200 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:divide-white/10 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
              <StatementFundRow
                v-for="f in groups.redeemed"
                :key="fundKey(f)"
                :fund="f"
                :open="openFund === fundKey(f)"
                @toggle="toggleFund(f)"
                @code="(code: string) => setCode(f, code)"
                @hand-entries="(choice: 'replace' | 'after_latest') => (mapping.hand_entries[f.security_key] = choice)"
              >
                <StatementFundDetail
                  :fund="f"
                  :all-funds="plan.funds"
                  :transactions="plan.transactions"
                  :focus-row="focusRow"
                  @correct="correct"
                  @undo="undoCorrection"
                />
              </StatementFundRow>
            </ul>
          </div>
        </template>

        <FormFeedback v-if="commitError" :message="commitError" severity="error" />
        <div v-if="plan" class="flex items-center justify-end gap-3">
          <span v-if="corrections.length" class="text-sm text-gray-500 dark:text-gray-400">
            {{ corrections.length }} row{{ corrections.length === 1 ? '' : 's' }} corrected
          </span>
          <button
            type="button"
            :disabled="!canRegister"
            class="flex items-center gap-2 rounded-md bg-green-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-green-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-green-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-green-500 dark:shadow-none dark:hover:bg-green-400"
            @click="register"
          >
            {{ committing ? 'Registering…' : `Register ${plan.summary.transactions} transactions` }}
          </button>
        </div>
      </section>
    </template>

    <!-- ── Earlier imports ─────────────────────────────────────────────────── -->
    <section v-if="imports.length">
      <h3 class="text-base font-semibold text-gray-900 dark:text-white">Earlier imports</h3>
      <ul class="mt-3 divide-y divide-gray-200 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:divide-white/10 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
        <li v-for="imp in imports" :key="imp.import_id" class="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
          <div class="text-sm">
            <p class="font-medium text-gray-900 dark:text-white">
              {{ imp.date }} · {{ imp.transactions }} transactions
              <span v-if="imp.period_start" class="font-normal text-gray-500 dark:text-gray-400">· statement {{ imp.period_start }} to {{ imp.period_end }}</span>
            </p>
            <p class="text-xs text-gray-500 dark:text-gray-400">{{ imp.import_id }} · {{ fileLabel(imp.file) }}</p>
          </div>
          <button type="button" :class="secondary" @click="undoTarget = imp.import_id">Undo</button>
        </li>
      </ul>
    </section>

    <ConfirmDialog
      :is-open="undoTarget !== null"
      title="Undo this import?"
      message="All transactions and balance checks from this import are removed from your ledger. Funds and prices stay. A backup of the ledger is kept."
      confirm-text="Undo import"
      cancel-text="Keep it"
      variant="danger"
      @confirm="confirmUndo"
      @cancel="undoTarget = null"
      @close="undoTarget = null"
    />
  </div>
</template>

<script setup lang="ts">
  import { computed, nextTick, onMounted, ref, watch } from 'vue'
  import { CheckCircleIcon, ChevronRightIcon } from '@heroicons/vue/20/solid'
  import AccountDropdown from '@/components/common/AccountDropdown.vue'
  import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
  import FormFeedback from '@/components/common/FormFeedback.vue'
  import SelectListbox from '@/components/common/SelectListbox.vue'
  import StatementFundDetail from '@/components/investments/StatementFundDetail.vue'
  import StatementFundRow from '@/components/investments/StatementFundRow.vue'
  import { PricesService, type FundPlan, type PriceUpdateData } from '@/services/generated-api'
  import { useStatementImport } from '@/composables/useStatementImport'
  import { useAccounts } from '@/composables/useAccounts'
  import { useServerNotices } from '@/composables/useServerNotices'
  import { useToast } from '@/composables/useNotifications'
  import { errorHandler } from '@/utils/ErrorHandler'
  import { groupFunds, resultSentence, spanSentence } from '@/utils/statementImport'

  const GUIDE_URL = 'https://docs.finzytrack.com/guides/import-cas/'
  const WARNINGS_SHOWN = 6

  const inputClass =
    'mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500'
  const primary =
    'rounded-md bg-indigo-600 px-2.5 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500'
  const primaryLg =
    'rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500'
  const secondary =
    'rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20'
  const link = 'font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300'

  const {
    parsed, mapping, corrections, plan, result, resultFacts, imports,
    reading, previewing, committing, readError, previewError, commitError,
    read, correct, undoCorrection, commit, loadImports, undo, reset,
  } = useStatementImport()
  const { fetchAccounts } = useAccounts()
  const { check: checkServerNotices } = useServerNotices()
  const { success: showSuccessToast } = useToast()

  // ── File + password ──
  const fileInput = ref<HTMLInputElement | null>(null)
  const file = ref<File | null>(null)
  const password = ref('')
  function onFile(e: Event) {
    file.value = (e.target as HTMLInputElement).files?.[0] ?? null
  }
  async function readStatement() {
    if (!file.value || reading.value) return
    await read(file.value, password.value)
    if (parsed.value) password.value = '' // used once; never kept
  }
  function startOver() {
    reset()
    result.value = null
    priceResult.value = null
    file.value = null
    if (fileInput.value) fileInput.value.value = ''
  }

  // Optional dates: an empty date input means "not set".
  const cutoff = computed({
    get: () => mapping.cutoff_date ?? '',
    set: (v: string) => { mapping.cutoff_date = v || null; if (!v) mapping.cash_before_cutoff_account = '' },
  })
  const historyStart = computed({
    get: () => mapping.history_start ?? '',
    set: (v: string) => { mapping.history_start = v || null },
  })
  const targetFile = computed({
    get: () => mapping.target_file ?? parsed.value?.ledger_files[0] ?? '',
    set: (v: string) => { mapping.target_file = v === parsed.value?.ledger_files[0] ? null : v },
  })
  function fileLabel(path: string): string {
    const root = parsed.value?.ledger_files[0]
    const name = path.split('/').pop() ?? path
    return path === root ? `${name} (main ledger)` : name
  }

  // ── Preview ──
  const groups = computed(() => groupFunds(plan.value?.funds ?? []))
  const redeemedReconciled = computed(() => groups.value.redeemed.filter(f => f.reconciled).length)
  const showRedeemed = ref(false)
  const allWarnings = ref(false)
  const shownWarnings = computed(() =>
    allWarnings.value ? plan.value?.warnings ?? [] : (plan.value?.warnings ?? []).slice(0, WARNINGS_SHOWN),
  )
  const dividendUntagged = computed(() =>
    mapping.tag_dividend_account || (plan.value?.warnings ?? []).some(w => w.code === 'DIVIDEND_ACCOUNT_UNTAGGED'),
  )
  const stats = computed(() => {
    const s = plan.value!.summary
    const reconciled = plan.value!.funds.filter(f => f.reconciled).length
    return [
      { label: 'Transactions to write', value: s.transactions.toLocaleString('en-IN') },
      { label: 'Funds matching the statement', value: `${reconciled} of ${s.funds}` },
      { label: 'Matched / other account', value: `${s.matched} / ${s.fallback}` },
      { label: 'Already in the ledger', value: s.already_imported.toLocaleString('en-IN') },
    ]
  })

  const fundKey = (f: FundPlan) => `${f.folio}|${f.security_key}`
  const openFund = ref<string | null>(null)
  const focusRow = ref<string | null>(null)
  const fundOfRow = (rowId: string) => plan.value?.funds.find(f => f.rows.some(r => r.id === rowId))

  /** Open the fund a message points at, at the row's year, and bring the row into view. */
  async function goToRow(rowId: string) {
    const f = fundOfRow(rowId)
    if (!f) return
    if (!f.held) showRedeemed.value = true
    openFund.value = fundKey(f)
    focusRow.value = rowId
    await nextTick()
    document.getElementById(`row-${rowId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
  function toggleFund(f: FundPlan) {
    openFund.value = openFund.value === fundKey(f) ? null : fundKey(f)
  }
  function setCode(f: FundPlan, code: string) {
    const c = code.trim().toUpperCase()
    if (!c || c === f.commodity) return
    mapping.commodity_codes[f.security_key] = c
  }

  const canRegister = computed(() =>
    !!plan.value && plan.value.blocking.length === 0 && !previewing.value && !committing.value
      && plan.value.summary.transactions + plan.value.balances.length > 0,
  )

  async function register() {
    if (!canRegister.value) return
    const ok = await commit()
    if (ok && result.value) {
      showSuccessToast('Statement imported', `${result.value.transactions} transactions were written to your ledger.`)
      checkServerNotices()
    }
  }

  // ── Prices (§9a.6: the full NAV history fetch, timed) ──
  const fetchingPrices = ref(false)
  const priceResult = ref<PriceUpdateData | null>(null)
  const priceSeconds = ref('0')
  async function updatePrices() {
    fetchingPrices.value = true
    const started = performance.now()
    try {
      const res = await PricesService.updatePrices()
      priceResult.value = res.data ?? null
      priceSeconds.value = ((performance.now() - started) / 1000).toFixed(1)
    } catch (err) {
      errorHandler.display(err)
    } finally {
      fetchingPrices.value = false
    }
  }

  // ── Undo ──
  const undoTarget = ref<string | null>(null)
  async function confirmUndo() {
    const id = undoTarget.value
    undoTarget.value = null
    if (id && await undo(id)) {
      showSuccessToast('Import undone', 'Its transactions and balance checks were removed.')
      checkServerNotices()
    }
  }

  watch(parsed, p => { if (p) fetchAccounts() })
  onMounted(() => { loadImports() })
</script>
