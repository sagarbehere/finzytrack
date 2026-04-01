import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import ImportView from '../views/ImportView.vue'
import AnalyzeView from '../views/AnalyzeView.vue'
import TransactionsView from '../views/TransactionsView.vue'
import SettingsView from '../views/SettingsView.vue'
import AssistantView from '../views/AssistantView.vue'
import SetupWizardView from '../views/SetupWizardView.vue'
import { useConfig } from '../composables/useConfig'

const routes = [
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/setup',
    name: 'setup',
    component: SetupWizardView,
    meta: {
      title: 'Setup',
      layout: 'none',  // Bypass AppShell
    },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: DashboardView,
    meta: {
      title: 'Dashboard',
    },
  },
  {
    path: '/import',
    name: 'import',
    component: ImportView,
    meta: {
      title: 'Import',
    },
  },
  {
    path: '/analyze',
    name: 'analyze',
    component: AnalyzeView,
    meta: {
      title: 'Query',
    },
  },
  {
    path: '/transactions',
    name: 'transactions',
    component: TransactionsView,
    meta: {
      title: 'Transactions',
    },
  },
  {
    path: '/accounts',
    name: 'accounts',
    component: () => import('../views/AccountsView.vue'),
    meta: {
      title: 'Accounts',
    },
  },
  {
    path: '/settings',
    name: 'settings',
    component: SettingsView,
    meta: {
      title: 'Settings',
    },
  },
  {
    path: '/assistant',
    name: 'assistant',
    component: AssistantView,
    meta: {
      title: 'AI Assistant',
    },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Redirect to setup wizard on first run
let configLoaded = false

router.beforeEach(async (to, from, next) => {
  document.title = to.meta.title ? `${to.meta.title} - Finzytrack` : 'Finzytrack'

  // Ensure config is loaded (once per session)
  const { loadConfig, config } = useConfig()
  if (!configLoaded) {
    configLoaded = true
    await loadConfig()
  }

  const setupComplete = config.value?.setup_complete ?? false

  // Redirect to setup if not complete (unless already going there)
  if (!setupComplete && to.name !== 'setup') {
    next({ name: 'setup' })
  } else if (setupComplete && to.name === 'setup') {
    // Don't allow revisiting setup after completion
    next({ name: 'dashboard' })
  } else {
    next()
  }
})

export default router
