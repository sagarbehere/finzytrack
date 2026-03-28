<template>
  <!--
    Finzytrack Application Shell
    Main layout component with sidebar navigation and top bar
  -->
  <div class="h-full">
    <!-- Mobile sidebar -->
    <TransitionRoot as="template" :show="sidebarOpen">
      <Dialog class="relative z-50 lg:hidden" @close="sidebarOpen = false">
        <TransitionChild
          as="template"
          enter="transition-opacity ease-linear duration-300"
          enter-from="opacity-0"
          enter-to="opacity-100"
          leave="transition-opacity ease-linear duration-300"
          leave-from="opacity-100"
          leave-to="opacity-0"
        >
          <div class="fixed inset-0 bg-gray-900/80" />
        </TransitionChild>

        <div class="fixed inset-0 flex">
          <TransitionChild
            as="template"
            enter="transition ease-in-out duration-300 transform"
            enter-from="-translate-x-full"
            enter-to="translate-x-0"
            leave="transition ease-in-out duration-300 transform"
            leave-from="translate-x-0"
            leave-to="-translate-x-full"
          >
            <DialogPanel class="relative mr-16 flex w-full max-w-xs flex-1">
              <TransitionChild
                as="template"
                enter="ease-in-out duration-300"
                enter-from="opacity-0"
                enter-to="opacity-100"
                leave="ease-in-out duration-300"
                leave-from="opacity-100"
                leave-to="opacity-0"
              >
                <div class="absolute top-0 left-full flex w-16 justify-center pt-5">
                  <button type="button" class="-m-2.5 p-2.5" @click="sidebarOpen = false">
                    <span class="sr-only">Close sidebar</span>
                    <XMarkIcon class="size-6 text-white" aria-hidden="true" />
                  </button>
                </div>
              </TransitionChild>

              <!-- Mobile sidebar content -->
              <div
                class="flex grow flex-col gap-y-5 overflow-y-auto bg-white px-6 pb-4 dark:bg-gray-900"
              >
                <div class="flex h-16 shrink-0 items-center">
                  <h1 class="text-xl font-bold text-gray-900 dark:text-white">Finzytrack</h1>
                </div>
                <nav class="flex flex-1 flex-col">
                  <ul role="list" class="flex flex-1 flex-col gap-y-7">
                    <li>
                      <ul role="list" class="-mx-2 space-y-1">
                        <li v-for="item in mainNavigation" :key="item.name">
                          <router-link
                            :to="item.href"
                            :class="[
                              $route.name === item.id
                                ? 'bg-gray-50 text-indigo-600 dark:bg-white/5 dark:text-white'
                                : 'text-gray-700 hover:bg-gray-50 hover:text-indigo-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white',
                              'group flex gap-x-3 rounded-md p-2 text-sm font-semibold',
                            ]"
                          >
                            <component
                              :is="item.icon"
                              :class="[
                                $route.name === item.id
                                  ? 'text-indigo-600 dark:text-white'
                                  : 'text-gray-400 group-hover:text-indigo-600 dark:group-hover:text-white',
                                'size-6 shrink-0',
                              ]"
                              aria-hidden="true"
                            />
                            {{ item.name }}
                          </router-link>
                        </li>
                      </ul>
                    </li>
                    <li class="mt-auto">
                      <router-link
                        to="/settings"
                        :class="[
                          $route.name === 'settings'
                            ? 'bg-gray-50 text-indigo-600 dark:bg-white/5 dark:text-white'
                            : 'text-gray-700 hover:bg-gray-50 hover:text-indigo-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white',
                          'group -mx-2 flex gap-x-3 rounded-md p-2 text-sm font-semibold',
                        ]"
                      >
                        <Cog6ToothIcon
                          :class="[
                            $route.name === 'settings'
                              ? 'text-indigo-600 dark:text-white'
                              : 'text-gray-400 group-hover:text-indigo-600 dark:group-hover:text-white',
                            'size-6 shrink-0',
                          ]"
                          aria-hidden="true"
                        />
                        Settings
                      </router-link>
                    </li>
                  </ul>
                </nav>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>

    <!-- Desktop sidebar -->
    <div
      class="hidden lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:flex-col group"
      :style="{ width: sidebarWidthPx }"
    >
      <div
        class="flex grow flex-col gap-y-5 overflow-y-auto border-r border-gray-200 bg-white px-6 pb-4 dark:border-white/10 dark:bg-gray-900 relative"
      >
        <!-- Resize handle -->
        <div
          class="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-indigo-500 hover:w-1 transition-all duration-150 z-10 group-hover:bg-gray-300 dark:group-hover:bg-gray-600"
          @mousedown="startResize"
          title="Drag to resize sidebar"
        ></div>
        <div class="flex h-16 shrink-0 items-center">
          <h1 class="text-xl font-bold text-gray-900 dark:text-white">Finzytrack</h1>
        </div>
        <nav class="flex flex-1 flex-col">
          <ul role="list" class="flex flex-1 flex-col gap-y-7">
            <li>
              <ul role="list" class="-mx-2 space-y-1">
                <li v-for="item in mainNavigation" :key="item.name">
                  <router-link
                    :to="item.href"
                    :class="[
                      $route.name === item.id
                        ? 'bg-gray-50 text-indigo-600 dark:bg-white/5 dark:text-white'
                        : 'text-gray-700 hover:bg-gray-50 hover:text-indigo-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white',
                      'group flex gap-x-3 rounded-md p-2 text-sm font-semibold',
                    ]"
                  >
                    <component
                      :is="item.icon"
                      :class="[
                        $route.name === item.id
                          ? 'text-indigo-600 dark:text-white'
                          : 'text-gray-400 group-hover:text-indigo-600 dark:group-hover:text-white',
                        'size-6 shrink-0',
                      ]"
                      aria-hidden="true"
                    />
                    {{ item.name }}
                  </router-link>
                </li>
              </ul>
            </li>
            <!-- Settings at bottom, sticky -->
            <li class="mt-auto">
              <router-link
                to="/settings"
                :class="[
                  $route.name === 'settings'
                    ? 'bg-gray-50 text-indigo-600 dark:bg-white/5 dark:text-white'
                    : 'text-gray-700 hover:bg-gray-50 hover:text-indigo-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white',
                  'group -mx-2 flex gap-x-3 rounded-md p-2 text-sm font-semibold',
                ]"
              >
                <Cog6ToothIcon
                  :class="[
                    $route.name === 'settings'
                      ? 'text-indigo-600 dark:text-white'
                      : 'text-gray-400 group-hover:text-indigo-600 dark:group-hover:text-white',
                    'size-6 shrink-0',
                  ]"
                  aria-hidden="true"
                />
                Settings
              </router-link>
            </li>
          </ul>
        </nav>
      </div>
    </div>

    <!-- Main content area -->
    <div
      class="bg-white dark:bg-gray-900 flex flex-col h-screen"
      :style="{ paddingLeft: isLargeScreen ? `${sidebarWidth}px` : '0px' }"
    >
      <!-- Top bar -->
      <div
        class="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-x-4 border-b border-gray-200 bg-white px-4 shadow-sm sm:gap-x-6 sm:px-6 lg:px-8 dark:border-white/10 dark:bg-gray-900"
      >
        <!-- Mobile menu button -->
        <button
          type="button"
          class="-m-2.5 p-2.5 text-gray-700 hover:text-gray-900 lg:hidden dark:text-gray-400 dark:hover:text-white"
          @click="sidebarOpen = true"
        >
          <span class="sr-only">Open sidebar</span>
          <Bars3Icon class="size-6" aria-hidden="true" />
        </button>

        <!-- Separator for mobile -->
        <div class="h-6 w-px bg-gray-200 lg:hidden dark:bg-white/10" aria-hidden="true" />

        <!-- Top bar content -->
        <div class="flex flex-1 gap-x-4 self-stretch lg:gap-x-6">
          <!-- Search bar -->
          <GlobalSearch />

          <!-- Right side actions -->
          <div class="flex items-center gap-x-4 lg:gap-x-6">
            <!-- Notifications -->
            <div class="relative" ref="notificationRef">
              <button
                type="button"
                class="-m-2.5 p-2.5 text-gray-400 hover:text-gray-500 dark:hover:text-white relative"
                title="View notifications"
                @click="toggleNotificationPanel"
              >
                <span class="sr-only">View notifications</span>
                <BellIcon class="size-6" aria-hidden="true" />
                <!-- Add notification badge -->
                <span
                  v-if="unreadCount > 0"
                  class="absolute -top-1 -right-1 h-5 w-5 rounded-full bg-red-500 flex items-center justify-center text-xs font-medium text-white"
                >
                  {{ unreadCount > 99 ? '99+' : unreadCount }}
                </span>
              </button>

              <!-- Add notification dropdown panel -->
              <NotificationPanel
                v-if="showNotificationPanel"
                @close="showNotificationPanel = false"
              />
            </div>

            <!-- Separator -->
            <div
              class="hidden lg:block lg:h-6 lg:w-px lg:bg-gray-200 dark:lg:bg-white/10"
              aria-hidden="true"
            />

            <!-- Theme toggle -->
            <button
              type="button"
              class="-m-2.5 p-2.5 text-gray-400 hover:text-gray-500 dark:hover:text-white transition-colors"
              :title="themeLabel"
              @click="toggleTheme"
            >
              <span class="sr-only">{{ themeLabel }}</span>
              <SunIcon v-if="themeIcon === 'sun'" class="size-6" aria-hidden="true" />
              <MoonIcon v-else-if="themeIcon === 'moon'" class="size-6" aria-hidden="true" />
              <ComputerDesktopIcon v-else class="size-6" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <!-- Main content -->
      <main class="flex-1 min-h-0 overflow-auto py-8 min-w-0">
        <!-- Ledger error banner -->
        <div
          v-if="ledgerErrorCount > 0 && !ledgerDismissed"
          class="mx-4 sm:mx-6 lg:mx-8 mb-4 rounded-md bg-red-50 dark:bg-red-500/10 p-4 dark:outline dark:outline-red-500/15"
        >
          <div class="flex items-start gap-3">
            <ExclamationTriangleIcon class="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400" aria-hidden="true" />
            <div class="flex-1 min-w-0">
              <p class="text-sm font-medium text-red-800 dark:text-red-200">
                Your ledger has {{ ledgerErrorCount }} parsing {{ ledgerErrorCount === 1 ? 'error' : 'errors' }} that may affect app functionality.
              </p>
              <details class="mt-2">
                <summary class="text-sm text-red-700 dark:text-red-300 cursor-pointer hover:text-red-900 dark:hover:text-red-100">Show details</summary>
                <ul class="mt-2 space-y-1 text-sm text-red-700 dark:text-red-300 font-mono">
                  <li v-for="(err, i) in ledgerErrors" :key="i">
                    Line {{ err.line }}: {{ err.message }}
                  </li>
                </ul>
              </details>
            </div>
            <button
              @click="dismissLedgerErrors"
              class="shrink-0 rounded-md p-1 text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-200"
              aria-label="Dismiss"
            >
              <XMarkIcon class="h-4 w-4" />
            </button>
          </div>
        </div>

        <div class="px-4 sm:px-6 lg:px-8 min-w-0 h-full">
          <slot />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
  import { ref, onMounted, onUnmounted } from 'vue'
  import { useTheme } from '@/composables/useTheme'
  import { useNotifications } from '@/composables/useNotifications'
  import { useSidebarWidth } from '@/composables/useSidebarWidth'
  import { useLedgerHealth } from '@/composables/useLedgerHealth'
  import NotificationPanel from '@/components/common/NotificationPanel.vue'
  import GlobalSearch from '@/components/layout/GlobalSearch.vue'
  import { Dialog, DialogPanel, TransitionChild, TransitionRoot } from '@headlessui/vue'
  import {
    Bars3Icon,
    BellIcon,
    ChartBarIcon,
    Cog6ToothIcon,
    ComputerDesktopIcon,
    ExclamationTriangleIcon,
    HomeIcon,
    ArrowUpTrayIcon,
    TableCellsIcon,
    XMarkIcon,
    SunIcon,
    MoonIcon,
    WalletIcon,
    SparklesIcon,
  } from '@heroicons/vue/24/outline'

  // Props

  // Reactive state
  const sidebarOpen = ref(false)

  // Theme system
  const { themeIcon, themeLabel, toggleTheme } = useTheme()

  // Add notification system
  const { unreadCount } = useNotifications()
  const showNotificationPanel = ref(false)
  const notificationRef = ref(null)

  const toggleNotificationPanel = () => {
    showNotificationPanel.value = !showNotificationPanel.value
  }

  const handleClickOutside = (e) => {
    if (showNotificationPanel.value && notificationRef.value && !notificationRef.value.contains(e.target)) {
      showNotificationPanel.value = false
    }
  }

  const handleEscape = (e) => {
    if (e.key === 'Escape' && showNotificationPanel.value) {
      showNotificationPanel.value = false
    }
  }

  // Ledger health
  const {
    errorCount: ledgerErrorCount,
    errors: ledgerErrors,
    dismissed: ledgerDismissed,
    checkErrors: checkLedgerErrors,
    dismiss: dismissLedgerErrors,
  } = useLedgerHealth()

  // Sidebar resizing
  const { sidebarWidth, sidebarWidthPx, setSidebarWidth } = useSidebarWidth()
  const isResizing = ref(false)
  const isLargeScreen = ref(window.innerWidth >= 1024)

  const startResize = (e) => {
    isResizing.value = true
    document.addEventListener('mousemove', handleSidebarResize)
    document.addEventListener('mouseup', stopResize)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    e.preventDefault()
  }

  const handleSidebarResize = (e) => {
    if (!isResizing.value) return

    const newWidth = e.clientX
    setSidebarWidth(newWidth)
  }

  const stopResize = () => {
    isResizing.value = false
    document.removeEventListener('mousemove', handleSidebarResize)
    document.removeEventListener('mouseup', stopResize)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  // Handle responsive screen size changes
  const handleWindowResize = () => {
    isLargeScreen.value = window.innerWidth >= 1024
  }

  onMounted(() => {
    window.addEventListener('resize', handleWindowResize)
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    checkLedgerErrors()
  })

  // Cleanup on component unmount
  onUnmounted(() => {
    if (isResizing.value) {
      stopResize()
    }
    window.removeEventListener('resize', handleWindowResize)
    document.removeEventListener('mousedown', handleClickOutside)
    document.removeEventListener('keydown', handleEscape)
  })

  // Navigation configuration based on Finzytrack spec
  const mainNavigation = [
    {
      name: 'Dashboard',
      href: '/dashboard',
      icon: HomeIcon,
      id: 'dashboard',
    },
    {
      name: 'Import',
      href: '/import',
      icon: ArrowUpTrayIcon,
      id: 'import',
    },
    {
      name: 'Analyze',
      href: '/analyze',
      icon: ChartBarIcon,
      id: 'analyze',
    },
    {
      name: 'Transactions',
      href: '/transactions',
      icon: TableCellsIcon,
      id: 'transactions',
    },
    {
      name: 'Accounts',
      href: '/accounts',
      icon: WalletIcon,
      id: 'accounts',
    },
    {
      name: 'AI Assistant',
      href: '/assistant',
      icon: SparklesIcon,
      id: 'assistant',
    },
  ]

  // No methods needed - router handles navigation state
</script>
