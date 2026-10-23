<template>
  <li class="px-4 py-4">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0 flex-1">
        <p :class="['text-sm font-semibold', fund.excluded ? 'text-gray-500 line-through dark:text-gray-400' : 'text-gray-900 dark:text-white']">
          {{ fund.name }}
        </p>
        <p class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
          Folio {{ fund.folio }} · {{ fund.institution }}<span v-if="fund.investor"> · {{ fund.investor }}</span>
          <span v-if="fund.amfi"> · AMFI {{ fund.amfi }}</span>
        </p>
        <p v-if="fund.excluded" class="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Not imported: no transactions, balance check or price are written for this fund.
        </p>
        <p v-else class="mt-1 text-xs text-gray-500 dark:text-gray-400">
          {{ countsText }}<span v-if="fundCashText(fund)"> · {{ fundCashText(fund) }}</span>
        </p>
      </div>

      <div class="flex shrink-0 flex-wrap items-center gap-3">
        <span v-if="fund.excluded" :class="[badgeBase, badge.gray]">Not imported</span>
        <template v-else>
        <div>
          <label :for="`code-${id}`" class="sr-only">Commodity code</label>
          <input
            :id="`code-${id}`"
            :value="fund.commodity"
            type="text"
            :title="fund.commodity_status === 'existing' ? 'An existing commodity in your ledger' : 'A new commodity (you can change the code)'"
            class="block w-44 rounded-md bg-white px-2 py-1 font-mono text-xs text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
            @change="emit('code', ($event.target as HTMLInputElement).value)"
          />
        </div>
        <span :class="[badgeBase, fund.commodity_status === 'existing' ? badge.blue : badge.gray]">
          {{ fund.commodity_status === 'existing' ? 'Existing' : 'New' }}
        </span>
        <span v-if="fund.reconciled" class="inline-flex items-center gap-1 text-sm text-green-700 dark:text-green-400" :title="`Ledger ${fund.ledger_units} = statement ${fund.close_units}`">
          <CheckCircleIcon class="size-4" aria-hidden="true" /> {{ fund.close_units }} units
        </span>
        <span v-else class="inline-flex items-center gap-1 text-sm text-red-700 dark:text-red-400">
          <ExclamationTriangleIcon class="size-4" aria-hidden="true" />
          {{ fund.ledger_units }} units, statement {{ fund.close_units }}
        </span>
        </template>
        <button
          type="button"
          class="text-sm font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
          @click="emit('exclude', !fund.excluded)"
        >
          {{ fund.excluded ? 'Import this fund' : "Don't import" }}
        </button>
        <button
          type="button"
          class="text-sm font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
          :aria-expanded="open"
          @click="emit('toggle')"
        >
          {{ open ? 'Hide details' : 'Details' }}
        </button>
      </div>
    </div>

    <!-- Hand-entered holdings of this fund (§8): never both silently -->
    <div v-if="fund.hand_entries && !fund.excluded" class="mt-3 rounded-md bg-yellow-50 p-3 dark:bg-yellow-500/10 dark:outline dark:outline-yellow-500/15">
      <p class="text-sm text-yellow-800 dark:text-yellow-100">
        Your ledger already has {{ fund.hand_entries }} transaction{{ fund.hand_entries === 1 ? '' : 's' }} in
        {{ fund.commodity }} that you entered yourself (latest {{ fund.hand_entries_latest }}).
      </p>
      <fieldset class="mt-2">
        <legend class="sr-only">What to do with them</legend>
        <div class="flex flex-wrap gap-x-6 gap-y-2">
          <label v-for="o in handOptions" :key="o.value" class="flex items-center gap-2 text-sm text-yellow-800 dark:text-yellow-100">
            <input
              type="radio"
              :name="`hand-${id}`"
              :value="o.value"
              :checked="fund.hand_entries_choice === o.value"
              class="size-4 border-gray-300 text-indigo-600 focus:ring-indigo-600 dark:border-white/10 dark:bg-white/5"
              @change="emit('handEntries', o.value)"
            />
            {{ o.label }}
          </label>
        </div>
      </fieldset>
    </div>

    <div v-if="open" class="mt-4">
      <slot />
    </div>
  </li>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import { CheckCircleIcon, ExclamationTriangleIcon } from '@heroicons/vue/20/solid'
  import type { FundPlan } from '@/services/generated-api'
  import { fundCashText } from '@/utils/statementImport'

  const props = defineProps<{ fund: FundPlan; open: boolean }>()
  const emit = defineEmits<{
    toggle: []
    code: [code: string]
    handEntries: [choice: 'replace' | 'after_latest']
    exclude: [on: boolean]
  }>()

  const id = computed(() => `${props.fund.folio}-${props.fund.security_key}`.replace(/[^A-Za-z0-9-]/g, '-'))
  const badgeBase = 'inline-flex items-center rounded-md px-2 py-1 text-xs font-medium'
  const badge = {
    gray: 'bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400',
    blue: 'bg-blue-100 text-blue-700 dark:bg-blue-400/10 dark:text-blue-400',
  }
  const handOptions = [
    { value: 'replace' as const, label: 'Replace them with the statement history' },
    { value: 'after_latest' as const, label: 'Keep them; import only what comes after' },
  ]

  const countsText = computed(() => {
    const c = props.fund.counts as Record<string, number>
    const parts: string[] = []
    if (c.import) parts.push(`${c.import} to import`)
    if (c.already) parts.push(`${c.already} already in ledger`)
    if (c.before_start) parts.push(`${c.before_start} before the start date`)
    if (c.before_hand) parts.push(`${c.before_hand} covered by your entries`)
    if (c.unsupported) parts.push(`${c.unsupported} not imported`)
    if (c.failed) parts.push(`${c.failed} failed`)
    if (c.excluded) parts.push(`${c.excluded} excluded`)
    return parts.join(' · ') || 'Nothing to import'
  })
</script>
