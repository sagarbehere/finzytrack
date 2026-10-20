<template>
  <div class="space-y-6">
    <!-- Intro + actions -->
    <div class="sm:flex sm:items-start sm:justify-between sm:gap-6">
      <p class="max-w-2xl text-sm text-gray-500 dark:text-gray-400">
        The prices used to value your holdings. Update prices fetches new ones; you can also add,
        change or delete any price here. A holding with no price is valued at cost.
      </p>
      <div class="mt-4 flex shrink-0 gap-3 sm:mt-0">
        <button
          type="button"
          class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
          @click="addOpen = true"
        >
          Add price
        </button>
        <button
          type="button"
          :disabled="isFetching"
          class="inline-flex items-center gap-x-1.5 rounded-md bg-indigo-600 px-2.5 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
          @click="fetchPrices"
        >
          <ArrowPathIcon :class="['-ml-0.5 size-5', isFetching ? 'animate-spin' : '']" aria-hidden="true" />
          {{ isFetching ? 'Updating…' : 'Update prices' }}
        </button>
      </div>
    </div>

    <!-- Result of the last Update prices click -->
    <div v-if="lastRun" :class="runFailed.length > 0 ? warnBox : successBox">
      <div class="flex">
        <component
          :is="runFailed.length > 0 ? ExclamationTriangleIcon : CheckCircleIcon"
          :class="['size-5 shrink-0', runFailed.length > 0 ? 'text-yellow-400 dark:text-yellow-300' : 'text-green-400']"
          aria-hidden="true"
        />
        <div class="ml-3 flex-1">
          <h3 :class="['text-sm font-medium', runFailed.length > 0 ? 'text-yellow-800 dark:text-yellow-100' : 'text-green-800 dark:text-green-200']">
            Price update at {{ runTime }}
          </h3>
          <div :class="['mt-2 space-y-1 text-sm', runFailed.length > 0 ? 'text-yellow-700 dark:text-yellow-100/80' : 'text-green-700 dark:text-green-200/85']">
            <p>{{ runSummary }}</p>
            <p v-if="lastRun.result.skipped?.length">Not fetched: {{ lastRun.result.skipped.join(', ') }}.</p>
            <template v-if="runFailed.length > 0">
              <p>Could not fetch (valued at the latest price, or at cost if there is none):</p>
              <ul class="list-disc space-y-1 pl-5">
                <li v-for="f in runFailed" :key="f">{{ f }}</li>
              </ul>
              <p>
                Check the price symbol in
                <button type="button" class="font-medium underline hover:text-yellow-600 dark:hover:text-yellow-50" @click="emit('open-metadata')">
                  Setup &amp; Metadata
                </button>.
              </p>
            </template>
          </div>
        </div>
        <button
          type="button"
          :class="['ml-3 inline-flex shrink-0 self-start rounded-md', runFailed.length > 0 ? 'text-yellow-500 hover:text-yellow-600 dark:text-yellow-300' : 'text-green-500 hover:text-green-600 dark:text-green-300']"
          @click="lastRun = null"
        >
          <span class="sr-only">Dismiss</span>
          <XMarkIcon class="size-5" aria-hidden="true" />
        </button>
      </div>
    </div>

    <!-- Filter pills -->
    <nav class="flex space-x-4" aria-label="Filter">
      <button
        v-for="f in filters"
        :key="f.id"
        type="button"
        :aria-current="filter === f.id ? 'page' : undefined"
        :class="[
          filter === f.id
            ? 'bg-gray-100 text-gray-700 dark:bg-white/10 dark:text-gray-200'
            : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
          'rounded-md px-3 py-2 text-sm font-medium',
        ]"
        @click="filter = f.id"
      >
        {{ f.label }}
      </button>
    </nav>

    <div v-if="isLoading && rows.length === 0" class="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
      Loading…
    </div>
    <div v-else-if="error && rows.length === 0" class="rounded-md bg-red-50 p-4 dark:bg-red-500/15 dark:outline dark:outline-red-500/25">
      <div class="flex">
        <XCircleIcon class="size-5 shrink-0 text-red-400" aria-hidden="true" />
        <div class="ml-3">
          <h3 class="text-sm font-medium text-red-800 dark:text-red-200">{{ error }}</h3>
          <div class="mt-4">
            <button
              type="button"
              class="rounded-md bg-red-50 px-2 py-1.5 text-sm font-medium text-red-800 hover:bg-red-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 dark:bg-transparent dark:text-red-200 dark:hover:bg-white/10"
              @click="load"
            >
              Try again
            </button>
          </div>
        </div>
      </div>
    </div>
    <p v-else-if="visibleRows.length === 0" class="text-sm text-gray-500 dark:text-gray-400">
      {{ filter === 'held' && rows.length > 0 ? 'Nothing is held right now.' : 'No prices or holdings yet.' }}
      <button
        v-if="filter === 'held' && rows.length > 0"
        type="button"
        class="font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
        @click="filter = 'all'"
      >
        Show all
      </button>
    </p>

    <div v-else class="overflow-x-auto shadow-sm outline-1 outline-black/5 sm:rounded-lg dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
      <table class="min-w-full divide-y divide-gray-300 dark:divide-white/15">
        <thead class="bg-gray-50 dark:bg-gray-800/75">
          <tr>
            <th scope="col" class="py-3.5 pr-3 pl-4 text-left text-sm font-semibold text-gray-900 sm:pl-6 dark:text-white">Commodity</th>
            <th scope="col" class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900 dark:text-white">Asset class</th>
            <th scope="col" class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900 dark:text-white">Fetch</th>
            <th scope="col" class="px-3 py-3.5 text-right text-sm font-semibold text-gray-900 dark:text-white">Latest price</th>
            <th scope="col" class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900 dark:text-white">As of</th>
            <th scope="col" class="py-3.5 pr-4 pl-3 sm:pr-6"><span class="sr-only">History</span></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200 bg-white dark:divide-white/10 dark:bg-gray-800/50">
          <tr
            v-for="row in visibleRows"
            :key="rowKey(row)"
            class="cursor-pointer hover:bg-gray-50 dark:hover:bg-white/5"
            @click="openHistory(row)"
          >
            <td class="py-4 pr-3 pl-4 text-sm font-medium whitespace-nowrap text-gray-900 sm:pl-6 dark:text-white">
              {{ row.commodity }}
              <span
                v-if="!row.held"
                class="ml-2 inline-flex items-center rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
              >
                Not held
              </span>
            </td>
            <td class="px-3 py-4 text-sm whitespace-nowrap">
              <span v-if="row.asset_class" class="text-gray-900 dark:text-white">{{ row.asset_class }}</span>
              <span v-else class="text-gray-400 dark:text-gray-500">Not set</span>
            </td>
            <td class="px-3 py-4 text-sm">
              <div class="whitespace-nowrap text-gray-900 dark:text-white">{{ cadenceLabel(row.cadence) }}</div>
              <div v-if="row.not_fetched_reason" class="mt-1 text-gray-500 dark:text-gray-400">{{ row.not_fetched_reason }}</div>
            </td>
            <td class="px-3 py-4 text-right text-sm whitespace-nowrap text-gray-900 tabular-nums dark:text-white">
              <template v-if="row.latest">{{ formatPrice(row.latest.number, row.quote_currency) }}</template>
              <span v-else class="text-gray-400 dark:text-gray-500">No price</span>
            </td>
            <td class="px-3 py-4 text-sm whitespace-nowrap text-gray-500 dark:text-gray-400">
              <template v-if="row.latest">
                {{ formatShortDate(row.latest.date, today) }} ({{ formatAge(row.age_days ?? 0) }})
              </template>
              <span
                v-if="row.out_of_date"
                :class="['inline-flex items-center rounded-md bg-yellow-100 px-2 py-1 text-xs font-medium text-yellow-800 dark:bg-yellow-400/10 dark:text-yellow-500', row.latest ? 'ml-2' : '']"
              >
                Out of date
              </span>
              <span
                v-if="row.latest?.source === 'ledger'"
                class="ml-2 inline-flex items-center rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
                title="The latest price is written in your ledger file."
              >
                In ledger file
              </span>
            </td>
            <td class="py-4 pr-4 pl-3 text-right text-sm font-medium whitespace-nowrap sm:pr-6">
              <button
                type="button"
                class="text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
                @click.stop="openHistory(row)"
              >
                History<span class="sr-only">, {{ row.commodity }} in {{ row.quote_currency }}</span>
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="hiddenCount > 0" class="text-sm text-gray-500 dark:text-gray-400">
      {{ hiddenCount }} not held, not shown.
      <button type="button" class="font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300" @click="filter = 'all'">
        Show all
      </button>
    </p>

    <PriceHistoryDrawer
      v-model:open="historyOpen"
      :row="selectedRow"
      :today="today"
      :saving="isSaving"
      :save="save"
      @select="(commodity, quote) => (selectedKey = `${commodity}|${quote}`)"
    />

    <AddPriceModal
      v-model:open="addOpen"
      :default-quote="defaultQuote"
      :saving="isSaving"
      :today="today"
      :save="save"
      @saved="onAdded"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * Investments → Prices: one row per (commodity, quote currency) with prices or
 * held units — how it is fetched, its latest price, and whether that is out of
 * date. Rows open their price history. See dev-docs/investment-view.md §10.2.
 */
import { computed, onMounted, ref } from 'vue'
import { ArrowPathIcon, CheckCircleIcon, ExclamationTriangleIcon, XCircleIcon, XMarkIcon } from '@heroicons/vue/20/solid'
import type { PriceOverviewRow } from '@/services/generated-api'
import AddPriceModal from '@/components/investments/AddPriceModal.vue'
import PriceHistoryDrawer from '@/components/investments/PriceHistoryDrawer.vue'
import { usePrices } from '@/composables/usePrices'
import { useToast } from '@/composables/useNotifications'
import {
  cadenceLabel,
  formatAge,
  formatPrice,
  formatShortDate,
  type PriceInput,
} from '@/utils/prices'

const emit = defineEmits<{ 'open-metadata': [] }>()

const { rows, today, isLoading, isFetching, isSaving, error, lastRun, load, fetchPrices, save } = usePrices()
const { success: showSuccessToast } = useToast()

onMounted(load)

const filters = [
  { id: 'held', label: 'Held' },
  { id: 'all', label: 'All' },
] as const
const filter = ref<'held' | 'all'>('held')

const visibleRows = computed(() => rows.value.filter(r => filter.value === 'all' || r.held))
const hiddenCount = computed(() => rows.value.length - visibleRows.value.length)

function rowKey(row: PriceOverviewRow): string {
  return `${row.commodity}|${row.quote_currency ?? ''}`
}

// The drawer follows the selected pair across reloads (a save refreshes rows).
const selectedKey = ref<string | null>(null)
const selectedRow = computed(() => rows.value.find(r => rowKey(r) === selectedKey.value) ?? null)
const historyOpen = ref(false)

function openHistory(row: PriceOverviewRow) {
  selectedKey.value = rowKey(row)
  historyOpen.value = true
}

// ── Add price (header) ──

const addOpen = ref(false)
// The most used quote currency among the rows, as the form's default.
const defaultQuote = computed(() => {
  const counts = new Map<string, number>()
  for (const r of rows.value) {
    if (r.quote_currency) counts.set(r.quote_currency, (counts.get(r.quote_currency) ?? 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? ''
})

function onAdded(input: PriceInput, replaced: boolean) {
  addOpen.value = false
  const code = input.commodity.trim()
  showSuccessToast('Saved', replaced ? `Replaced a price for ${code}.` : `Added a price for ${code}.`)
}

// ── Last fetch result ──

const successBox = 'rounded-md bg-green-50 p-4 dark:bg-green-500/10 dark:outline dark:outline-green-500/15'
const warnBox = 'rounded-md bg-yellow-50 p-4 dark:bg-yellow-500/10 dark:outline dark:outline-yellow-500/15'

const runFailed = computed(() => lastRun.value?.result.failed ?? [])
const runTime = computed(() =>
  lastRun.value?.at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) ?? '',
)
const runSummary = computed(() => {
  const r = lastRun.value?.result
  if (!r) return ''
  const fetched = r.symbols?.length ?? 0
  if (fetched === 0) return 'No prices were fetched.'
  const changes: string[] = []
  if (r.added > 0) changes.push(`${r.added} new ${r.added === 1 ? 'price' : 'prices'}`)
  if ((r.updated ?? 0) > 0) changes.push(`${r.updated} updated`)
  const what = changes.length > 0 ? changes.join(', ') : 'no changes'
  return `Fetched ${fetched} ${fetched === 1 ? 'holding' : 'holdings'}: ${what}.`
})
</script>
