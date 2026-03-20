import { ref, computed } from 'vue'

// Define types for notifications
export interface Notification {
  id: number
  type: 'info' | 'success' | 'warning' | 'error'
  title: string
  message: string
  timestamp: Date
  read: boolean
  // Enhanced error details for debugging
  errorCode?: string | null
  errorDetails?: unknown | null
  isPersistent?: boolean // Errors from Tier 2/3 are persistent
}

export interface NotificationInput {
  type?: 'info' | 'success' | 'warning' | 'error'
  title: string
  message: string
  errorCode?: string
  errorDetails?: unknown
  isPersistent?: boolean
}

// Global notification state
const notifications = ref<Notification[]>([])
const notificationIdCounter = ref(0)

export function useNotifications() {
  const addNotification = (notification: NotificationInput): number => {
    const id = ++notificationIdCounter.value
    const newNotification: Notification = {
      id,
      type: notification.type || 'info',
      title: notification.title,
      message: notification.message,
      timestamp: new Date(),
      read: false,
      // Enhanced error details for debugging
      errorCode: notification.errorCode || null,
      errorDetails: notification.errorDetails || null,
      isPersistent: notification.isPersistent || false,
    }

    notifications.value.unshift(newNotification) // Add to beginning

    return id
  }

  const clearNotification = (id: number): void => {
    const index = notifications.value.findIndex((n) => n.id === id)
    if (index !== -1) {
      notifications.value.splice(index, 1)
    }
  }

  const markAsRead = (id: number): void => {
    const notification = notifications.value.find((n) => n.id === id)
    if (notification) {
      notification.read = true
    }
  }

  const markAllAsRead = (): void => {
    notifications.value.forEach((n) => (n.read = true))
  }

  const clearAllNotifications = (): void => {
    notifications.value.splice(0)
  }

  const clearOldNotifications = (): void => {
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000)
    notifications.value = notifications.value.filter((n) => n.timestamp > oneHourAgo)
  }

  // Computed properties
  const allNotifications = computed(() => notifications.value)
  const unreadCount = computed(() => notifications.value.filter((n) => !n.read).length)

  // Auto-cleanup every hour
  setInterval(clearOldNotifications, 60 * 60 * 1000)

  return {
    // State
    allNotifications,
    unreadCount,

    // Actions
    addNotification,
    clearNotification,
    markAsRead,
    markAllAsRead,
    clearAllNotifications,
  }
}

// Convenience functions for different notification types
export function useToast() {
  const { addNotification } = useNotifications()

  return {
    success: (title: string, message: string) => addNotification({
      type: 'success',
      title,
      message,
    }),
    error: (title: string, message: string) => addNotification({
      type: 'error',
      title,
      message,
    }),
    warning: (title: string, message: string) => addNotification({
      type: 'warning',
      title,
      message,
    }),
    info: (title: string, message: string) => addNotification({
      type: 'info',
      title,
      message,
    }),
  }
}
