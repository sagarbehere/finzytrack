import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import ImportView from '../views/ImportView.vue'
import AnalyzeView from '../views/AnalyzeView.vue'
import TransactionsView from '../views/TransactionsView.vue'
import RawDataView from '../views/RawDataView.vue'
import SettingsView from '../views/SettingsView.vue'
import AssistantView from '../views/AssistantView.vue'

const routes = [
  {
    path: '/',
    redirect: '/dashboard',
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
      title: 'Analyze',
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
    path: '/raw-data',
    name: 'raw-data',
    component: RawDataView,
    meta: {
      title: 'Raw Data',
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

// Update document title based on route
router.beforeEach((to, from, next) => {
  document.title = to.meta.title ? `${to.meta.title} - Finzytrack` : 'Finzytrack'
  next()
})

export default router
