<template>
  <div class="space-y-2">
    <div
      v-for="y in fund.years"
      :key="y.year"
      class="rounded-md bg-white ring-1 ring-gray-200 dark:bg-white/5 dark:ring-white/10"
    >
      <button
        type="button"
        class="flex w-full items-center justify-between gap-4 px-3 py-2 text-left"
        :aria-expanded="open.has(y.year)"
        @click="toggle(y.year)"
      >
        <span class="flex items-center gap-2">
          <ChevronRightIcon :class="['size-4 text-gray-400 transition-transform', open.has(y.year) ? 'rotate-90' : '']" aria-hidden="true" />
          <span class="text-sm font-semibold text-gray-900 dark:text-white">{{ y.year }}</span>
          <span class="text-sm text-gray-500 dark:text-gray-400">{{ yearCountsText(y) }}</span>
        </span>
        <span class="flex items-center gap-2 text-sm">
          <span class="text-gray-500 dark:text-gray-400">Year end {{ y.end_units }} units</span>
          <span v-if="y.ok === true" class="inline-flex items-center gap-1 text-green-700 dark:text-green-400">
            <CheckCircleIcon class="size-4" aria-hidden="true" /> matches statement
          </span>
          <span v-else-if="y.ok === false" class="inline-flex items-center gap-1 text-red-700 dark:text-red-400">
            <ExclamationTriangleIcon class="size-4" aria-hidden="true" /> statement says {{ y.statement_units }}
          </span>
        </span>
      </button>

      <ul v-if="open.has(y.year)" class="divide-y divide-gray-200 border-t border-gray-200 dark:divide-white/10 dark:border-white/10">
        <li
          v-for="r in rowsOf(y.year)"
          :id="`row-${r.id}`"
          :key="r.id"
          :class="['px-3 py-2', r.interpretation === 'tax' ? 'pl-8' : '', r.id === focusRow ? 'bg-yellow-50 dark:bg-yellow-500/10' : '']"
        >
          <div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
            <div class="min-w-0 flex-1">
              <p class="text-sm text-gray-900 dark:text-white">
                <span class="font-medium">{{ r.date }}</span>
                <span class="ml-2 text-gray-500 dark:text-gray-400" :title="r.description">{{ r.description }}</span>
              </p>
              <p class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                <span v-if="r.amount">Amount {{ r.amount }}</span>
                <span v-if="r.units" class="ml-3">Units {{ r.units }}</span>
                <span v-if="r.balance" class="ml-3">Balance {{ r.balance }}</span>
                <span v-if="r.pair_fund" class="ml-3">Paired with {{ r.pair_fund }}</span>
              </p>
              <p v-if="r.reason" class="mt-0.5 text-xs text-indigo-700 dark:text-indigo-400">{{ r.reason }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <span class="text-xs text-gray-600 dark:text-gray-300">{{ INTERPRETATION_LABELS[r.interpretation] }}</span>
              <span :class="[badgeBase, badge[statusTone(r.status)]]">{{ STATUS_LABELS[r.status] }}</span>
              <span v-if="r.corrected" :class="[badgeBase, badge.indigo]">Corrected</span>
              <button
                v-if="r.interpretation !== 'tax'"
                type="button"
                class="text-sm font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
                @click="startEdit(r)"
              >
                Fix
              </button>
              <button
                v-if="r.transaction != null"
                type="button"
                class="text-sm font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
                @click="toggleEntry(r.id)"
              >
                {{ entryOpen.has(r.id) ? 'Hide entry' : 'Entry' }}
              </button>
            </div>
          </div>

          <!-- The ledger entry this row becomes -->
          <pre
            v-if="entryOpen.has(r.id) && r.transaction != null && transactions[r.transaction]"
            class="mt-2 overflow-x-auto rounded-md bg-gray-50 p-2 text-xs text-gray-700 dark:bg-white/5 dark:text-gray-300"
          >{{ entryText(r.transaction) }}</pre>

          <!-- Corrections for this row -->
          <div v-if="editing === r.id" class="mt-3 rounded-md bg-gray-50 p-3 dark:bg-white/5">
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Read as</label>
                <div class="mt-2">
                  <SelectListbox
                    v-model="draft.interpretation"
                    :options="[...CHOOSABLE_INTERPRETATIONS]"
                    :label-of="interpLabel"
                  />
                </div>
              </div>
              <div>
                <label :for="`d-${r.id}`" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Date</label>
                <input :id="`d-${r.id}`" v-model="draft.date" type="date" :class="inputClass" />
              </div>
              <div>
                <label :for="`u-${r.id}`" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Units</label>
                <input :id="`u-${r.id}`" v-model="draft.units" type="text" inputmode="decimal" :class="inputClass" />
              </div>
              <div>
                <label :for="`a-${r.id}`" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Amount</label>
                <input :id="`a-${r.id}`" v-model="draft.amount" type="text" inputmode="decimal" :class="inputClass" />
              </div>
              <div v-if="candidates.length || r.pair_id" class="sm:col-span-2 lg:col-span-4">
                <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Other half of a switch or merger</label>
                <div class="mt-2">
                  <SelectListbox
                    v-model="draft.pair"
                    :options="pairOptions"
                    :label-of="pairLabel"
                  />
                </div>
              </div>
            </div>
            <div class="mt-3 flex flex-wrap justify-end gap-2">
              <button v-if="r.corrected" type="button" :class="secondary" @click="emit('undo', r.id); editing = null">
                Undo my changes
              </button>
              <button type="button" :class="secondary" @click="exclude(r)">
                {{ r.status === 'excluded' ? 'Include again' : 'Exclude this row' }}
              </button>
              <button type="button" :class="secondary" @click="editing = null">Cancel</button>
              <button type="button" :class="primary" @click="save(r)">Apply</button>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed, reactive, ref, watch } from 'vue'
  import { CheckCircleIcon, ChevronRightIcon, ExclamationTriangleIcon } from '@heroicons/vue/20/solid'
  import SelectListbox from '@/components/common/SelectListbox.vue'
  import type { FundPlan, PlannedRow, PlannedTransaction, RowCorrection } from '@/services/generated-api'
  import {
    CHOOSABLE_INTERPRETATIONS,
    INTERPRETATION_LABELS,
    STATUS_LABELS,
    pairCandidates,
    postingLine,
    statusTone,
    yearCountsText,
  } from '@/utils/statementImport'

  const props = defineProps<{
    fund: FundPlan
    allFunds: FundPlan[]
    transactions: PlannedTransaction[]
    /** A row to show: its year opens and the row is highlighted. */
    focusRow?: string | null
  }>()
  const emit = defineEmits<{
    correct: [change: RowCorrection]
    undo: [rowId: string]
  }>()

  const inputClass =
    'mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500'
  const primary =
    'rounded-md bg-indigo-600 px-2.5 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500'
  const secondary =
    'rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20'
  const badgeBase = 'inline-flex items-center rounded-md px-2 py-1 text-xs font-medium'
  const badge: Record<string, string> = {
    gray: 'bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400',
    red: 'bg-red-100 text-red-700 dark:bg-red-400/10 dark:text-red-400',
    yellow: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-400/10 dark:text-yellow-500',
    green: 'bg-green-100 text-green-700 dark:bg-green-400/10 dark:text-green-400',
    blue: 'bg-blue-100 text-blue-700 dark:bg-blue-400/10 dark:text-blue-400',
    indigo: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-400/10 dark:text-indigo-400',
  }

  // Years start collapsed, except one whose year-end check fails (§9a.1: pin the year a gap starts).
  const open = ref(new Set<number>(props.fund.years.filter(y => y.ok === false).slice(0, 1).map(y => y.year)))
  const entryOpen = ref(new Set<string>())
  watch(() => props.focusRow, id => {
    const r = id ? props.fund.rows.find(x => x.id === id) : undefined
    if (r) open.value = new Set([...open.value, Number(r.date.slice(0, 4))])
  }, { immediate: true })
  function toggle(year: number) {
    const s = new Set(open.value)
    if (s.has(year)) s.delete(year)
    else s.add(year)
    open.value = s
  }
  function toggleEntry(id: string) {
    const s = new Set(entryOpen.value)
    if (s.has(id)) s.delete(id)
    else s.add(id)
    entryOpen.value = s
  }

  const interpLabel = (o: string) => INTERPRETATION_LABELS[o] ?? o
  const rowsOf = (year: number) => props.fund.rows.filter(r => r.date.startsWith(String(year)))

  function entryText(index: number): string {
    const t = props.transactions[index]
    return [`${t.date} ${t.narration}`, ...t.postings.map(p => `  ${postingLine(p)}`)].join('\n')
  }

  // ── Corrections ──
  const NO_PAIR = '__none__'
  const editing = ref<string | null>(null)
  const draft = reactive({ interpretation: '', date: '', units: '', amount: '', pair: '' })
  const editingRow = computed(() => props.fund.rows.find(r => r.id === editing.value) ?? null)

  const institutionOf = (rowId: string) =>
    props.allFunds.find(f => f.rows.some(r => r.id === rowId))?.institution
  const candidates = computed(() =>
    editingRow.value ? pairCandidates(props.allFunds, editingRow.value, institutionOf) : [],
  )
  const pairOptions = computed(() => [NO_PAIR, ...candidates.value.map(c => c.row.id)])
  function pairLabel(id: string): string {
    if (id === NO_PAIR) return 'Not paired'
    const c = candidates.value.find(x => x.row.id === id)
    return c ? `${c.fund.name} · ${c.row.date} · ${c.row.amount ?? ''} (${c.row.units} units)` : id
  }

  function startEdit(r: PlannedRow) {
    editing.value = r.id
    draft.interpretation = r.interpretation
    draft.date = r.date
    draft.units = r.units ?? ''
    draft.amount = r.amount ?? ''
    draft.pair = r.pair_id ?? NO_PAIR
  }

  function save(r: PlannedRow) {
    const change: RowCorrection = { row_id: r.id }
    if (draft.interpretation && draft.interpretation !== r.interpretation) {
      change.interpretation = draft.interpretation as RowCorrection['interpretation']
    }
    if (draft.date && draft.date !== r.date) change.date = draft.date
    if (draft.units && draft.units !== (r.units ?? '')) change.units = draft.units
    if (draft.amount && draft.amount !== (r.amount ?? '')) change.amount = draft.amount
    const currentPair = r.pair_id ?? NO_PAIR
    if (draft.pair !== currentPair) change.pair_with = draft.pair === NO_PAIR ? '' : draft.pair
    if (Object.keys(change).length > 1) emit('correct', change)
    editing.value = null
  }

  function exclude(r: PlannedRow) {
    if (r.status === 'excluded') emit('undo', r.id)
    else emit('correct', { row_id: r.id, exclude: true })
    editing.value = null
  }
</script>
