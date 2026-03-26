<template>
  <div
    class="absolute right-0 top-12 w-96 rounded-md bg-white shadow-lg outline-1 outline-black/5 z-50 dark:bg-gray-800 dark:-outline-offset-1 dark:outline-white/10"
  >
    <div class="p-4 border-b border-gray-200 dark:border-white/10">
      <div class="flex items-center justify-between">
        <h3 class="text-lg font-medium text-gray-900 dark:text-white">Notifications</h3>
        <div class="flex space-x-2">
          <button
            @click="markAllAsRead"
            class="text-sm text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
          >
            Mark all read
          </button>
          <button @click="clearAllNotifications" class="text-sm text-gray-500 hover:text-gray-400">
            Clear all
          </button>
          <button @click="$emit('close')" class="text-gray-400 hover:text-gray-500">
            <XMarkIcon class="h-5 w-5" />
          </button>
        </div>
      </div>
    </div>

    <div class="max-h-96 overflow-y-auto">
      <div v-if="allNotifications.length === 0" class="p-8 text-center">
        <BellIcon class="mx-auto h-12 w-12 text-gray-400" />
        <p class="mt-4 text-sm text-gray-500 dark:text-gray-400">No notifications</p>
      </div>

      <div v-else class="divide-y divide-gray-200 dark:divide-white/10">
        <div
          v-for="notification in allNotifications"
          :key="notification.id"
          @click="markAsRead(notification.id)"
          :class="[
            'p-4 hover:bg-gray-50 dark:hover:bg-white/5 relative cursor-pointer',
            !notification.read ? 'bg-indigo-50 dark:bg-indigo-900/20' : '',
          ]"
        >
          <div class="flex">
            <div class="flex-shrink-0">
              <component
                :is="getNotificationIcon(notification.type)"
                :class="['h-5 w-5', getIconClasses(notification.type)]"
              />
            </div>
            <div class="ml-3 flex-1">
              <div class="flex items-center justify-between">
                <p class="text-sm font-medium text-gray-900 dark:text-white">
                  {{ notification.title }}
                </p>
                <!-- Show expand button for error notifications with details -->
                <button
                  v-if="notification.type === 'error' && (notification.errorCode || notification.errorDetails)"
                  @click="toggleErrorDetails(notification.id)"
                  class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                >
                  <ChevronDownIcon v-if="isExpanded(notification.id)" class="h-4 w-4" />
                  <ChevronRightIcon v-else class="h-4 w-4" />
                </button>
              </div>
              <p class="text-sm text-gray-500 dark:text-gray-400">
                {{ notification.message }}
              </p>
              
              <!-- Expandable error details -->
              <div 
                v-if="notification.type === 'error' && isExpanded(notification.id) && (notification.errorCode || notification.errorDetails)"
                class="mt-2 p-2 bg-gray-100 dark:bg-white/5 rounded text-xs"
              >
                <div v-if="notification.errorCode" class="mb-1">
                  <span class="font-medium text-gray-700 dark:text-gray-300">Error Code:</span>
                  <span class="text-gray-600 dark:text-gray-400 ml-1">{{ notification.errorCode }}</span>
                </div>
                <div v-if="notification.errorDetails">
                  <span class="font-medium text-gray-700 dark:text-gray-300">Details:</span>
                  <pre class="text-gray-600 dark:text-gray-400 mt-1 whitespace-pre-wrap">{{ JSON.stringify(notification.errorDetails, null, 2) }}</pre>
                </div>
              </div>
              
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-1">
                {{ formatTime(notification.timestamp) }}
              </p>
            </div>
            <div class="flex-shrink-0">
              <button
                @click.stop="clearNotification(notification.id)"
                class="text-gray-400 hover:text-gray-500"
              >
                <XMarkIcon class="h-4 w-4" />
              </button>
            </div>
          </div>

          <!-- Unread indicator -->
          <div
            v-if="!notification.read"
            class="absolute right-2 top-2 w-2 h-2 bg-indigo-500 rounded-full"
          ></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
  import { ref } from 'vue'
  import {
    CheckCircleIcon,
    ExclamationCircleIcon,
    ExclamationTriangleIcon,
    InformationCircleIcon,
    XMarkIcon,
    BellIcon,
    ChevronDownIcon,
    ChevronRightIcon,
  } from '@heroicons/vue/24/outline'
  import { useNotifications } from '@/composables/useNotifications'

  defineEmits(['close'])

  const { allNotifications, clearNotification, markAsRead, markAllAsRead, clearAllNotifications } =
    useNotifications()

  // Track expanded error details
  const expandedNotifications = ref(new Set())

  const toggleErrorDetails = (notificationId) => {
    if (expandedNotifications.value.has(notificationId)) {
      expandedNotifications.value.delete(notificationId)
    } else {
      expandedNotifications.value.add(notificationId)
    }
  }

  const isExpanded = (notificationId) => {
    return expandedNotifications.value.has(notificationId)
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

  const getIconClasses = (type) => {
    const classes = {
      success: 'text-green-400',
      error: 'text-red-400',
      warning: 'text-yellow-400',
      info: 'text-indigo-400',
    }
    return classes[type] || classes.info
  }

  const formatTime = (timestamp) => {
    return new Date(timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    })
  }
</script>