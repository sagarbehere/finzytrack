<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">Application preferences and configuration</p>
    </div>

    <!-- Restart required banner -->
    <div
      v-if="restartRequired"
      class="mb-6 flex items-start gap-3 rounded-md bg-yellow-50 p-4 text-sm dark:bg-yellow-500/10 dark:outline dark:outline-yellow-500/15"
    >
      <svg class="mt-0.5 h-4 w-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
      </svg>
      <span>Some changes require an app restart to take effect. Please restart the application.</span>
    </div>

    <!-- Tab bar -->
    <div class="mb-6 border-b border-gray-200 dark:border-white/10">
      <nav class="-mb-px flex space-x-4 sm:space-x-8 overflow-x-auto">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="switchTab(tab.id)"
          :class="[
            activeTab === tab.id
              ? 'border-indigo-500 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
              : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
            'border-b-2 px-1 py-4 text-sm font-medium whitespace-nowrap'
          ]"
        >
          {{ tab.label }}
        </button>
      </nav>
    </div>

    <!-- Tab content -->
    <GeneralSettingsTab v-if="activeTab === 'general'" @restart-required="restartRequired = true" />
    <RulesTab v-else-if="activeTab === 'rules'" :initial-rule-type="initialRuleType" />
    <DashboardsTab v-else-if="activeTab === 'dashboards'" :initial-recipe-type="initialRuleType" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GeneralSettingsTab from '@/components/settings/GeneralSettingsTab.vue'
import RulesTab from '@/components/settings/RulesTab.vue'
import DashboardsTab from '@/components/settings/DashboardsTab.vue'

const route = useRoute()
const router = useRouter()

const tabs = [
  { id: 'general', label: 'General' },
  { id: 'rules', label: 'Import Rules' },
  { id: 'dashboards', label: 'Dashboards' },
] as const

type TabId = typeof tabs[number]['id']

const restartRequired = ref(false)

const activeTab = ref<TabId>(
  (['rules', 'dashboards'] as const).includes(route.query.tab as any)
    ? (route.query.tab as TabId)
    : 'general'
)

const initialRuleType = computed(() => route.query.type as string | undefined)

function switchTab(tabId: TabId) {
  activeTab.value = tabId
  router.replace({ query: tabId === 'general' ? {} : { tab: tabId, ...(route.query.type ? { type: route.query.type } : {}) } })
}

// Sync when navigating via deep link
watch(() => route.query.tab, (newTab) => {
  if (newTab === 'rules' || newTab === 'general' || newTab === 'dashboards') {
    activeTab.value = newTab
  } else if (!newTab) {
    activeTab.value = 'general'
  }
})
</script>
