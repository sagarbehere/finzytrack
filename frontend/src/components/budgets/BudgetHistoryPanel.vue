<template>
  <div class="space-y-3">
    <!-- End budget: a distinct forward action — stop budgeting this (account,
         currency) going forward without deleting past directives (dev-docs/budget.md). -->
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <span class="text-gray-500 dark:text-gray-400">End this budget as of</span>
      <input v-model="endDate" type="date" :class="inputClass" />
      <button
        type="button"
        :disabled="isSaving"
        @click="endBudget"
        class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 dark:bg-white/10 dark:text-white dark:inset-ring-white/10"
      >
        End budget
      </button>
    </div>

    <!-- Manage history: the raw directives for this (account, currency), edited or
         deleted in place — the one place history is mutated. -->
    <p class="border-t border-gray-100 pt-3 text-sm font-semibold text-gray-900 dark:border-white/5 dark:text-white">Manage history</p>
    <!-- One directive per row. Flex-wrap (not a fixed table) so it stays usable in
         narrow containers — the account drawer and mobile cards — without the
         actions running off-screen. -->
    <div class="divide-y divide-gray-100 dark:divide-white/5">
      <div
        v-for="row in sortedRows"
        :key="row.id"
        class="flex flex-wrap items-center gap-x-3 gap-y-2 py-2"
        :class="dirty.has(row.id) ? 'bg-amber-50 dark:bg-amber-500/10' : ''"
      >
        <input v-model="row.date" type="date" @change="markDirty(row.id)" :class="inputClass" />
        <span
          v-if="row.ended"
          class="inline-flex items-center rounded bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600 dark:bg-white/10 dark:text-gray-300"
        >Ended</span>
        <div v-else class="w-36">
          <IntervalDropdown v-model="row.interval" @update:model-value="markDirty(row.id)" />
        </div>
        <div v-if="!row.ended" class="flex items-center gap-1.5">
          <input v-model="row.amount" @input="markDirty(row.id)" :class="inputClass + ' w-24 text-right'" />
          <span class="text-sm text-gray-500 dark:text-gray-400">{{ currency }}</span>
        </div>
        <div class="flex items-center gap-3">
          <button
            v-if="dirty.has(row.id)"
            type="button"
            :disabled="isSaving"
            @click="saveRow(row)"
            class="text-sm font-medium text-indigo-600 hover:text-indigo-500 disabled:opacity-50 dark:text-indigo-400"
          >
            Save
          </button>
          <button
            type="button"
            :disabled="isSaving"
            @click="deleteRow(row)"
            class="text-sm font-medium text-red-600 hover:text-red-500 disabled:opacity-50 dark:text-red-400"
          >
            Delete
          </button>
        </div>
      </div>
      <div v-if="sortedRows.length === 0" class="py-3 text-center text-sm text-gray-500 dark:text-gray-400">
        No budget history.
      </div>
    </div>

    <ConfirmDialog
      :is-open="confirmDialog.isOpen.value"
      :title="confirmDialog.dialogOptions.value.title"
      :message="confirmDialog.dialogOptions.value.message"
      :confirm-text="confirmDialog.dialogOptions.value.confirmText"
      :cancel-text="confirmDialog.dialogOptions.value.cancelText"
      :variant="confirmDialog.dialogOptions.value.variant"
      @confirm="confirmDialog.handleConfirm"
      @cancel="confirmDialog.handleCancel"
      @close="confirmDialog.handleClose"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useBudgets } from '@/composables/useBudgets'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { todayLocal } from '@/utils/date'
import type { BudgetItem } from '@/services/generated-api'
import IntervalDropdown from '@/components/common/IntervalDropdown.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

const props = defineProps<{ account: string; currency: string; directives: BudgetItem[] }>()
const emit = defineEmits<{ changed: [] }>()

// Match the height of AccountDropdown/CommodityDropdown/IntervalDropdown:
// py-1.5 + text-base sm:text-sm, so all fields in a row line up.
const inputClass =
  'rounded-md bg-white px-2 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10'

const { isSaving, create, update, remove } = useBudgets()
const confirmDialog = useConfirmDialog()

type Row = { id: string; date: string; interval: string; amount: string; ended: boolean }
const rows = ref<Row[]>([])
const dirty = ref<Set<string>>(new Set())
const endDate = ref(todayLocal())

// Rebuild the editable copy whenever the source directives change (after a write
// the parent reloads and passes fresh props).
watch(
  () => props.directives,
  (ds) => {
    rows.value = ds.map((d) => ({
      id: d.id,
      date: d.date,
      interval: d.interval,
      amount: d.amount,
      ended: d.ended ?? false,
    }))
    dirty.value = new Set()
  },
  { immediate: true },
)

const sortedRows = computed(() => [...rows.value].sort((a, b) => b.date.localeCompare(a.date)))

function markDirty(id: string) {
  dirty.value = new Set(dirty.value).add(id)
}

async function saveRow(row: Row) {
  const ok = await update(row.id, {
    date: row.date,
    account: props.account,
    interval: row.interval,
    amount: row.amount.trim(),
    currency: props.currency,
  })
  if (ok) emit('changed')
}

async function deleteRow(row: Row) {
  const ok = await confirmDialog.showConfirm({
    title: row.ended ? 'Remove budget end' : 'Delete budget directive',
    message: row.ended
      ? `Remove the budget end dated ${row.date}? The prior budget takes effect again.`
      : `Delete the ${row.interval} budget of ${row.amount} ${props.currency} dated ${row.date}?`,
    confirmText: 'Delete',
    cancelText: 'Cancel',
    variant: 'danger',
  })
  if (!ok) return
  if (await remove(row.id)) emit('changed')
}

async function endBudget() {
  const created = await create({
    date: endDate.value,
    account: props.account,
    interval: 'none', // tombstone — no budget from here (dev-docs/budget.md)
    currency: props.currency,
  })
  if (created) emit('changed')
}
</script>
