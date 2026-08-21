<template>
  <div>
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <AccountDropdown
        v-model="holdingAccount"
        label="Holding account (sell from)"
        :account-types="['Assets']"
        :allow-custom="true"
        placeholder="e.g. Assets:Broker:VOO"
      />
      <CommodityDropdown
        v-model="security"
        label="Security"
        :holdings-only="true"
        placeholder="e.g. VOO"
      />
    </div>

    <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Sale price per unit</label>
        <input v-model="salePrice" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 560.00" />
      </div>
      <AccountDropdown
        v-model="cashAccount"
        label="Cash account (proceeds)"
        :account-types="['Assets']"
        :allow-custom="true"
        placeholder="e.g. Assets:Cash"
      />
    </div>

    <div class="mt-4">
      <AccountDropdown
        v-model="gainsAccount"
        label="Capital-gains account"
        :account-types="['Income']"
        :allow-custom="true"
        placeholder="e.g. Income:CapitalGains"
      />
      <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
        Tag it <code>income-type: "capital-gains"</code> to label it (realized gains are detected
        structurally, so this is descriptive).
      </p>
    </div>

    <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <label class="block text-sm font-medium text-gray-900 dark:text-white">Fee <span class="text-gray-400">(optional)</span></label>
        <input v-model="fee" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 4.95" />
      </div>
      <AccountDropdown
        v-if="hasFee"
        v-model="feeAccount"
        label="Fee account"
        :account-types="['Expenses']"
        :allow-custom="true"
        placeholder="e.g. Expenses:Fees"
      />
    </div>

    <!-- Lot selection -->
    <div v-if="security && holdingAccount" class="mt-6">
      <div v-if="loadingHoldings" class="text-sm text-gray-500 dark:text-gray-400">Loading lots…</div>

      <div v-else-if="availableLots.length === 0" class="rounded-md bg-gray-50 p-3 text-sm text-gray-600 dark:bg-white/5 dark:text-gray-300">
        No open lots of {{ security }} in {{ holdingAccount }}. Record a Buy or Add Holding first.
      </div>

      <div v-else>
        <div class="mb-3 flex items-center justify-between">
          <label class="block text-sm font-medium text-gray-900 dark:text-white">Which lots?</label>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="s in strategies"
              :key="s.id"
              type="button"
              @click="strategy = s.id"
              :class="[
                strategy === s.id
                  ? 'bg-indigo-600 text-white dark:bg-indigo-500'
                  : 'bg-white text-gray-700 inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-gray-200 dark:inset-ring-white/10',
                'rounded-full px-2.5 py-1 text-xs font-medium',
              ]"
            >
              {{ s.label }}
            </button>
          </div>
        </div>

        <!-- Total units (strategy modes) -->
        <div v-if="strategy !== 'specific'" class="mb-3">
          <label class="block text-sm font-medium text-gray-900 dark:text-white">Units to sell</label>
          <input v-model="totalUnitsInput" type="text" inputmode="decimal" :class="inputClass" placeholder="e.g. 5" />
        </div>

        <!-- Lot table -->
        <div class="overflow-hidden rounded-lg ring-1 ring-gray-200 dark:ring-white/10">
          <table class="min-w-full divide-y divide-gray-200 dark:divide-white/10">
            <thead class="bg-gray-50 dark:bg-white/5">
              <tr>
                <th class="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400">Acquired</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">Available</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">Unit cost</th>
                <th class="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">Selling</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-white/5">
              <tr v-for="lot in availableLots" :key="lot.key">
                <td class="px-3 py-2 text-sm text-gray-900 dark:text-white">{{ lot.date }}</td>
                <td class="px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">{{ lot.unitsAvail }}</td>
                <td class="px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">{{ lot.cost }} {{ lot.cost_currency }}</td>
                <td class="px-3 py-2 text-right text-sm">
                  <input
                    v-if="strategy === 'specific'"
                    v-model="lotUnits[lot.key]"
                    type="text"
                    inputmode="decimal"
                    class="w-24 rounded-md bg-white px-2 py-1 text-right text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10"
                    placeholder="0"
                  />
                  <span v-else class="text-gray-700 dark:text-gray-300">{{ consumedByKey[lot.key] ?? '—' }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Live preview -->
        <div class="mt-3 rounded-lg bg-gray-50 p-3 text-sm dark:bg-white/5">
          <div class="flex justify-between"><span class="text-gray-500 dark:text-gray-400">Units</span><span class="font-medium text-gray-900 dark:text-white">{{ fmt(totalUnits) }}</span></div>
          <div class="flex justify-between"><span class="text-gray-500 dark:text-gray-400">Cost basis</span><span class="font-medium text-gray-900 dark:text-white">{{ fmt(costBasis) }} {{ currency }}</span></div>
          <div class="flex justify-between"><span class="text-gray-500 dark:text-gray-400">Proceeds</span><span class="font-medium text-gray-900 dark:text-white">{{ fmt(proceeds) }} {{ currency }}</span></div>
          <div class="mt-1 flex justify-between border-t border-gray-200 pt-1 dark:border-white/10">
            <span class="text-gray-500 dark:text-gray-400">Realized gain</span>
            <span :class="['font-semibold', gainSign < 0 ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400']">{{ fmt(gain) }} {{ currency }}</span>
          </div>
          <p v-if="!fullyCovered" class="mt-2 text-xs text-yellow-700 dark:text-yellow-400">
            {{ coverageMessage }}
          </p>
        </div>
      </div>
    </div>

    <div class="mt-6 flex justify-end">
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
  import { ref, reactive, computed, watch } from 'vue'
  import AccountDropdown from '@/components/common/AccountDropdown.vue'
  import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
  import type { AssembleRequest, LotSelection } from '@/services/generated-api'
  import { useInvestmentActions } from '@/composables/useInvestmentActions'
  import { useAccounts } from '@/composables/useAccounts'
  import type { Money } from '@/utils/money'
  import { toMoney, add, sub, mul, gt, lt, sign, toFixed, zero } from '@/utils/money'

  type SaleStrategy = 'specific' | 'fifo' | 'lifo' | 'hifo'
  type InvestmentEvent = AssembleRequest['events'][number]

  const props = defineProps<{ date: string; currency: string; isLoading: boolean }>()
  const emit = defineEmits<{ submit: [event: InvestmentEvent] }>()

  const { fetchHoldings } = useInvestmentActions()
  const { accountDetails } = useAccounts()

  const inputClass =
    'mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500'

  const strategies: { id: SaleStrategy; label: string }[] = [
    { id: 'fifo', label: 'FIFO' },
    { id: 'lifo', label: 'LIFO' },
    { id: 'hifo', label: 'HIFO' },
    { id: 'specific', label: 'Specific lots' },
  ]

  const holdingAccount = ref('')
  const security = ref('')
  const salePrice = ref('')
  const cashAccount = ref('')
  const gainsAccount = ref('')
  const fee = ref('')
  const feeAccount = ref('')
  const strategy = ref<SaleStrategy>('fifo')
  const totalUnitsInput = ref('')
  const lotUnits = reactive<Record<string, string>>({})

  interface Lot { key: string; date: string; unitsAvail: Money; cost: Money; costRaw: string; cost_currency: string }
  const availableLots = ref<Lot[]>([])
  const loadingHoldings = ref(false)

  const hasFee = computed(() => isPositive(fee.value))

  function isPositive(v: string): boolean {
    if (!v) return false
    const n = Number(v)
    return Number.isFinite(n) && n > 0
  }

  function fmt(m: Money): string {
    return toFixed(m, 2)
  }

  // Fetch lots whenever the security + holding account are set.
  watch([security, holdingAccount], async ([sec, acct]) => {
    Object.keys(lotUnits).forEach(k => delete lotUnits[k])
    availableLots.value = []
    if (!sec || !acct) return
    loadingHoldings.value = true
    try {
      const holding = await fetchHoldings(sec)
      const lots = (holding?.lots ?? []).filter(l => l.account === acct)
      availableLots.value = lots.map(l => ({
        key: `${l.acquisition_date}|${l.cost_number}|${l.cost_currency}`,
        date: l.acquisition_date,
        unitsAvail: toMoney(l.units),
        cost: toMoney(l.cost_number),
        costRaw: l.cost_number, // exact mirror value — sent verbatim so the lot matches
        cost_currency: l.cost_currency,
      }))
    } finally {
      loadingHoldings.value = false
    }
  })

  // Suggest a capital-gains income account (metadata-driven; never by name).
  watch([security, () => accountDetails.value], () => {
    if (gainsAccount.value) return
    const gains = accountDetails.value.filter(
      a => String(a.metadata?.['income-type'] ?? '').toLowerCase() === 'capital-gains'
    )
    if (gains.length === 1) gainsAccount.value = gains[0].name
  })

  function orderedLots(): Lot[] {
    const ls = [...availableLots.value]
    if (strategy.value === 'lifo') return ls.sort((a, b) => b.date.localeCompare(a.date))
    if (strategy.value === 'hifo') return ls.sort((a, b) => sign(sub(b.cost, a.cost)))
    return ls.sort((a, b) => a.date.localeCompare(b.date)) // fifo
  }

  interface Consumed { key: string; units: Money; cost: Money; costRaw: string; date: string; cost_currency: string }

  const consumed = computed<Consumed[]>(() => {
    if (strategy.value === 'specific') {
      return availableLots.value
        .filter(l => isPositive(lotUnits[l.key] ?? ''))
        .map(l => ({ key: l.key, units: toMoney(lotUnits[l.key]), cost: l.cost, costRaw: l.costRaw, date: l.date, cost_currency: l.cost_currency }))
    }
    // strategy fill
    let remaining = isPositive(totalUnitsInput.value) ? toMoney(totalUnitsInput.value) : zero()
    const out: Consumed[] = []
    for (const l of orderedLots()) {
      if (!gt(remaining, zero())) break
      const take = gt(l.unitsAvail, remaining) ? remaining : l.unitsAvail
      out.push({ key: l.key, units: take, cost: l.cost, costRaw: l.costRaw, date: l.date, cost_currency: l.cost_currency })
      remaining = sub(remaining, take)
    }
    return out
  })

  const consumedByKey = computed<Record<string, string>>(() => {
    const m: Record<string, string> = {}
    consumed.value.forEach(c => { m[c.key] = fmt(c.units) })
    return m
  })

  const totalUnits = computed<Money>(() => consumed.value.reduce((s, c) => add(s, c.units), zero()))
  const costBasis = computed<Money>(() => consumed.value.reduce((s, c) => add(s, mul(c.units, c.cost)), zero()))
  const proceeds = computed<Money>(() => isPositive(salePrice.value) ? mul(totalUnits.value, toMoney(salePrice.value)) : zero())
  const gain = computed<Money>(() => sub(proceeds.value, costBasis.value))
  const gainSign = computed(() => sign(gain.value))

  const totalAvailable = computed<Money>(() => availableLots.value.reduce((s, l) => add(s, l.unitsAvail), zero()))

  // A specific-lot pick must not exceed any lot's available units.
  const specificOverflow = computed(() =>
    availableLots.value.some(l => isPositive(lotUnits[l.key] ?? '') && gt(toMoney(lotUnits[l.key]), l.unitsAvail))
  )

  const fullyCovered = computed(() => {
    if (!gt(totalUnits.value, zero())) return false
    if (strategy.value === 'specific') return !specificOverflow.value
    // strategy mode: the requested total must be fully consumable
    if (!isPositive(totalUnitsInput.value)) return false
    return !lt(totalAvailable.value, toMoney(totalUnitsInput.value))
  })

  const coverageMessage = computed(() => {
    if (specificOverflow.value) return 'A selected quantity exceeds the lot’s available units.'
    if (strategy.value !== 'specific' && isPositive(totalUnitsInput.value) && lt(totalAvailable.value, toMoney(totalUnitsInput.value)))
      return `Only ${fmt(totalAvailable.value)} units available; reduce the quantity.`
    return 'Select the units to sell.'
  })

  const canSubmit = computed(() =>
    !!holdingAccount.value && !!security.value && isPositive(salePrice.value) &&
    !!cashAccount.value && !!gainsAccount.value &&
    (!hasFee.value || !!feeAccount.value) &&
    availableLots.value.length > 0 && fullyCovered.value
  )

  function submit() {
    if (!canSubmit.value) return
    const common = {
      date: props.date,
      currency: props.currency,
    }
    const event: Record<string, unknown> = {
      ...common,
      type: 'sell',
      holding_account: holdingAccount.value,
      security: security.value,
      units: fmt(totalUnits.value),
      sale_price: salePrice.value,
      cash_account: cashAccount.value,
      gains_account: gainsAccount.value,
    }
    if (hasFee.value) {
      event.fee = fee.value
      event.fee_account = feeAccount.value
    }
    if (strategy.value === 'specific') {
      event.lots = consumed.value.map<LotSelection>(c => ({
        acquisition_date: c.date,
        cost_number: c.costRaw, // verbatim mirror value → exact lot match
        cost_currency: c.cost_currency,
        units: fmt(c.units),
      }))
    } else {
      event.strategy = strategy.value
    }
    emit('submit', event as unknown as InvestmentEvent)
  }

  defineExpose({ reset })
  function reset() {
    salePrice.value = ''
    totalUnitsInput.value = ''
    fee.value = ''
    Object.keys(lotUnits).forEach(k => delete lotUnits[k])
  }
</script>
