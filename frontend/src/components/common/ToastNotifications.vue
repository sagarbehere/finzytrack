<template>
  <div
    class="fixed top-4 right-4 left-4 sm:left-auto z-50 space-y-2 w-full sm:w-auto max-w-md sm:max-w-lg md:max-w-xl"
    style="min-width: 320px"
  >
    <TransitionGroup name="toast" tag="div" class="space-y-2">
      <div
        v-for="notification in visibleNotifications"
        :key="notification.id"
        :class="[
          'bg-white dark:bg-gray-800 shadow-lg rounded-lg pointer-events-auto ring-1 ring-black ring-opacity-5 overflow-hidden',
          getNotificationClasses(notification.type),
        ]"
      >
        <div class="p-4">
          <div class="flex items-start">
            <div class="flex-shrink-0">
              <component
                :is="getNotificationIcon(notification.type)"
                :class="['h-5 w-5', getIconClasses(notification.type)]"
              />
            </div>
            <div class="ml-3 flex-1 pt-0.5 min-w-0">
              <p class="text-sm font-medium text-gray-900 dark:text-white break-words">
                {{ notification.title }}
              </p>
              <p
                v-if="notification.message"
                class="mt-1 text-sm text-gray-500 dark:text-gray-400 break-words leading-relaxed"
              >
                {{ notification.message }}
              </p>
            </div>
            <div class="ml-4 flex-shrink-0 flex">
              <button
                @click="dismissNotification(notification.id)"
                class="bg-white dark:bg-gray-800 rounded-md inline-flex text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
              >
                <span class="sr-only">Close</span>
                <XMarkIcon class="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup>
  import {
    CheckCircleIcon,
    ExclamationCircleIcon,
    ExclamationTriangleIcon,
    InformationCircleIcon,
    XMarkIcon,
  } from '@heroicons/vue/24/outline'
  import { useNotifications } from '@/composables/useNotifications'

  const { visibleNotifications, dismissNotification } = useNotifications()

  const getNotificationIcon = (type) => {
    const icons = {
      success: CheckCircleIcon,
      error: ExclamationCircleIcon,
      warning: ExclamationTriangleIcon,
      info: InformationCircleIcon,
    }
    return icons[type] || InformationCircleIcon
  }

  const getNotificationClasses = (type) => {
    const classes = {
      success: 'bg-green-50 dark:bg-green-900/20 border-l-4 border-green-400',
      error: 'bg-red-50 dark:bg-red-900/20 border-l-4 border-red-400',
      warning: 'bg-yellow-50 dark:bg-yellow-900/20 border-l-4 border-yellow-400',
      info: 'bg-blue-50 dark:bg-blue-900/20 border-l-4 border-blue-400',
    }
    return classes[type] || classes.info
  }

  const getIconClasses = (type) => {
    const classes = {
      success: 'text-green-400',
      error: 'text-red-400',
      warning: 'text-yellow-400',
      info: 'text-blue-400',
    }
    return classes[type] || classes.info
  }
</script>

<style scoped>
  .toast-enter-active {
    transition: all 0.3s ease-out;
  }

  .toast-leave-active {
    transition: all 0.3s ease-in;
  }

  .toast-enter-from {
    transform: translateX(100%);
    opacity: 0;
  }

  .toast-leave-to {
    transform: translateX(100%);
    opacity: 0;
  }
</style>
