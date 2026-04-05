<template>
  <div class="flex items-center h-full overflow-hidden">
    <div v-if="icon" class="flex-shrink-0 mr-4">
      <div
        class="w-12 h-12 rounded-full flex items-center justify-center"
        :class="iconBgClass"
      >
        <span class="text-white text-xl font-semibold">{{ icon }}</span>
      </div>
    </div>
    <div
      class="flex-1 min-w-0 overflow-hidden"
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
          class="kpi-value font-bold text-gray-900 dark:text-white whitespace-nowrap"
          :style="{ '--kpi-max-fs': maxFontSize(values.length) }"
        >
          {{ formatCurrencyAmount(item) }}
        </p>
      </template>
      <!-- Single value (backward compatible) -->
      <p
        v-else
        class="kpi-value font-bold text-gray-900 dark:text-white whitespace-nowrap"
        :style="{ '--kpi-max-fs': maxFontSize(1) }"
      >
        {{ formattedValue }}
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

interface Props {
  value: number
  label?: string
  icon?: string
  iconColor?: 'blue' | 'green' | 'red' | 'purple' | 'amber'
  formatValue?: (value: number) => string
  showTrend?: boolean
  trend?: number | null
  values?: CurrencyAmount[]
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  iconColor: 'blue',
  showTrend: false,
  trend: null,
})

// Max font sizes per currency count: 1 → 26px, 2 → 22px, 3+ → 18px
const maxSizes: Record<number, number> = { 1: 26, 2: 22, 3: 18 }

function maxFontSize(count: number): string {
  return `${maxSizes[Math.min(count, 3)]}px`
}

const iconBgClass = computed(() => {
  const colors = {
    blue: 'bg-indigo-500',
    green: 'bg-green-500',
    red: 'bg-red-500',
    purple: 'bg-purple-500',
    amber: 'bg-amber-500',
  }
  return colors[props.iconColor]
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
  font-size: clamp(0.875rem, 12cqw, var(--kpi-max-fs, 26px));
}
</style>
