<template>
  <div class="pb-8">
    <!-- Header -->
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Budgets</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        Set a budget for any account, effective from a date.
      </p>
    </div>

    <!-- Filters -->
    <div class="mb-4 flex flex-wrap items-end gap-4">
      <div>
        <label class="block text-xs font-medium text-gray-500 dark:text-gray-400">Filter by account</label>
        <div class="relative mt-1 w-64">
          <input
            v-model="filter"
            type="text"
            placeholder="Account…"
            class="block w-full rounded-md bg-white py-1.5 pr-8 pl-3 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500"
          />
          <button
            v-if="filter"
            type="button"
            @click="filter = ''"
            aria-label="Clear account filter"
            class="absolute inset-y-0 right-0 flex items-center pr-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
          >
            <XMarkIcon class="size-4" aria-hidden="true" />
          </button>
        </div>
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-500 dark:text-gray-400">Budget as of</label>
        <input
          v-model="asOf"
          type="date"
          class="mt-1 block rounded-md bg-white px-3 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10"
        />
      </div>
      <div v-if="currencies.length > 1" class="w-40">
        <label class="block text-xs font-medium text-gray-500 dark:text-gray-400">Currency</label>
        <Listbox v-model="currencyFilter" as="div" class="relative mt-1">
          <ListboxButton
            class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10"
          >
            <span class="col-start-1 row-start-1 truncate pr-6">{{ currencyFilter || 'All currencies' }}</span>
            <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
          </ListboxButton>
          <ListboxOptions
            class="absolute z-30 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10"
          >
            <ListboxOption v-for="c in ['', ...currencies]" :key="c || 'all'" :value="c" as="template" v-slot="{ active, selected }">
              <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ c || 'All currencies' }}</span>
                <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                  <CheckIcon class="size-5" aria-hidden="true" />
                </span>
              </li>
            </ListboxOption>
          </ListboxOptions>
        </Listbox>
      </div>
    </div>

    <!-- Table (desktop). Internal scroll keeps the header pinned and the action
         buttons below the table from being pushed down as rows grow. -->
    <div v-if="isMd" class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <div class="max-h-[60vh] overflow-y-auto">
      <table class="min-w-full divide-y divide-gray-200 dark:divide-white/10">
        <thead class="sticky top-0 z-10 bg-gray-50 dark:bg-gray-800">
          <tr class="text-left text-xs font-medium tracking-wider text-gray-500 uppercase dark:text-gray-400">
            <th class="w-8 px-2 py-3"></th>
            <th class="px-4 py-3">Account</th>
            <th class="px-4 py-3">Currency</th>
            <th class="px-4 py-3">Budget</th>
            <th class="px-4 py-3 text-right">New budget</th>
            <th class="px-4 py-3">Interval</th>
            <th class="px-4 py-3">Effective</th>
            <th class="px-4 py-3">Status</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 dark:divide-white/5">
          <!-- Quick-add row: a brand-new budget for an unbudgeted (account, currency). -->
          <tr class="bg-gray-50 dark:bg-white/5">
            <td class="px-2 py-3"></td>
            <td class="px-4 py-3">
              <AccountDropdown v-model="newRow.account" :allow-custom="false" :include-groups="true" placeholder="Account…" />
            </td>
            <td class="px-4 py-3">
              <div class="w-32">
                <CommodityDropdown
                  v-model="newRow.currency"
                  :allow-custom="false"
                  :show-details="false"
                  placeholder="Currency…"
                />
              </div>
            </td>
            <td class="px-4 py-3 text-sm text-gray-400 dark:text-gray-500">—</td>
            <td class="px-4 py-3 text-right">
              <input v-model="newRow.amount" placeholder="0" :class="inputClass + ' text-right w-28'" />
            </td>
            <td class="px-4 py-3">
              <div class="w-40">
                <IntervalDropdown v-model="newRow.interval" />
              </div>
            </td>
            <td class="px-4 py-3">
              <input v-model="newRow.date" type="date" :class="inputClass" />
            </td>
            <td class="px-4 py-3 text-right">
              <button
                type="button"
                :disabled="!canAdd || isSaving"
                @click="addRow"
                class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 dark:bg-white/10 dark:text-white dark:inset-ring-white/10"
              >Add</button>
            </td>
          </tr>

          <!-- One row per (account, currency): current effective budget + a forward edit. -->
          <template v-for="row in filteredRows" :key="row.key">
            <tr :class="row.newAmount.trim() ? 'bg-amber-50 dark:bg-amber-500/10' : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'">
              <td class="px-2 py-3">
                <button
                  type="button"
                  @click="toggleExpand(row.key)"
                  class="rounded p-0.5 text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-white/5 dark:hover:text-gray-300"
                  :aria-label="expandedKey === row.key ? 'Collapse history' : 'Expand history'"
                >
                  <ChevronRightIcon class="size-4 transition-transform" :class="expandedKey === row.key ? 'rotate-90' : ''" />
                </button>
              </td>
              <td class="px-4 py-3 text-sm font-medium text-gray-900 dark:text-white">{{ row.account }}</td>
              <td class="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">{{ row.currency }}</td>
              <td class="px-4 py-3 text-sm">
                <span v-if="row.current?.ended" class="text-gray-400 italic dark:text-gray-500">Ended {{ row.current.date }}</span>
                <span v-else-if="row.current" class="text-gray-900 dark:text-white">{{ row.current.amount }} · {{ row.current.interval }}</span>
                <span v-else class="text-gray-400 dark:text-gray-500">—</span>
              </td>
              <td class="px-4 py-3 text-right">
                <input v-model="row.newAmount" @input="clearStatus(row.key)" placeholder="—" :class="inputClass + ' text-right w-28'" />
              </td>
              <td class="px-4 py-3">
                <div class="w-40">
                  <IntervalDropdown v-model="row.newInterval" />
                </div>
              </td>
              <td class="px-4 py-3">
                <input v-model="row.newDate" type="date" :class="inputClass" />
              </td>
              <td class="px-4 py-3">
                <PencilSquareIcon
                  v-if="row.newAmount.trim()"
                  class="size-5 text-indigo-600 dark:text-indigo-400"
                  title="Edited — click “Save Changes” to apply"
                />
                <CheckCircleIcon
                  v-else-if="statusMap.get(row.key)?.state === 'saved'"
                  class="size-5 text-green-600 dark:text-green-400"
                  title="Saved"
                />
                <ExclamationCircleIcon
                  v-else-if="statusMap.get(row.key)?.state === 'failed'"
                  class="size-5 text-red-600 dark:text-red-400"
                  :title="statusMap.get(row.key)?.msg"
                />
              </td>
            </tr>
            <tr v-if="expandedKey === row.key">
              <td></td>
              <td colspan="7" class="px-4 py-3">
                <BudgetHistoryPanel :account="row.account" :currency="row.currency" :directives="row.directives" @changed="refresh" />
              </td>
            </tr>
          </template>

          <tr v-if="!isLoading && groupRows.length === 0">
            <td colspan="8" class="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
              No budgets yet. Add one above.
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>

    <!-- Card list (mobile / narrow viewport). -->
    <div v-else class="space-y-2">
      <!-- Quick-add card -->
      <div class="rounded-lg bg-gray-50 p-3 ring-1 ring-gray-200 dark:bg-white/5 dark:ring-white/10">
        <p class="mb-2 text-xs font-medium tracking-wider text-gray-500 uppercase dark:text-gray-400">Add a budget</p>
        <div class="space-y-2">
          <AccountDropdown v-model="newRow.account" :allow-custom="false" :include-groups="true" placeholder="Account…" />
          <div class="flex gap-2">
            <div class="flex-1">
              <CommodityDropdown v-model="newRow.currency" :allow-custom="false" :show-details="false" placeholder="Currency…" />
            </div>
            <div class="flex-1">
              <IntervalDropdown v-model="newRow.interval" />
            </div>
          </div>
          <div class="flex gap-2">
            <input v-model="newRow.amount" placeholder="Amount" :class="inputClass + ' flex-1 text-right'" />
            <input v-model="newRow.date" type="date" :class="inputClass + ' flex-1'" />
          </div>
          <button
            type="button"
            :disabled="!canAdd || isSaving"
            @click="addRow"
            class="w-full rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 dark:bg-white/10 dark:text-white dark:inset-ring-white/10"
          >Add</button>
        </div>
      </div>

      <!-- One card per (account, currency). -->
      <div
        v-for="row in filteredRows"
        :key="row.key"
        class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10"
        :class="row.newAmount.trim() ? 'ring-amber-300 dark:ring-amber-500/30' : ''"
      >
        <div class="flex items-center gap-2 px-3 py-2.5">
          <button
            type="button"
            @click="toggleExpand(row.key)"
            class="shrink-0 rounded p-1 text-gray-400 hover:bg-gray-200 dark:hover:bg-white/5"
            :aria-label="expandedKey === row.key ? 'Collapse history' : 'Expand history'"
          >
            <ChevronRightIcon class="size-4 transition-transform" :class="expandedKey === row.key ? 'rotate-90' : ''" />
          </button>
          <div class="min-w-0 flex-1">
            <span class="block truncate text-sm font-medium text-gray-900 dark:text-white">{{ row.account }}</span>
            <span class="text-xs text-gray-500 dark:text-gray-400">{{ row.currency }}</span>
          </div>
          <PencilSquareIcon v-if="row.newAmount.trim()" class="size-5 shrink-0 text-indigo-600 dark:text-indigo-400" title="Edited — not yet saved" />
          <CheckCircleIcon v-else-if="statusMap.get(row.key)?.state === 'saved'" class="size-5 shrink-0 text-green-600 dark:text-green-400" title="Saved" />
          <ExclamationCircleIcon v-else-if="statusMap.get(row.key)?.state === 'failed'" class="size-5 shrink-0 text-red-600 dark:text-red-400" :title="statusMap.get(row.key)?.msg" />
        </div>

        <div class="space-y-2 border-t border-gray-100 px-3 py-2 dark:border-white/5">
          <div class="flex items-center justify-between text-sm">
            <span class="text-gray-500 dark:text-gray-400">Budget</span>
            <span v-if="row.current?.ended" class="text-gray-400 italic dark:text-gray-500">Ended {{ row.current.date }}</span>
            <span v-else-if="row.current" class="text-gray-900 dark:text-white">{{ row.current.amount }} · {{ row.current.interval }}</span>
            <span v-else class="text-gray-400 dark:text-gray-500">—</span>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400">New budget</label>
            <div class="mt-1 flex gap-2">
              <input v-model="row.newAmount" @input="clearStatus(row.key)" placeholder="—" :class="inputClass + ' flex-1 text-right'" />
              <div class="flex-1">
                <IntervalDropdown v-model="row.newInterval" />
              </div>
            </div>
            <input v-model="row.newDate" type="date" :class="inputClass + ' mt-2 w-full'" />
          </div>
        </div>

        <div v-if="expandedKey === row.key" class="border-t border-gray-100 px-3 py-2 dark:border-white/5">
          <BudgetHistoryPanel :account="row.account" :currency="row.currency" :directives="row.directives" @changed="refresh" />
        </div>
      </div>

      <div v-if="!isLoading && groupRows.length === 0" class="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
        No budgets yet. Add one above.
      </div>
    </div>

    <!-- Action buttons below the table (mirrors TransactionsView). -->
    <div class="mt-6 flex items-center justify-between px-4">
      <button
        type="button"
        :disabled="!anyDirty || isSaving"
        @click="discardChanges"
        class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
      >
        Reset
      </button>
      <button
        type="button"
        :disabled="!anyDirty || isSaving"
        @click="saveChanges"
        class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:hover:bg-indigo-400"
      >
        {{ isSaving ? 'Saving…' : `Save Changes (${modifiedCount})` }}
      </button>
    </div>

    <ConfirmDialog
      :is-open="unsavedConfirm.isOpen.value"
      :title="unsavedConfirm.dialogOptions.value.title"
      :message="unsavedConfirm.dialogOptions.value.message"
      :confirm-text="unsavedConfirm.dialogOptions.value.confirmText"
      :cancel-text="unsavedConfirm.dialogOptions.value.cancelText"
      :variant="unsavedConfirm.dialogOptions.value.variant"
      @confirm="unsavedConfirm.handleConfirm"
      @cancel="unsavedConfirm.handleCancel"
      @close="unsavedConfirm.handleClose"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, onBeforeRouteLeave } from 'vue-router'
import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
import { CheckIcon, ChevronRightIcon, XMarkIcon } from '@heroicons/vue/20/solid'
import { PencilSquareIcon, CheckCircleIcon, ExclamationCircleIcon } from '@heroicons/vue/24/solid'
import { useBudgets } from '@/composables/useBudgets'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { todayLocal } from '@/utils/date'
import { errorHandler } from '@/utils/ErrorHandler'
import type { BudgetItem } from '@/services/generated-api'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import IntervalDropdown from '@/components/common/IntervalDropdown.vue'
import BudgetHistoryPanel from '@/components/budgets/BudgetHistoryPanel.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

// Match the dropdown height (py-1.5 + text-base sm:text-sm) so every field in a row aligns.
const inputClass =
  'block rounded-md bg-white px-2 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10'

const { isSaving, isLoading, fetch: fetchBudgets, create } = useBudgets()
const { isMd } = useBreakpoint()
const route = useRoute()

type GroupRow = {
  key: string
  account: string
  currency: string
  current: BudgetItem | null // effective as-of (may be a tombstone → ended)
  directives: BudgetItem[] // full history for this (account, currency)
  newAmount: string
  newInterval: string
  newDate: string
}
const groupRows = ref<GroupRow[]>([])
const statusMap = ref<Map<string, { state: 'saved' | 'failed'; msg: string }>>(new Map())
const expandedKey = ref<string | null>(null)

const filter = ref('')
const asOf = ref(todayLocal())
const currencyFilter = ref('')

const newRow = ref({ account: '', currency: 'USD', interval: 'monthly', amount: '', date: todayLocal() })

const key = (account: string, currency: string) => `${account} ${currency}`

async function refresh() {
  try {
    const [history, effective] = await Promise.all([
      fetchBudgets({ history: true }),
      fetchBudgets({ history: false, asOf: asOf.value }),
    ])
    const effByKey = new Map(effective.map((e) => [key(e.account, e.currency), e]))
    const byKey = new Map<string, BudgetItem[]>()
    for (const d of history) {
      const k = key(d.account, d.currency)
      const list = byKey.get(k)
      if (list) list.push(d)
      else byKey.set(k, [d])
    }
    groupRows.value = [...byKey.entries()]
      .map(([k, directives]) => {
        const current = effByKey.get(k) ?? null
        return {
          key: k,
          account: directives[0].account,
          currency: directives[0].currency,
          current,
          directives,
          newAmount: '',
          newInterval: current && !current.ended ? current.interval : 'monthly',
          newDate: todayLocal(),
        }
      })
      .sort((a, b) => a.account.localeCompare(b.account) || a.currency.localeCompare(b.currency))
  } catch (err: unknown) {
    errorHandler.display(err)
  }
}

const currencies = computed(() => [...new Set(groupRows.value.map((r) => r.currency))].sort())

const filteredRows = computed(() => {
  const f = filter.value.trim().toLowerCase()
  return groupRows.value.filter(
    (r) =>
      (!f || r.account.toLowerCase().includes(f)) &&
      (!currencyFilter.value || r.currency === currencyFilter.value),
  )
})

const anyDirty = computed(() => groupRows.value.some((r) => r.newAmount.trim() !== ''))
const modifiedCount = computed(() => groupRows.value.filter((r) => r.newAmount.trim() !== '').length)
const canAdd = computed(
  () =>
    newRow.value.account.trim() !== '' &&
    newRow.value.currency.trim() !== '' &&
    newRow.value.amount.trim() !== '',
)

function toggleExpand(k: string) {
  expandedKey.value = expandedKey.value === k ? null : k
}

/** Discard all pending New-budget edits locally (no server round-trip). */
function discardChanges() {
  for (const row of groupRows.value) {
    row.newAmount = ''
    row.newInterval = row.current && !row.current.ended ? row.current.interval : 'monthly'
    row.newDate = todayLocal()
  }
  statusMap.value = new Map()
}

function clearStatus(k: string) {
  if (statusMap.value.has(k)) {
    const next = new Map(statusMap.value)
    next.delete(k)
    statusMap.value = next
  }
}

/** Commit every row with a New budget as a forward-dated superseding directive.
 * Best-effort per row: a failed row keeps its typed input so nothing is lost. */
async function saveChanges() {
  const dirty = groupRows.value.filter((r) => r.newAmount.trim() !== '')
  const next = new Map(statusMap.value)
  const failed = new Map<string, { amount: string; interval: string; date: string }>()
  for (const row of dirty) {
    const created = await create({
      date: row.newDate,
      account: row.account,
      interval: row.newInterval,
      amount: row.newAmount.trim(),
      currency: row.currency,
    })
    if (created) {
      next.set(row.key, { state: 'saved', msg: 'Saved' })
    } else {
      next.set(row.key, { state: 'failed', msg: 'Save failed' })
      failed.set(row.key, { amount: row.newAmount, interval: row.newInterval, date: row.newDate })
    }
  }
  statusMap.value = next
  await refresh()
  // Restore the input of any row that failed so a partial save never drops work.
  for (const row of groupRows.value) {
    const f = failed.get(row.key)
    if (f) {
      row.newAmount = f.amount
      row.newInterval = f.interval
      row.newDate = f.date
    }
  }
}

async function addRow() {
  if (!canAdd.value) return
  const created = await create({
    date: newRow.value.date,
    account: newRow.value.account.trim(),
    interval: newRow.value.interval,
    amount: newRow.value.amount.trim(),
    currency: newRow.value.currency.trim(),
  })
  if (created) {
    newRow.value = { account: '', currency: newRow.value.currency, interval: 'monthly', amount: '', date: todayLocal() }
    await refresh()
  }
}

// Re-fetch the effective set (the "Current" column) when the as-of date changes.
watch(asOf, refresh)

// Warn before leaving (or refreshing/closing) with staged, unsaved New budgets.
const unsavedConfirm = useConfirmDialog()
onBeforeRouteLeave(async () => {
  if (!anyDirty.value) return true
  return await unsavedConfirm.showConfirm({
    title: 'Unsaved Changes',
    message: `You have ${modifiedCount.value} unsaved budget change${modifiedCount.value > 1 ? 's' : ''} that will be lost if you leave this page.`,
    confirmText: 'Leave',
    cancelText: 'Stay',
    variant: 'warning',
  })
})

function beforeUnloadHandler(e: BeforeUnloadEvent) {
  if (anyDirty.value) e.preventDefault()
}

onMounted(() => {
  window.addEventListener('beforeunload', beforeUnloadHandler)
  const acct = route.query.account
  if (typeof acct === 'string') filter.value = acct
  refresh()
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnloadHandler))
</script>
