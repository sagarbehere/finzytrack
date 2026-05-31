import { createRouter, createWebHistory, type RouteRecordRaw, type RouteLocationNormalized, type NavigationGuardNext } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import ImportView from '../views/ImportView.vue'
import AnalyzeView from '../views/AnalyzeView.vue'
import TransactionsView from '../views/TransactionsView.vue'
import SettingsView from '../views/SettingsView.vue'
import AssistantView from '../views/AssistantView.vue'
import SetupWizardView from '../views/SetupWizardView.vue'
import { useConfig } from '../composables/useConfig'

const routes: RouteRecordRaw[] = [
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

// Per-route scroll position of the AppShell <main> element. The app's actual
// scroll container is <main>, not the window, so Vue Router's built-in
// savedPosition (which records window scroll) is irrelevant. We maintain our
// own map and restore from it on popstate (browser back/forward); for any
// other navigation we reset main to the top.
const mainScrollPositions = new Map<string, number>()

function getMainScrollEl(): HTMLElement | null {
  // AppShell renders exactly one <main>. Setup-wizard routes bypass AppShell,
  // but in that case there's no AppShell <main> either, so a null is safe.
  return document.querySelector('main')
}

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, _from, savedPosition) {
    return new Promise((resolve) => {
      // Wait one frame so the destination component has mounted and its
      // content has been laid out before we set scrollTop.
      requestAnimationFrame(() => {
        const main = getMainScrollEl()
        if (main) {
          if (savedPosition) {
            main.scrollTop = mainScrollPositions.get(to.fullPath) ?? 0
          } else if (!to.hash) {
            // When navigating to a hash target (e.g. /settings#ai-settings),
            // don't reset scroll to top — the destination view scrolls the
            // anchor into view via its own onMounted/watcher. Resetting here
            // would race that logic and win, leaving the user at the top.
            main.scrollTop = 0
          }
        }
        // Returning false tells the router not to also scroll the window.
        resolve(false)
      })
    })
  },
})

// Capture the leaving route's main scroll so popstate can restore it.
router.beforeEach((_to, from) => {
  const main = getMainScrollEl()
  if (main && from.fullPath) {
    mainScrollPositions.set(from.fullPath, main.scrollTop)
  }
})

// Redirect to setup wizard on first run
let configLoaded = false

router.beforeEach(async (to: RouteLocationNormalized, _from: RouteLocationNormalized, next: NavigationGuardNext) => {
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
