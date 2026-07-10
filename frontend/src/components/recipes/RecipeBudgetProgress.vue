<template>
  <div class="h-full overflow-auto pr-3">
    <ul v-if="viewRows.length > 0" role="list" class="divide-y divide-gray-100 dark:divide-white/5">
      <li v-for="(r, i) in viewRows" :key="i">
        <component
          :is="r.link ? 'RouterLink' : 'div'"
          :to="r.link ?? undefined"
          class="block px-1 py-2.5 rounded-md"
          :class="r.link ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-white/5' : ''"
        >
          <!-- Top line: account (left) · spent/budget (magnitude) + remaining
               (action) on the right. No % — the bar already carries the ratio. -->
          <div class="flex items-baseline justify-between gap-3">
            <span class="truncate text-[15px] font-medium text-gray-900 dark:text-white">{{ r.label }}</span>
            <span class="flex shrink-0 items-baseline gap-2.5 tabular-nums">
              <span class="text-sm text-gray-500 dark:text-gray-400">
                {{ r.actualText }} <span class="text-gray-300 dark:text-gray-600">/</span> {{ r.budgetText }}
              </span>
              <span class="text-sm font-semibold" :class="r.statusTextClass">{{ r.statusText }}</span>
            </span>
          </div>

          <!-- Two-tone bar: spent solid, remaining a faint tint of the same hue.
               Full width; clamped at 100% (over-budget shows red). -->
          <div class="relative mt-1.5 h-2.5 w-full overflow-hidden rounded-full" :class="r.trackClass">
            <div
              class="absolute inset-y-0 left-0 rounded-full transition-[width]"
              :class="r.fillClass"
              :style="{ width: r.fillPct + '%' }"
            />
          </div>
        </component>
      </li>
    </ul>
    <div v-if="viewRows.length === 0" class="flex h-full items-center justify-center p-4 text-center text-sm text-gray-500 dark:text-gray-400">
      {{ emptyText || 'No budgets for this period.' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import { RouterLink } from 'vue-router'
import { formatAmount } from '@/utils/currencyFormat'

export interface BudgetProgressFields {
  account: string
  budget: string
  actual: string
  remaining: string
  pctUsed: string
  currency: string
  direction: string
}

interface Props {
  rows: Record<string, unknown>[]
  fields: BudgetProgressFields
  /** Optional label formatter (e.g. accountName2), resolved by the renderer. */
  accountFormat?: (value: unknown) => string
  /** Optional per-row click-through, resolved by the renderer from the JSON link. */
  getRowLink?: (row: Record<string, unknown>) => RouteLocationRaw | null
  emptyText?: string
}

const props = defineProps<Props>()

function num(v: unknown): number {
  if (typeof v === 'number') return v
  if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) return Number(v)
  return 0
}

/** A budget line is "healthy" per its direction: expenses good when under, income good when over. */
function classifyColor(pctUsed: number, overGood: boolean): { fill: string; track: string; text: string } {
  // Traffic-light on how much of the target is used, flipped for income. `track`
  // is a faint tint of the same hue so the bar reads as used|remaining in one colour.
  // Fills softened uniformly (transparency) so they read calm on BOTH the white
  // light-mode card and the dark card — the hue carries the status; full
  // saturation looked neon on white. Text stays saturated for legibility.
  const good = { fill: 'bg-emerald-500/85 dark:bg-emerald-400/70', track: 'bg-emerald-500/10 dark:bg-emerald-400/10', text: 'text-emerald-600/90 dark:text-emerald-400/90' }
  const warn = { fill: 'bg-amber-500/85 dark:bg-amber-400/70', track: 'bg-amber-500/12 dark:bg-amber-400/12', text: 'text-amber-600 dark:text-amber-400' }
  // "On the mark" — spent exactly the budget (e.g. rent paid in full). Its own
  // calm colour, distinct from amber's "approaching" and red's "over".
  const exact = { fill: 'bg-blue-500/85 dark:bg-blue-400/70', track: 'bg-blue-500/12 dark:bg-blue-400/12', text: 'text-blue-600 dark:text-blue-400' }
  const bad = { fill: 'bg-red-500/85 dark:bg-red-400/70', track: 'bg-red-500/12 dark:bg-red-400/12', text: 'text-red-600 dark:text-red-400' }
  if (overGood) {
    if (pctUsed >= 1) return good // hit/beat the income target
    if (pctUsed >= 0.85) return warn
    return bad // well short of target
  }
  // green (room) → amber (approaching) → blue (exactly on budget) → red (over).
  // Red only when EXCEEDED (> 100%); exactly 100% is the ideal for a fixed
  // expense, so it gets its own "on the mark" colour, not an alarm.
  if (pctUsed > 1) return bad
  if (pctUsed === 1) return exact
  if (pctUsed >= 0.85) return warn
  return good
}

const viewRows = computed(() =>
  props.rows.map((row) => {
    const f = props.fields
    const budget = num(row[f.budget])
    const actual = num(row[f.actual])
    const remaining = f.remaining in row ? num(row[f.remaining]) : budget - actual
    const pctUsed = f.pctUsed in row ? num(row[f.pctUsed]) : budget > 0 ? actual / budget : 0
    const currency = String(row[f.currency] ?? '')
    const overGood = String(row[f.direction] ?? 'under-good') === 'over-good'
    const label = props.accountFormat ? props.accountFormat(row[f.account]) : String(row[f.account] ?? '')

    const color = classifyColor(pctUsed, overGood)
    // Over an expense budget → surface the overage; otherwise show what's left.
    const isOver = !overGood && remaining < 0
    const statusText = isOver
      ? `over ${formatAmount(Math.abs(remaining), currency)}`
      : `${formatAmount(remaining, currency)} left`
    return {
      label,
      actualText: formatAmount(actual, currency),
      budgetText: formatAmount(budget, currency),
      statusText,
      statusTextClass: color.text,
      fillClass: color.fill,
      trackClass: color.track,
      fillPct: Math.min(100, Math.max(0, pctUsed * 100)),
      link: props.getRowLink ? props.getRowLink(row) : null,
    }
  }),
)
</script>
