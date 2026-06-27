<template>
  <div id="app" class="h-full bg-white dark:bg-gray-900">
    <!-- Startup gate: a required upgrade (e.g. the dashboard-format migration)
         blocks the whole app behind a consent dialog until the user applies it.
         Detection is read-only; nothing on disk changes until they consent. -->
    <StartupGate
      v-if="startupChecked && gateTask"
      :task="gateTask"
      @applied="onStartupApplied"
    />

    <!-- Wait for the startup check AND initial navigation before rendering the
         app. This also prevents AppShell from mounting (and loading recipes)
         before a pending upgrade is resolved. -->
    <template v-if="startupChecked && !gateTask && routerReady">
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
  import StartupGate from './components/common/StartupGate.vue'
  import { useStartupTasks } from './composables/useStartupTasks'

  const router = useRouter()
  const routerReady = ref(false)
  router.isReady().then(() => { routerReady.value = true })

  // Check for pending upgrades before the app renders (read-only).
  const { checked: startupChecked, gateTask, checkStartupTasks } = useStartupTasks()
  checkStartupTasks()

  function onStartupApplied() {
    // After a successful apply the task list is re-detected; the gate clears and
    // the app proceeds to load (recipes are now at the current format).
  }
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
