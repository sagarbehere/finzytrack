<template>
  <div class="flex items-center h-full">
    <div v-if="icon" class="flex-shrink-0 mr-4">
      <div
        class="w-12 h-12 rounded-full flex items-center justify-center"
        :class="iconBgClass"
      >
        <span class="text-white text-xl font-semibold">{{ icon }}</span>
      </div>
    </div>
    <div class="flex-1 min-w-0">
      <p class="text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
        {{ label }}
      </p>
      <p class="text-2xl font-bold text-gray-900 dark:text-white">
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

interface Props {
  value: number
  label?: string
  icon?: string
  iconColor?: 'blue' | 'green' | 'red' | 'purple' | 'amber'
  formatValue?: (value: number) => string
  showTrend?: boolean
  trend?: number | null
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  iconColor: 'blue',
  showTrend: false,
  trend: null,
})

const iconBgClass = computed(() => {
  const colors = {
    blue: 'bg-blue-500',
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

function formatTrend(value: number): string {
  return value.toLocaleString('en-US', {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })
}
</script>
