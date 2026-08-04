<template>
  <div class="flex items-center h-full overflow-hidden">
    <div v-if="icon" class="flex-shrink-0 mr-4">
      <div
        class="w-12 h-12 rounded-full flex items-center justify-center"
        :style="{ backgroundColor: iconColorHex }"
      >
        <span class="text-white text-xl font-semibold">{{ icon }}</span>
      </div>
    </div>
    <!-- Content column: scales font to fit (see maxFontSize), but if content is
         still taller than the card at the minimum readable size, scroll instead of
         clipping. Vertical scroll only; block flow scrolls from the top so nothing
         is stranded above the centered fold. -->
    <div
      class="flex-1 min-w-0 max-h-full overflow-x-hidden overflow-y-auto"
      style="container-type: inline-size"
    >
      <p v-if="label" class="text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
        {{ label }}
      </p>
      <!-- Multi-currency stacked values -->
      <template v-if="values && values.length > 0">
        <p
          v-for="(item, index) in values"
          :key="index"
          class="kpi-value font-bold whitespace-nowrap"
          :class="colorBySign ? '' : 'text-gray-900 dark:text-white'"
          :style="colorBySign
            ? { color: signColor(item.amount), '--kpi-max-fs': maxFontSize(contentLines) }
            : { '--kpi-max-fs': maxFontSize(contentLines) }"
        >
          {{ formatCurrencyAmount(item) }}
        </p>
      </template>
      <!-- Single value (backward compatible) -->
      <p
        v-else
        class="kpi-value font-bold whitespace-nowrap"
        :class="colorBySign ? '' : 'text-gray-900 dark:text-white'"
        :style="colorBySign
          ? { color: signColor(value), '--kpi-max-fs': maxFontSize(contentLines) }
          : { '--kpi-max-fs': maxFontSize(contentLines) }"
      >
        {{ formattedValue }}
      </p>
      <!-- Optional secondary sub-line (e.g. a YTD figure under an all-time total) -->
      <p
        v-if="secondaryValues && secondaryValues.length > 0"
        class="mt-0.5 text-xs text-gray-500 dark:text-gray-400 truncate"
      >
        <span v-if="secondaryLabel" class="font-medium">{{ secondaryLabel }}</span>
        {{ secondaryValues.map(formatCurrencyAmount).join(' · ') }}
      </p>
      <div v-if="showTrend && trend !== null" class="flex items-center mt-1">
        <span
          class="text-sm font-medium"
          :class="trend >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'"
        >
          {{ trend >= 0 ? '+' : '' }}{{ formatTrend(trend) }}
        </span>
        <span class="ml-1 text-xs text-gray-500 dark:text-gray-400">vs prior</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { CurrencyAmount } from '@/types/recipes'
import { formatAmount } from '@/utils/currencyFormat'
import { useDashboardTheme } from '@/composables/useDashboardTheme'
import { useTheme } from '@/composables/useTheme'
import type { BudgetStatus } from '@/utils/budgetStatus'

interface Props {
  value: number
  label?: string
  icon?: string
  /** A `{{theme.*}}` token, a hex, or a legacy named color (blue/green/red/purple/amber). */
  iconColor?: string
  formatValue?: (value: number) => string
  showTrend?: boolean
  trend?: number | null
  values?: CurrencyAmount[]
  /** Colour the value + icon by sign (green ≥ 0, red < 0). */
  colorBySign?: boolean
  /** Optional muted sub-line values (e.g. a YTD figure under an all-time total). */
  secondaryValues?: CurrencyAmount[]
  secondaryLabel?: string
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  iconColor: 'blue',
  showTrend: false,
  trend: null,
  colorBySign: false,
})

const { valenceColor, resolveThemeColor } = useDashboardTheme()
const { isDarkMode } = useTheme()

// Three-way by sign, from the theme's valence band: bad < 0, complete (on the
// mark) at 0, good > 0 — mirrors the budget-progress bars.
function signStatus(n: number): BudgetStatus {
  return n < 0 ? 'bad' : n === 0 ? 'exact' : 'good'
}
function signColor(n: number): string {
  return valenceColor(signStatus(n), isDarkMode.value)
}

// Legacy named icon colors → theme roles (orphan 'purple'/'blue' → brand; the
// collision-fix default is "accent = brand unless the number is signed").
const NAMED_ICON: Record<string, string> = {
  blue: '{{theme.brand}}',
  purple: '{{theme.brand}}',
  green: '{{theme.valence.good}}',
  red: '{{theme.valence.bad}}',
  amber: '{{theme.valence.warn}}',
}

const shownAmounts = computed<number[]>(() =>
  props.values && props.values.length > 0 ? props.values.map((v) => v.amount) : [props.value],
)
const isNegative = computed(() => shownAmounts.value.some((n) => n < 0))
const isExactlyZero = computed(() => !isNegative.value && shownAmounts.value.every((n) => n === 0))

// Total lines of content the card must fit: one per currency value, plus the
// optional secondary sub-line and trend line (and a label, if present). A
// multi-currency KPI with a YTD sub-line is several lines and must shrink to fit
// the fixed-height card instead of clipping.
const contentLines = computed(() => {
  const primary = props.values && props.values.length > 0 ? props.values.length : 1
  const secondary = props.secondaryValues && props.secondaryValues.length > 0 ? 1 : 0
  const trend = props.showTrend && props.trend !== null ? 1 : 0
  const label = props.label ? 1 : 0
  return primary + secondary + trend + label
})

// Ceiling font size by total content lines — more lines → smaller cap, so the
// content fits the card. The CSS clamp still scales down further for narrow cards
// (12cqw) but never below a readable floor (~13px); past that, the content column
// scrolls rather than shrinking into illegibility.
const maxSizes: Record<number, number> = { 1: 26, 2: 22, 3: 18, 4: 16, 5: 15, 6: 14 }

function maxFontSize(count: number): string {
  return `${maxSizes[Math.min(Math.max(count, 1), 6)]}px`
}

const iconColorHex = computed<string>(() => {
  if (props.colorBySign) {
    if (isNegative.value) return valenceColor('bad', isDarkMode.value)
    if (isExactlyZero.value) return valenceColor('exact', isDarkMode.value)
    return valenceColor('good', isDarkMode.value)
  }
  // A token or hex resolves directly; a legacy named color maps to a theme role.
  const ic = props.iconColor
  return resolveThemeColor(NAMED_ICON[ic] ?? ic, isDarkMode.value)
})

const formattedValue = computed(() => {
  if (props.formatValue) {
    return props.formatValue(props.value)
  }
  return String(props.value)
})

function formatCurrencyAmount(item: CurrencyAmount): string {
  return formatAmount(item.amount, item.currency)
}

function formatTrend(value: number): string {
  return value.toLocaleString('en-US', {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })
}
</script>

<style scoped>
/* Font scales with container width (cqw) via container query on the
   text wrapper (container-type: inline-size).  The --kpi-max-fs custom
   property is set per-element based on currency count. */
.kpi-value {
  /* Floor ~13px keeps the value readable; below this the content column scrolls
     (see template) rather than shrinking further. */
  font-size: clamp(0.8rem, 12cqw, var(--kpi-max-fs, 26px));
  line-height: 1.15;
}
</style>
