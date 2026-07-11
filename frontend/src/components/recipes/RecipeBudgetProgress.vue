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
              <span class="text-sm font-semibold" :class="r.textClass" :style="r.textStyle">{{ r.statusText }}</span>
            </span>
          </div>

          <!-- Two-tone bar: spent solid, remaining a faint tint of the same hue.
               Full width; clamped at 100% (over-budget shows red). -->
          <div
            class="relative mt-1.5 h-2.5 w-full overflow-hidden rounded-full"
            :class="r.trackClass"
            :style="r.trackStyle"
          >
            <div
              class="absolute inset-y-0 left-0 rounded-full transition-[width]"
              :class="r.fillClass"
              :style="{ width: r.fillPct + '%', ...r.fillStyle }"
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

/** Per-status colour overrides (any CSS/hex colour); omitted → default palette. */
export interface BudgetProgressColors {
  under?: string
  approaching?: string
  exact?: string
  over?: string
}

interface Props {
  rows: Record<string, unknown>[]
  fields: BudgetProgressFields
  /** Optional label formatter (e.g. accountName2), resolved by the renderer. */
  accountFormat?: (value: unknown) => string
  /** Optional per-row click-through, resolved by the renderer from the JSON link. */
  getRowLink?: (row: Record<string, unknown>) => RouteLocationRaw | null
  emptyText?: string
  /** Fraction of budget where the bar turns amber ("approaching"). Default 0.85. */
  warnAt?: number
  /** Per-status colour overrides; omitted statuses keep the default palette. */
  colors?: BudgetProgressColors
}

const props = defineProps<Props>()

function num(v: unknown): number {
  if (typeof v === 'number') return v
  if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) return Number(v)
  return 0
}

const warnAt = computed(() =>
  typeof props.warnAt === 'number' && props.warnAt > 0 && props.warnAt <= 1 ? props.warnAt : 0.85,
)

type Status = 'good' | 'warn' | 'exact' | 'bad'

// Default palette (Tailwind classes). Fills softened uniformly so they read calm
// on both the white light-mode card and the dark card; text stays saturated for
// legibility. Custom `colors` override these via inline style (below).
const DEFAULT_CLASSES: Record<Status, { fill: string; track: string; text: string }> = {
  good: { fill: 'bg-emerald-500/85 dark:bg-emerald-400/70', track: 'bg-emerald-500/10 dark:bg-emerald-400/10', text: 'text-emerald-600/90 dark:text-emerald-400/90' },
  warn: { fill: 'bg-amber-500/85 dark:bg-amber-400/70', track: 'bg-amber-500/12 dark:bg-amber-400/12', text: 'text-amber-600 dark:text-amber-400' },
  exact: { fill: 'bg-blue-500/85 dark:bg-blue-400/70', track: 'bg-blue-500/12 dark:bg-blue-400/12', text: 'text-blue-600 dark:text-blue-400' },
  bad: { fill: 'bg-red-500/85 dark:bg-red-400/70', track: 'bg-red-500/12 dark:bg-red-400/12', text: 'text-red-600 dark:text-red-400' },
}
const CONFIG_KEY: Record<Status, keyof BudgetProgressColors> = {
  good: 'under', warn: 'approaching', exact: 'exact', bad: 'over',
}

/** Status by usage & direction: expenses good when under, income good when over.
 * Expenses: green (room) → amber (approaching) → blue (exactly on budget) → red
 * (strictly over). Red only when EXCEEDED, so a fixed expense paid in full at
 * exactly 100% is "on the mark" (blue), not an alarm. */
function statusOf(pctUsed: number, overGood: boolean): Status {
  if (overGood) {
    if (pctUsed >= 1) return 'good'
    if (pctUsed >= warnAt.value) return 'warn'
    return 'bad'
  }
  if (pctUsed > 1) return 'bad'
  if (pctUsed === 1) return 'exact'
  if (pctUsed >= warnAt.value) return 'warn'
  return 'good'
}

function hexToRgba(color: string, alpha: number): string {
  const h = color.trim().replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  if ([r, g, b].some((n) => Number.isNaN(n))) return color // non-hex (e.g. "rebeccapurple") → use as-is
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** Class-based default, or inline-style override when a custom colour is set. */
function styleFor(status: Status) {
  const hex = props.colors?.[CONFIG_KEY[status]]
  if (hex) {
    return {
      fillClass: undefined as string | undefined,
      trackClass: undefined as string | undefined,
      textClass: undefined as string | undefined,
      fillStyle: { backgroundColor: hexToRgba(hex, 0.85) } as Record<string, string> | undefined,
      trackStyle: { backgroundColor: hexToRgba(hex, 0.12) } as Record<string, string> | undefined,
      textStyle: { color: hex } as Record<string, string> | undefined,
    }
  }
  const d = DEFAULT_CLASSES[status]
  return {
    fillClass: d.fill,
    trackClass: d.track,
    textClass: d.text,
    fillStyle: undefined as Record<string, string> | undefined,
    trackStyle: undefined as Record<string, string> | undefined,
    textStyle: undefined as Record<string, string> | undefined,
  }
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

    const st = styleFor(statusOf(pctUsed, overGood))
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
      ...st,
      fillPct: Math.min(100, Math.max(0, pctUsed * 100)),
      link: props.getRowLink ? props.getRowLink(row) : null,
    }
  }),
)
</script>
