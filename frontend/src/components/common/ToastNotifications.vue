<template>
  <div
    class="fixed top-4 right-4 left-4 sm:left-auto z-50 space-y-2 w-full sm:w-auto max-w-md sm:max-w-lg md:max-w-xl"
    style="min-width: 320px"
  >
    <TransitionGroup name="toast" tag="div" class="space-y-2">
      <div
        v-for="notification in toastNotifications"
        :key="notification.id"
        :class="[
          'pointer-events-auto rounded-lg bg-white shadow-lg outline-1 outline-black/5 overflow-hidden cursor-pointer dark:bg-gray-800 dark:-outline-offset-1 dark:outline-white/10',
          getNotificationClasses(notification.type),
        ]"
        @click="handleToastDismiss(notification.id)"
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
                @click.stop="clearNotification(notification.id)"
                class="inline-flex rounded-md text-gray-400 hover:text-gray-500 focus:outline-2 focus:outline-offset-2 focus:outline-indigo-600 dark:hover:text-white dark:focus:outline-indigo-500"
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
  import { ref, computed, watch } from 'vue'
  import {
    CheckCircleIcon,
    ExclamationCircleIcon,
    ExclamationTriangleIcon,
    InformationCircleIcon,
    XMarkIcon,
  } from '@heroicons/vue/24/outline'
  import { useNotifications } from '@/composables/useNotifications'

  const { allNotifications, markAsRead, clearNotification } = useNotifications()

  // Local list of notification IDs currently visible as toasts
  const visibleToastIds = ref([])

  // Watch for new notifications and show them as toasts
  watch(
    () => allNotifications.value.length,
    (newLen, oldLen) => {
      if (newLen > oldLen) {
        const addedCount = newLen - oldLen
        for (let i = 0; i < addedCount; i++) {
          const n = allNotifications.value[i]
          visibleToastIds.value = [...visibleToastIds.value, n.id]
          if (!n.isPersistent) {
            setTimeout(() => {
              visibleToastIds.value = visibleToastIds.value.filter((id) => id !== n.id)
            }, 5000)
          }
        }
      }
    },
  )

  const toastNotifications = computed(() =>
    allNotifications.value.filter((n) => visibleToastIds.value.includes(n.id)),
  )

  const handleToastDismiss = (id) => {
    markAsRead(id)
    visibleToastIds.value = visibleToastIds.value.filter((tid) => tid !== id)
  }

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
      info: 'bg-indigo-50 dark:bg-indigo-900/20 border-l-4 border-indigo-400',
    }
    return classes[type] || classes.info
  }

  const getIconClasses = (type) => {
    const classes = {
      success: 'text-green-400',
      error: 'text-red-400',
      warning: 'text-yellow-400',
      info: 'text-indigo-400',
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
