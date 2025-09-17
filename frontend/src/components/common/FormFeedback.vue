<template>
  <div 
    :class="feedbackClasses"
    class="rounded-lg p-4 border"
  >
    <div class="flex items-start">
      <component 
        :is="iconComponent" 
        :class="iconClasses"
        class="h-5 w-5 mr-3 mt-0.5 flex-shrink-0" 
      />
      <div class="flex-1">
        <p :class="messageClasses" class="text-sm font-medium">
          {{ message }}
        </p>
        <p 
          v-if="details" 
          :class="detailsClasses"
          class="mt-1 text-xs"
        >
          {{ details }}
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  CheckCircleIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  XCircleIcon
} from '@heroicons/vue/24/outline'

type Severity = 'success' | 'error' | 'warning' | 'info';

interface Props {
  message: string;
  severity?: Severity;
  details?: string | null;
}

const props = withDefaults(defineProps<Props>(), {
  severity: 'info',
  details: null
})

const severityConfig: Record<Severity, any> = {
  success: {
    icon: CheckCircleIcon,
    containerClasses: 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800',
    iconClasses: 'text-green-500 dark:text-green-400',
    messageClasses: 'text-green-800 dark:text-green-300',
    detailsClasses: 'text-green-700 dark:text-green-400'
  },
  error: {
    icon: XCircleIcon,
    containerClasses: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800',
    iconClasses: 'text-red-500 dark:text-red-400',
    messageClasses: 'text-red-800 dark:text-red-300',
    detailsClasses: 'text-red-700 dark:text-red-400'
  },
  warning: {
    icon: ExclamationTriangleIcon,
    containerClasses: 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800',
    iconClasses: 'text-yellow-500 dark:text-yellow-400',
    messageClasses: 'text-yellow-800 dark:text-yellow-300',
    detailsClasses: 'text-yellow-700 dark:text-yellow-400'
  },
  info: {
    icon: InformationCircleIcon,
    containerClasses: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800',
    iconClasses: 'text-blue-500 dark:text-blue-400',
    messageClasses: 'text-blue-800 dark:text-blue-300',
    detailsClasses: 'text-blue-700 dark:text-blue-400'
  }
}

const config = computed(() => severityConfig[props.severity])

const feedbackClasses = computed(() => config.value.containerClasses)
const iconComponent = computed(() => config.value.icon)
const iconClasses = computed(() => config.value.iconClasses)
const messageClasses = computed(() => config.value.messageClasses)
const detailsClasses = computed(() => config.value.detailsClasses)
</script>