<template>
  <div class="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-base font-semibold text-gray-900 dark:text-white">Budgets</h1>
        <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Set a budget for any account, effective from a date. Stored as
          <code class="rounded bg-gray-100 px-1 dark:bg-white/10">custom "budget"</code> directives in your ledger.
        </p>
      </div>
      <button
        type="button"
        :disabled="!isDirty || isSaving"
        @click="saveChanges"
        class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:hover:bg-indigo-400"
      >
        {{ isSaving ? 'Saving…' : 'Save Changes' }}
      </button>
    </div>

    <!-- Filter -->
    <div class="mt-6">
      <input
        v-model="filter"
        type="text"
        placeholder="Filter by account…"
        class="block w-full max-w-xs rounded-md bg-white px-3 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10"
      />
    </div>

    <!-- Table -->
    <div class="mt-4 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <table class="min-w-full divide-y divide-gray-200 dark:divide-white/10">
        <thead>
          <tr class="text-left text-xs font-medium text-gray-500 dark:text-gray-400">
            <th class="px-4 py-2">Account</th>
            <th class="px-4 py-2">Currency</th>
            <th class="px-4 py-2">Interval</th>
            <th class="px-4 py-2 text-right">Amount</th>
            <th class="px-4 py-2">Effective</th>
            <th class="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 dark:divide-white/5">
          <!-- Quick-add row -->
          <tr class="bg-gray-50 dark:bg-white/5">
            <td class="px-4 py-2">
              <input v-model="newRow.account" placeholder="Expenses:Food" :class="inputClass" />
            </td>
            <td class="px-4 py-2">
              <input v-model="newRow.currency" placeholder="USD" :class="inputClass + ' w-20'" />
            </td>
            <td class="px-4 py-2">
              <select v-model="newRow.interval" :class="inputClass">
                <option v-for="i in INTERVALS" :key="i" :value="i">{{ i }}</option>
              </select>
            </td>
            <td class="px-4 py-2 text-right">
              <input v-model="newRow.amount" placeholder="0" :class="inputClass + ' text-right w-28'" />
            </td>
            <td class="px-4 py-2">
              <input v-model="newRow.date" type="date" :class="inputClass" />
            </td>
            <td class="px-4 py-2 text-right">
              <button
                type="button"
                :disabled="!canAdd || isSaving"
                @click="addRow"
                class="rounded-md bg-white px-2.5 py-1 text-sm font-semibold text-gray-900 inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 dark:bg-white/10 dark:text-white dark:inset-ring-white/10"
              >Add</button>
            </td>
          </tr>

          <!-- Existing budgets -->
          <tr v-for="row in filteredRows" :key="row.id" :class="dirtyIds.has(row.id) ? 'bg-amber-50 dark:bg-amber-500/10' : ''">
            <td class="px-4 py-2 text-sm text-gray-900 dark:text-white">{{ row.account }}</td>
            <td class="px-4 py-2 text-sm text-gray-500 dark:text-gray-400">{{ row.currency }}</td>
            <td class="px-4 py-2">
              <select v-model="row.interval" @change="markDirty(row.id)" :class="inputClass">
                <option v-for="i in INTERVALS" :key="i" :value="i">{{ i }}</option>
              </select>
            </td>
            <td class="px-4 py-2 text-right">
              <input v-model="row.amount" @input="markDirty(row.id)" :class="inputClass + ' text-right w-28'" />
            </td>
            <td class="px-4 py-2">
              <input v-model="row.date" type="date" @change="markDirty(row.id)" :class="inputClass" />
            </td>
            <td class="px-4 py-2 text-right">
              <button
                type="button"
                @click="deleteRow(row)"
                class="text-sm font-medium text-red-600 hover:text-red-500 dark:text-red-400"
              >Delete</button>
            </td>
          </tr>

          <tr v-if="!isLoading && rows.length === 0">
            <td colspan="6" class="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
              No budgets yet. Add one above.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useBudgets } from '@/composables/useBudgets'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { todayLocal } from '@/utils/date'
import type { BudgetItem } from '@/services/generated-api'

const INTERVALS = ['daily', 'weekly', 'monthly', 'quarterly', 'yearly']
const inputClass =
  'block rounded-md bg-white px-2 py-1 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10'

const { budgets, isLoading, isSaving, load, create, update, remove } = useBudgets()
const confirmDialog = useConfirmDialog()
const route = useRoute()

// Local editable working copy.
type Row = { id: string; account: string; currency: string; interval: string; amount: string; date: string }
const rows = ref<Row[]>([])
const dirtyIds = ref<Set<string>>(new Set())
const filter = ref('')

const newRow = ref({ account: '', currency: 'USD', interval: 'monthly', amount: '', date: todayLocal() })

function toRow(b: BudgetItem): Row {
  return { id: b.id, account: b.account, currency: b.currency, interval: b.interval, amount: b.amount, date: b.date }
}

async function refresh() {
  await load({ history: true })
  rows.value = budgets.value.map(toRow)
  dirtyIds.value = new Set()
}

const filteredRows = computed(() => {
  const f = filter.value.trim().toLowerCase()
  return f ? rows.value.filter((r) => r.account.toLowerCase().includes(f)) : rows.value
})

const isDirty = computed(() => dirtyIds.value.size > 0)
const canAdd = computed(() =>
  newRow.value.account.trim() !== '' && newRow.value.currency.trim() !== '' && newRow.value.amount.trim() !== '',
)

function markDirty(id: string) {
  dirtyIds.value = new Set(dirtyIds.value).add(id)
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

async function deleteRow(row: Row) {
  const ok = await confirmDialog.showConfirm({
    title: 'Delete budget',
    message: `Delete the budget for ${row.account} (${row.amount} ${row.currency})?`,
    confirmText: 'Delete',
    cancelText: 'Cancel',
    variant: 'danger',
  })
  if (!ok) return
  if (await remove(row.id)) await refresh()
}

/** Commit all dirty rows in a batch (best-effort per row). */
async function saveChanges() {
  const dirty = rows.value.filter((r) => dirtyIds.value.has(r.id))
  for (const r of dirty) {
    await update(r.id, {
      date: r.date,
      account: r.account,
      interval: r.interval,
      amount: r.amount.trim(),
      currency: r.currency,
    })
  }
  await refresh()
}

onMounted(() => {
  // Pre-filter when arriving from an account drawer's "Manage" link.
  const acct = route.query.account
  if (typeof acct === 'string') filter.value = acct
  refresh()
})
</script>
