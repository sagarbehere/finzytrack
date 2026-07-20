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
        <!-- `:key` on the ledger file: switching ledgers recreates the KeepAlive
             with an empty cache, so the kept-alive views (Assistant, Import) reset
             fresh instead of showing state from the previous ledger. -->
        <router-view v-slot="{ Component }">
          <KeepAlive :key="activeLedger" :include="['AssistantView', 'ImportView']">
            <component :is="Component" />
          </KeepAlive>
        </router-view>
      </AppShell>

      <!-- Non-blocking notice: new/updated bundled demo content is available.
           Only inside the loaded app (never over the setup wizard or the gate). -->
      <SeedContentNotice v-if="$route.meta.layout !== 'none'" />
    </template>

    <!-- Add toast notifications component -->
    <ToastNotifications />
  </div>
</template>

<script setup>
  import { ref, computed } from 'vue'
  import { useRouter } from 'vue-router'
  import AppShell from './components/layout/AppShell.vue'
  import ToastNotifications from './components/common/ToastNotifications.vue'
  import StartupGate from './components/common/StartupGate.vue'
  import SeedContentNotice from './components/common/SeedContentNotice.vue'
  import { useStartupTasks } from './composables/useStartupTasks'
  import { useConfig } from './composables/useConfig'

  // Identity of the active ledger; keys the KeepAlive so a ledger switch drops the
  // cached Assistant/Import views (their state is component-local) and reloads fresh.
  const { config } = useConfig()
  const activeLedger = computed(() => config.value?.ledger_file ?? 'no-ledger')

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
