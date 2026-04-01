<template>
  <div ref="kpiRoot" class="flex items-center h-full overflow-hidden">
    <div v-if="icon" class="flex-shrink-0 mr-4">
      <div
        class="w-12 h-12 rounded-full flex items-center justify-center"
        :class="iconBgClass"
      >
        <span class="text-white text-xl font-semibold">{{ icon }}</span>
      </div>
    </div>
    <div class="flex-1 min-w-0 overflow-hidden">
      <p v-if="label" class="text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
        {{ label }}
      </p>
      <!-- Multi-currency stacked values -->
      <template v-if="values && values.length > 0">
        <p
          v-for="(item, index) in values"
          :key="index"
          class="font-bold text-gray-900 dark:text-white whitespace-nowrap"
          :style="valueStyle(values.length)"
        >
          {{ formatCurrencyAmount(item) }}
        </p>
      </template>
      <!-- Single value (backward compatible) -->
      <p
        v-else
        class="font-bold text-gray-900 dark:text-white whitespace-nowrap"
        :style="valueStyle(1)"
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
import { computed, ref, onMounted, onUnmounted } from 'vue'
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

// Measure the root KPI element (the full card content area) rather than
// the inner text container, since the root has a definite size from the
// grid layout while the inner flex child may not resize as expected.
const kpiRoot = ref<HTMLElement | null>(null)
const rootWidth = ref(400)

let observer: ResizeObserver | null = null

onMounted(() => {
  if (kpiRoot.value) {
    rootWidth.value = kpiRoot.value.clientWidth
    observer = new ResizeObserver((entries) => {
      rootWidth.value = entries[0].contentRect.width
    })
    observer.observe(kpiRoot.value)
  }
})

onUnmounted(() => observer?.disconnect())

// Max font sizes per currency count: 1 → 24px, 2 → 20px, 3+ → 18px
const maxSizes: Record<number, number> = { 1: 24, 2: 20, 3: 18 }

function valueStyle(count: number): Record<string, string> {
  const maxPx = maxSizes[Math.min(count, 3)]
  // Subtract icon width (~64px) to get effective text area width.
  // Scale: at 150px text area → 14px, at 300px+ → max size.
  const textWidth = rootWidth.value - (props.icon ? 64 : 0)
  const size = Math.min(maxPx, Math.max(14, 14 + ((textWidth - 150) / 150) * (maxPx - 14)))
  return { fontSize: `${size}px` }
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
