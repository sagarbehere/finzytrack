<template>
  <div id="app" class="h-full bg-white dark:bg-gray-900">
    <AppShell>
      <!-- Dynamic content based on current page -->
      <router-view v-if="$router" v-slot="{ Component }">
        <KeepAlive include="AssistantView">
          <component :is="Component" />
        </KeepAlive>
      </router-view>
      <div v-else>
        <!-- Fallback content when router is not available -->
        <div class="text-center py-12">
          <h1 class="text-2xl font-bold text-gray-900 dark:text-white mb-4">
            Welcome to Finzytrack
          </h1>
          <p class="text-gray-600 dark:text-gray-400">
            Your privacy-focused personal finance application
          </p>
        </div>
      </div>
    </AppShell>

    <!-- Add toast notifications component -->
    <ToastNotifications />
  </div>
</template>

<script setup>
  import AppShell from './components/layout/AppShell.vue'
  import ToastNotifications from './components/common/ToastNotifications.vue'
</script>

<style>
  /* Global styles */
  html,
  body {
    height: 100%;
    margin: 0;
    padding: 0;
  }

  #app {
    height: 100%;
  }

  /* Ensure proper dark mode support */
  :root {
    color-scheme: light;
  }

  .dark {
    color-scheme: dark;
  }
</style>
