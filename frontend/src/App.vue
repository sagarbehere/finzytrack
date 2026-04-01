<template>
  <div id="app" class="h-full bg-white dark:bg-gray-900">
    <!-- Wait for initial navigation to resolve before rendering.
         This prevents AppShell from briefly mounting (and making API calls)
         before the router guard redirects to /setup on first run. -->
    <template v-if="routerReady">
      <!-- Full-screen views (setup wizard) bypass AppShell -->
      <router-view v-if="$route.meta.layout === 'none'" />

      <!-- Normal views get the AppShell wrapper -->
      <AppShell v-else>
        <router-view v-slot="{ Component }">
          <KeepAlive :include="['AssistantView', 'ImportView']">
            <component :is="Component" />
          </KeepAlive>
        </router-view>
      </AppShell>
    </template>

    <!-- Add toast notifications component -->
    <ToastNotifications />
  </div>
</template>

<script setup>
  import { ref } from 'vue'
  import { useRouter } from 'vue-router'
  import AppShell from './components/layout/AppShell.vue'
  import ToastNotifications from './components/common/ToastNotifications.vue'

  const router = useRouter()
  const routerReady = ref(false)
  router.isReady().then(() => { routerReady.value = true })
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
